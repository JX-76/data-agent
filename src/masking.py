"""Data masking: redacts PII and sensitive fields from query results.

Protects:
- user_id → USR****
- email → masked
- phone → masked
- IP addresses → truncated

Configurable per-column mask rules. Applied at the output boundary
so internal execution sees full data but API consumers don't.

Usage:
    from masking import DataMasker
    masker = DataMasker()
    safe_results = masker.mask_rows(results, columns)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class MaskRule:
    """Rule for masking a column."""
    pattern: str          # Column name pattern (e.g., "user_id", "*email*")
    strategy: str         # "stars" | "hash" | "email" | "phone" | "ip"
    keep_chars: int = 3   # Prefix chars to keep (for stars strategy)


# ── Built-in rules ──

DEFAULT_RULES = [
    # PII
    MaskRule(pattern="user_id", strategy="stars", keep_chars=3),       # USR0001 → USR****
    MaskRule(pattern="phone", strategy="phone", keep_chars=3),
    MaskRule(pattern="mobile", strategy="phone", keep_chars=3),
    MaskRule(pattern="*email*", strategy="email"),
    MaskRule(pattern="email", strategy="email"),
    MaskRule(pattern="ip_address", strategy="ip"),
    MaskRule(pattern="client_ip", strategy="ip"),
    # Financial (partial)
    MaskRule(pattern="password", strategy="stars", keep_chars=0),
    MaskRule(pattern="secret", strategy="stars", keep_chars=0),
    MaskRule(pattern="token", strategy="stars", keep_chars=4),
    MaskRule(pattern="api_key", strategy="stars", keep_chars=4),
]


class DataMasker:
    """Apply masking rules to query results before returning to API consumers."""

    def __init__(self, extra_rules: list[MaskRule] | None = None):
        self.rules = list(DEFAULT_RULES)
        if extra_rules:
            self.rules.extend(extra_rules)

    def _match_rule(self, column: str) -> Optional[MaskRule]:
        """Find the first matching rule for a column name."""
        col_lower = column.lower()
        for rule in self.rules:
            pat = rule.pattern
            if "*" in pat:
                # Glob-style: *email* → .*email.*
                regex = pat.replace("*", ".*")
                if re.match(regex, col_lower):
                    return rule
            elif pat.lower() == col_lower:
                return rule
            elif col_lower.endswith(pat.lower()):
                return rule
        return None

    def mask_value(self, value, rule: MaskRule) -> str:
        """Apply a single mask rule to a value."""
        if value is None:
            return None

        s = str(value)
        strategy = rule.strategy

        if strategy == "stars":
            keep = min(rule.keep_chars, len(s))
            return s[:keep] + "*" * max(1, len(s) - keep)

        elif strategy == "email":
            if "@" in s:
                local, domain = s.split("@", 1)
                if len(local) <= 2:
                    masked_local = "*" * len(local)
                else:
                    masked_local = local[0] + "*" * (len(local) - 2) + local[-1]
                return f"{masked_local}@{domain}"
            return s[0] + "*" * max(1, len(s) - 1)

        elif strategy == "phone":
            # Keep first 3 and last 2 digits
            if len(s) >= 7:
                return s[:3] + "*" * (len(s) - 5) + s[-2:]
            return s[:3] + "*" * max(1, len(s) - 3)

        elif strategy == "ip":
            # Keep first octet only
            parts = s.split(".")
            if len(parts) == 4:
                return f"{parts[0]}.*.*.*"
            return s[0] + "*" * max(1, len(s) - 1)

        elif strategy == "hash":
            import hashlib
            return hashlib.sha256(s.encode()).hexdigest()[:12]

        return s

    def mask_rows(self, rows: list[dict]) -> list[dict]:
        """Mask all sensitive values in a list of result rows."""
        if not rows:
            return rows

        # Determine which columns need masking
        masked_cols = set()
        for row in rows:
            for col in row:
                if self._match_rule(col):
                    masked_cols.add(col)

        if not masked_cols:
            return rows

        # Apply masking
        safe_rows = []
        for row in rows:
            safe_row = dict(row)
            for col in masked_cols:
                rule = self._match_rule(col)
                if rule and col in safe_row:
                    safe_row[col] = self.mask_value(safe_row[col], rule)
            safe_rows.append(safe_row)

        return safe_rows

    def mask_dict(self, data: dict, columns: Optional[list[str]] = None) -> dict:
        """Mask sensitive values in a dict (e.g., single row from results)."""
        cols_to_check = columns or list(data.keys())
        result = dict(data)
        for col in cols_to_check:
            rule = self._match_rule(col)
            if rule and col in result:
                result[col] = self.mask_value(result[col], rule)
        return result

    @property
    def active_rules(self) -> list[str]:
        return [f"{r.pattern} → {r.strategy}" for r in self.rules]


# ── Global ──

_masker: DataMasker | None = None


def get_masker() -> DataMasker:
    global _masker
    if _masker is None:
        _masker = DataMasker()
    return _masker
