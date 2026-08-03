"""Tool output enforcement: schema validation, re-injection on parse failure, regex enforcement.

Three layers of defense for model outputs:
  1. JSON mode (via API) — basic JSON format guarantee
  2. Regex structural enforcement — pattern check before parse attempt
  3. Schema validation — field types, required fields, value ranges
  4. Re-injection retry — on failure, feed error back to model (not just discard)

Also provides idempotency key support for write operations.
"""

from __future__ import annotations

import json
import re
import hashlib
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional, Callable

import structlog

logger = structlog.get_logger("tool_enforcer")


# ══════════════════════════════════════════════════════════════
# Tool Schema Definitions
# ══════════════════════════════════════════════════════════════

TOOL_SCHEMAS: dict[str, dict] = {
    "catalog": {
        "required": [],
        "properties": {},
        "allowExtra": True,
        "description": "List available models, metrics, and dimensions",
    },
    "switch": {
        "required": ["model_id"],
        "properties": {
            "model_id": {"type": str, "values": ["order_detail", "user_summary", "product_analysis"]},
        },
        "allowExtra": False,
    },
    "preview": {
        "required": ["dataid"],
        "properties": {
            "dataid": {"type": str, "pattern": r"^d\d+$"},
            "n": {"type": int, "min": 1, "max": 20},
        },
        "allowExtra": False,
    },
    "filter": {
        "required": ["dataid", "metric_id"],
        "properties": {
            "dataid": {"type": str, "pattern": r"^d\d+$"},
            "metric_id": {"type": str, "values": ["gmv", "order_count", "avg_price", "aov"]},
            "start_iso": {"type": str, "pattern": r"^\d{4}-\d{2}-\d{2}"},
            "end_iso": {"type": str, "pattern": r"^\d{4}-\d{2}-\d{2}"},
            "start": {"type": str},
            "end": {"type": str},
        },
        "allowExtra": False,
    },
    "aggregate": {
        "required": ["dataid", "metric_id", "dimensions"],
        "properties": {
            "dataid": {"type": str, "pattern": r"^d\d+$"},
            "metric_id": {"type": str, "values": ["gmv", "order_count", "avg_price", "aov"]},
            "dimensions": {"type": list, "itemType": str, "allowedValues": ["channel", "region", "category", "date"]},
            "metric": {"type": str},  # alias for metric_id
        },
        "allowExtra": False,
    },
    "sort": {
        "required": ["dataid"],
        "properties": {
            "dataid": {"type": str, "pattern": r"^d\d+$"},
            "by": {"type": str},
            "order": {"type": str, "values": ["ASC", "DESC"]},
            "metric": {"type": str},  # alias for `by`
        },
        "allowExtra": False,
    },
    "top": {
        "required": ["dataid"],
        "properties": {
            "dataid": {"type": str, "pattern": r"^d\d+$"},
            "by": {"type": str},
            "n": {"type": int, "min": 1, "max": 100},
            "order": {"type": str, "values": ["ASC", "DESC"]},
            "metric": {"type": str},  # alias for `by`
        },
        "allowExtra": False,
    },
    "filter_value": {
        "required": ["dataid", "dimension", "value"],
        "properties": {
            "dataid": {"type": str, "pattern": r"^d\d+$"},
            "dimension": {"type": str, "values": ["channel", "region", "category"]},
            "value": {"type": str, "minLen": 1, "maxLen": 100},
        },
        "allowExtra": False,
    },
    "merge": {
        "required": ["dataid_a", "dataid_b", "on"],
        "properties": {
            "dataid_a": {"type": str, "pattern": r"^d\d+$"},
            "dataid_b": {"type": str, "pattern": r"^d\d+$"},
            "on": {"type": str, "values": ["channel", "region", "category", "date"]},
        },
        "allowExtra": False,
    },
    "compare_periods": {
        "required": ["dataid", "metric_id", "dimensions"],
        "properties": {
            "dataid": {"type": str, "pattern": r"^d\d+$"},
            "metric_id": {"type": str},
            "dimensions": {"type": list, "itemType": str},
            "p1_start": {"type": str, "pattern": r"^\d{4}-\d{2}-\d{2}"},
            "p1_end": {"type": str, "pattern": r"^\d{4}-\d{2}-\d{2}"},
            "p2_start": {"type": str, "pattern": r"^\d{4}-\d{2}-\d{2}"},
            "p2_end": {"type": str, "pattern": r"^\d{4}-\d{2}-\d{2}"},
            "period1_start": {"type": str},  # alias
            "period1_end": {"type": str},    # alias
            "period2_start": {"type": str},  # alias
            "period2_end": {"type": str},    # alias
            "metric": {"type": str},  # alias for metric_id
        },
        "allowExtra": False,
    },
    "done": {
        "required": ["summary"],
        "properties": {
            "summary": {"type": str, "minLen": 1, "maxLen": 500},
        },
        "allowExtra": True,
    },
}


# ══════════════════════════════════════════════════════════════
# Regex Structural Enforcement (Constrained Decoding Fallback)
# ══════════════════════════════════════════════════════════════

class JSONStructureEnforcer:
    """Regex-based structural enforcement for JSON model outputs.

    Since DeepSeek API doesn't support true constrained decoding,
    this implements a rejection-sampling approach:
      1. Validate against minimal JSON structure regex
      2. If structure fails, re-inject error and retry
      3. If structure passes, proceed to schema validation

    This eliminates common model output failures:
      - Missing closing braces
      - Trailing text after JSON
      - Nested unescaped quotes breaking parse
      - Backtick-wrapped JSON (```json ... ```)
    """

    # Extracts JSON object from model output (handles backtick wrapping)
    JSON_OBJECT_RE = re.compile(
        r'(?:```(?:json)?\s*)?(\{.*\})\s*(?:```)?',
        re.DOTALL
    )

    # Structural pattern: must have "action" field AND valid braces
    ACTION_PATTERN = re.compile(r'\{"action"\s*:\s*"(tool|done)"')

    def validate_structure(self, raw: str) -> dict:
        """Validate and extract JSON from raw model output.

        Returns {"valid": True, "json_str": clean_json} on success
        Returns {"valid": False, "error": reason} on failure
        """
        if not raw or not raw.strip():
            return {"valid": False, "error": "Empty output"}

        raw = raw.strip()

        # Step 1: Extract JSON from any wrapping
        m = self.JSON_OBJECT_RE.search(raw)
        if not m:
            return {"valid": False, "error": "No JSON object found in output"}

        json_str = m.group(1)

        # Step 2: Check for balanced braces
        if json_str.count("{") != json_str.count("}"):
            return {"valid": False, "error": "Unbalanced braces"}

        # Step 3: Verify required "action" field exists
        if not self.ACTION_PATTERN.search(json_str):
            return {"valid": False, "error": "Missing required 'action' field"}

        # Step 4: Try to parse
        try:
            json.loads(json_str)
            return {"valid": True, "json_str": json_str}
        except json.JSONDecodeError as e:
            # Try common fixes
            fixed = self._auto_fix(json_str)
            if fixed:
                try:
                    json.loads(fixed)
                    return {"valid": True, "json_str": fixed}
                except json.JSONDecodeError:
                    pass
            return {"valid": False, "error": f"JSON parse error: {e}"}

    def _auto_fix(self, json_str: str) -> Optional[str]:
        """Attempt common automatic fixes."""
        fixed = json_str

        # Fix 1: Trailing commas before }
        fixed = re.sub(r',\s*}', '}', fixed)

        # Fix 2: Trailing commas before ]
        fixed = re.sub(r',\s*]', ']', fixed)

        # Fix 3: Unescaped newlines in strings
        # (JSON strings cannot contain literal newlines)
        in_string = False
        chars = list(fixed)
        for i, c in enumerate(chars):
            if c == '"' and (i == 0 or chars[i - 1] != '\\'):
                in_string = not in_string
            elif c == '\n' and in_string:
                chars[i] = '\\n'

        fixed = ''.join(chars)

        if fixed != json_str:
            return fixed
        return None


# ══════════════════════════════════════════════════════════════
# Schema Validation
# ══════════════════════════════════════════════════════════════

@dataclass
class ValidationError:
    field: str
    message: str
    value: Any = None


class ToolSchemaValidator:
    """Validates tool call arguments against schema definitions."""

    def __init__(self):
        self.schemas = TOOL_SCHEMAS

    def validate(self, tool: str, args: dict) -> list[ValidationError]:
        """Validate tool arguments. Returns list of errors (empty = valid).

        Checks:
        - Required fields present
        - Field types match
        - Enum values allowed
        - Numeric ranges
        - String lengths
        - No extra fields (if allowExtra=False)
        """
        schema = self.schemas.get(tool)
        if schema is None:
            return []  # Unknown tools pass through

        errors = []

        # Check required fields
        for field in schema.get("required", []):
            if field not in args:
                errors.append(ValidationError(
                    field=field,
                    message=f"Required field '{field}' is missing",
                ))

        # Check each provided arg
        for field, value in args.items():
            prop = schema.get("properties", {}).get(field)
            if prop:
                errors.extend(self._validate_field(field, value, prop))

        # Check for extra fields
        if not schema.get("allowExtra", True):
            for field in args:
                if field not in schema.get("properties", {}):
                    errors.append(ValidationError(
                        field=field,
                        message=f"Unknown field '{field}', allowed: {list(schema['properties'].keys())}",
                    ))

        return errors

    def _validate_field(self, field: str, value: Any, prop: dict) -> list[ValidationError]:
        errors = []

        expected_type = prop.get("type")
        if expected_type == str and not isinstance(value, str):
            errors.append(ValidationError(field, f"Expected string, got {type(value).__name__}", value))
        elif expected_type == int and not isinstance(value, int):
            if isinstance(value, float) and value == int(value):
                pass  # Accept float that is actually an integer
            else:
                errors.append(ValidationError(field, f"Expected int, got {type(value).__name__}", value))
        elif expected_type == list and not isinstance(value, list):
            errors.append(ValidationError(field, f"Expected list, got {type(value).__name__}", value))
        elif expected_type == bool and not isinstance(value, bool):
            errors.append(ValidationError(field, f"Expected bool, got {type(value).__name__}", value))

        # Value constraints
        if isinstance(value, str):
            min_len = prop.get("minLen")
            max_len = prop.get("maxLen")
            if min_len is not None and len(value) < min_len:
                errors.append(ValidationError(field, f"String too short (min {min_len})", value))
            if max_len is not None and len(value) > max_len:
                errors.append(ValidationError(field, f"String too long (max {max_len})", value))

        if isinstance(value, (int, float)):
            min_v = prop.get("min")
            max_v = prop.get("max")
            if min_v is not None and value < min_v:
                errors.append(ValidationError(field, f"Value too small (min {min_v})", value))
            if max_v is not None and value > max_v:
                errors.append(ValidationError(field, f"Value too large (max {max_v})", value))

        # Allowed values
        if "values" in prop and value not in prop["values"]:
            errors.append(ValidationError(
                field,
                f"Invalid value '{value}', allowed: {prop['values']}",
                value,
            ))

        # Pattern check
        if isinstance(value, str) and "pattern" in prop:
            if not re.match(prop["pattern"], value):
                errors.append(ValidationError(field, f"Value '{value}' does not match pattern", value))

        # List item type check
        if isinstance(value, list) and "itemType" in prop:
            for i, item in enumerate(value):
                if prop["itemType"] == str and not isinstance(item, str):
                    errors.append(ValidationError(f"{field}[{i}]", f"Expected string, got {type(item).__name__}", item))

        return errors

    def format_errors(self, errors: list[ValidationError]) -> str:
        """Format validation errors as a model-friendly message."""
        if not errors:
            return ""

        lines = ["Your tool call has errors:"]
        for e in errors:
            lines.append(f"  - {e.field}: {e.message}")
        lines.append("\nPlease correct these and try again with valid arguments.")
        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
# Parse + Retry Manager
# ══════════════════════════════════════════════════════════════

@dataclass
class ParseResult:
    success: bool
    action: dict | None = None
    error: str = ""
    retry_count: int = 0
    auto_fixed: bool = False


class ParseRetryManager:
    """Manages the try-parse → re-inject → retry cycle.

    Flow:
      1. Try to extract and parse JSON from model output
      2. On structure failure → auto-fix common issues, re-parse
      3. On parse failure → construct error message, feed back to model
      4. On schema validation failure → format errors, feed back to model
      5. Until success or max retries exhausted
    """

    MAX_PARSE_RETRIES = 3
    MAX_SCHEMA_RETRIES = 2

    def __init__(self):
        self.enforcer = JSONStructureEnforcer()
        self.validator = ToolSchemaValidator()
        self.parse_errors: list[str] = []
        self.schema_errors: list[str] = []
        self.retry_count: int = 0

    def parse_and_validate(self, raw_output: str) -> ParseResult:
        """Parse model output and validate tool call structure.

        Returns ParseResult with success/action/error.
        """
        # Layer 1: Structural enforcement (regex)
        struct = self.enforcer.validate_structure(raw_output)
        if not struct["valid"]:
            return ParseResult(
                success=False,
                error=self._build_struct_error(raw_output, struct["error"]),
                retry_count=self.retry_count,
            )

        # Layer 2: JSON parse
        json_str = struct["json_str"]
        auto_fixed = False
        try:
            action = json.loads(json_str)
        except json.JSONDecodeError as e:
            # Try auto-fix
            fixed = self.enforcer._auto_fix(json_str)
            if fixed:
                try:
                    action = json.loads(fixed)
                    auto_fixed = True
                except json.JSONDecodeError:
                    self.retry_count += 1
                    return ParseResult(
                        success=False,
                        error=self._build_parse_error(raw_output, str(e), json_str),
                        retry_count=self.retry_count,
                    )
            else:
                self.retry_count += 1
                return ParseResult(
                    success=False,
                    error=self._build_parse_error(raw_output, str(e), json_str),
                    retry_count=self.retry_count,
                )

        # Layer 3: Action type check
        action_type = action.get("action")
        if action_type not in ("tool", "done"):
            self.retry_count += 1
            return ParseResult(
                success=False,
                error=f"Invalid action type '{action_type}'. Must be 'tool' or 'done'.",
                retry_count=self.retry_count,
            )

        # Layer 4: Schema validation (only for tool actions)
        if action_type == "tool":
            tool = action.get("tool", "")
            args = action.get("args", {})
            if not isinstance(args, dict):
                args = {}
                action["args"] = args

            schema_errors = self.validator.validate(tool, args)
            if schema_errors:
                self.retry_count += 1
                return ParseResult(
                    success=False,
                    error=self.validator.format_errors(schema_errors),
                    retry_count=self.retry_count,
                )

        return ParseResult(
            success=True,
            action=action,
            retry_count=self.retry_count,
            auto_fixed=auto_fixed,
        )

    def _build_struct_error(self, raw: str, reason: str) -> str:
        """Build a model-friendly structural error message."""
        preview = raw[:200]
        return (
            f"Your output is structurally invalid.\n\n"
            f"Error: {reason}\n\n"
            f"Your output (first 200 chars):\n{preview}\n\n"
            f"Please respond with EXACTLY ONE JSON object containing "
            f'the "action" field, like:\n'
            f'{{"action": "tool", "tool": "switch", "args": {{"model_id": "order_detail"}}}}\n'
            f'{{"action": "done", "summary": "your summary"}}'
        )

    def _build_parse_error(self, raw: str, error: str, extracted: str) -> str:
        """Build a model-friendly JSON parse error message."""
        preview = extracted[:200]
        return (
            f"Your JSON output failed to parse.\n\n"
            f"Parse error: {error}\n\n"
            f"Extracted JSON (first 200 chars):\n{preview}\n\n"
            f"Common issues:\n"
            f"- Trailing commas after last element (remove them)\n"
            f"- Unescaped quotes inside strings\n"
            f"- Missing closing braces\n"
            f"- Non-JSON text before/after the object\n\n"
            f"Please output ONLY the JSON object, with no surrounding text or markdown."
        )

    def should_retry(self) -> bool:
        """Check if retry limit hasn't been exceeded."""
        return self.retry_count < self.MAX_PARSE_RETRIES

    def reset(self):
        self.retry_count = 0
        self.parse_errors.clear()
        self.schema_errors.clear()


# ══════════════════════════════════════════════════════════════
# Idempotency Key Support
# ══════════════════════════════════════════════════════════════

class IdempotencyManager:
    """Tracks idempotency keys for write operations.

    Even though current tools are all read-only, this is designed
    for future write tools (insert, update, delete).

    Each key maps to a result; duplicate keys return the cached result.
    This prevents side-effect duplication from retries/replays.

    Key design: {tool_name}:{request_hash}:{client_id}
    - tool_name: which operation
    - request_hash: sha256(args + timestamp truncated to minute)
    - client_id: tenant/session identifier
    """

    def __init__(self, ttl_seconds: int = 3600):
        self._store: dict[str, dict] = {}
        self._ttl = ttl_seconds

    def generate_key(self, tool_name: str, args: dict, client_id: str = "",
                     ttl_seconds: int = None) -> str:
        """Generate an idempotency key from tool + args + client.

        Truncates timestamp to minute to allow near-duplicate detection
        while still allowing intentional re-execution after a minute.
        """
        import time
        min_bucket = int(time.time() / 60)
        payload = f"{tool_name}:{json.dumps(args, sort_keys=True)}:{client_id}:{min_bucket}"
        key = hashlib.sha256(payload.encode()).hexdigest()[:12]
        return key

    def check(self, key: str) -> Optional[dict]:
        """Check if key exists (not expired). Returns cached result or None."""
        entry = self._store.get(key)
        if entry is None:
            return None

        import time
        if time.time() - entry.get("created_at", 0) > (entry.get("ttl", self._ttl)):
            del self._store[key]
            return None

        return entry.get("result")

    def store(self, key: str, result: dict, ttl_seconds: int = None):
        """Store a result for an idempotency key."""
        import time
        self._store[key] = {
            "result": result,
            "created_at": time.time(),
            "ttl": ttl_seconds or self._ttl,
        }

    def cleanup(self):
        """Remove expired entries."""
        import time
        now = time.time()
        expired = [k for k, v in self._store.items()
                   if now - v.get("created_at", 0) > v.get("ttl", self._ttl)]
        for k in expired:
            del self._store[k]

    def wrap(self, tool_fn: Callable, tool_name: str, client_id: str = "",
             ttl_seconds: int = None) -> Callable:
        """Wrap a tool function with idempotency support.

        Returns a callable that checks for cached results before execution.
        """
        def wrapped(args: dict) -> dict:
            key = self.generate_key(tool_name, args, client_id, ttl_seconds)
            cached = self.check(key)
            if cached is not None:
                logger.info("idempotency_cache_hit", tool=tool_name, key=key)
                return cached

            result = tool_fn(args)
            self.store(key, result, ttl_seconds)
            return result

        wrapped.__name__ = tool_fn.__name__
        return wrapped
