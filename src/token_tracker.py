"""Token cost tracker: monitors all LLM calls and their token consumption.

Tracks:
- Per-call token usage (prompt + completion)
- Per-model cost accumulation
- Daily/hourly cost breakdown
- Budget alerts

Usage:
    from token_tracker import TokenTracker, track_call
    tracker = track_call(model="deepseek-chat", prompt_tokens=150, completion_tokens=50)
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import structlog

logger = structlog.get_logger("token-tracker")

# ── Pricing (USD per 1M tokens) ──
# DeepSeek pricing as of 2026-07
PRICING = {
    "deepseek-chat":       {"input": 0.27, "output": 1.10},
    "deepseek-v4-flash":   {"input": 0.14, "output": 0.55},
    "deepseek-reasoner":   {"input": 0.55, "output": 2.19},
    # Fallback/future
    "gpt-4o":              {"input": 2.50, "output": 10.00},
    "claude-3.5-sonnet":   {"input": 3.00, "output": 15.00},
    "qwen-plus":           {"input": 0.56, "output": 1.12},
}


@dataclass
class CallRecord:
    timestamp: str
    model: str
    operation: str          # route / analyze / generate / fallback
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float
    duration_ms: float


@dataclass
class CostSummary:
    total_calls: int
    total_tokens: int
    total_cost_usd: float
    by_model: dict  # model → {calls, tokens, cost}
    by_operation: dict  # op → {calls, tokens, cost}
    period_hours: float


class TokenTracker:
    """Singleton token usage tracker with disk persistence."""

    def __init__(self, data_path: str = "token_usage.json"):
        self.data_path = Path(data_path)
        self._lock = threading.Lock()
        self._calls: list[CallRecord] = []
        self._daily_buckets: dict[str, list[CallRecord]] = {}
        self._total_cost = 0.0
        self._budget_limit: Optional[float] = None

        # Restore from disk
        self._restore()

    def _restore(self):
        if self.data_path.exists():
            try:
                data = json.loads(self.data_path.read_text())
                self._total_cost = data.get("total_cost", 0.0)
                self._budget_limit = data.get("budget_limit")
                for raw in data.get("calls", []):
                    self._calls.append(CallRecord(**raw))
            except Exception as e:
                logger.warning("bare_exception_caught", error=str(e))
                pass

    def _save(self):
        with self._lock:
            data = {
                "total_cost": self._total_cost,
                "budget_limit": self._budget_limit,
                "calls": [c.__dict__ for c in self._calls[-1000:]],  # Keep last 1000
            }
            self.data_path.write_text(json.dumps(data, ensure_ascii=False, indent=2))

    def set_budget(self, monthly_limit_usd: float):
        """Set a monthly budget for alerts."""
        self._budget_limit = monthly_limit_usd
        self._save()

    def record(
        self,
        model: str,
        operation: str,
        prompt_tokens: int,
        completion_tokens: int,
        duration_ms: float = 0,
    ) -> float:
        """Record a token usage event. Returns cost in USD."""
        pricing = PRICING.get(model, {"input": 1.0, "output": 5.0})
        input_cost = (prompt_tokens / 1_000_000) * pricing["input"]
        output_cost = (completion_tokens / 1_000_000) * pricing["output"]
        cost = input_cost + output_cost

        record = CallRecord(
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
            model=model,
            operation=operation,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            cost_usd=round(cost, 6),
            duration_ms=duration_ms,
        )

        with self._lock:
            self._calls.append(record)
            self._total_cost += cost

            # Daily bucket
            day_key = time.strftime("%Y-%m-%d", time.localtime())
            if day_key not in self._daily_buckets:
                self._daily_buckets[day_key] = []
            self._daily_buckets[day_key].append(record)

        # Budget check (log warning, not block)
        if self._budget_limit and self._total_cost > self._budget_limit * 0.8:
            logger.warning("token_budget_warning",
                          total_cost=round(self._total_cost, 4),
                          budget=self._budget_limit,
                          usage_pct=round(self._total_cost / self._budget_limit * 100, 1))

        self._save()
        return cost

    def summary(self, hours: int = 24) -> CostSummary:
        """Get cost summary for the last N hours."""
        cutoff = time.time() - hours * 3600
        recent = []

        with self._lock:
            for c in self._calls:
                try:
                    ts = time.mktime(time.strptime(c.timestamp, "%Y-%m-%dT%H:%M:%S"))
                    if ts >= cutoff:
                        recent.append(c)
                except Exception as e:
                    logger.warning("bare_exception_caught", error=str(e))
                    recent.append(c)

        by_model = {}
        by_operation = {}
        for c in recent:
            # By model
            if c.model not in by_model:
                by_model[c.model] = {"calls": 0, "tokens": 0, "cost": 0.0}
            by_model[c.model]["calls"] += 1
            by_model[c.model]["tokens"] += c.total_tokens
            by_model[c.model]["cost"] += c.cost_usd

            # By operation
            if c.operation not in by_operation:
                by_operation[c.operation] = {"calls": 0, "tokens": 0, "cost": 0.0}
            by_operation[c.operation]["calls"] += 1
            by_operation[c.operation]["tokens"] += c.total_tokens
            by_operation[c.operation]["cost"] += c.cost_usd

        return CostSummary(
            total_calls=len(recent),
            total_tokens=sum(c.total_tokens for c in recent),
            total_cost_usd=round(sum(c.cost_usd for c in recent), 6),
            by_model=by_model,
            by_operation=by_operation,
            period_hours=hours,
        )

    @property
    def total_cost(self) -> float:
        return self._total_cost

    @property
    def total_calls(self) -> int:
        return len(self._calls)


# ── Global singleton ──

_tracker: TokenTracker | None = None


def get_tracker() -> TokenTracker:
    global _tracker
    if _tracker is None:
        _tracker = TokenTracker()
    return _tracker


def track_call(
    model: str = "deepseek-v4-flash",
    operation: str = "route",
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    duration_ms: float = 0,
) -> float:
    """Convenience: record a token usage event."""
    return get_tracker().record(model, operation, prompt_tokens, completion_tokens, duration_ms)
