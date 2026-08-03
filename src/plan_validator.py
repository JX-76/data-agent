# -*- coding: utf-8 -*-
"""Validation and normalization for AnalysisPlan v2.

The validator is intentionally semantic-layer agnostic. It protects the
planning/execution boundary with structural checks; SQL-specific checks belong
to the later execution validation stage.
"""
from __future__ import unicode_literals

try:
    unicode
except NameError:  # pragma: no cover - Python 3 compatibility
    unicode = str


TERMINAL_STATUSES = ("blocked", "need_clarification", "pending_human_review", "error", "unsupported")
VALID_EXECUTION_MODES = ("plan_act", "react")


def _as_dict(value):
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if hasattr(value, "to_dict"):
        return value.to_dict()
    return dict(getattr(value, "__dict__", {}) or {})


def _list(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _normalized_policy(value, default_mode):
    policy = _as_dict(value)
    policy.setdefault("mode", default_mode)
    return policy


def _filter_errors(filters):
    errors = []
    for index, item in enumerate(filters):
        if not isinstance(item, dict):
            errors.append("filters[%d] must be an object" % index)
            continue
        if not item.get("field") and not item.get("dimension"):
            errors.append("filters[%d] requires field or dimension" % index)
        if "value" not in item and "values" not in item:
            errors.append("filters[%d] requires value or values" % index)
    return errors


def validate_analysis_plan(plan):
    """Return a non-mutating validation report for an AnalysisPlan-like input."""
    data = _as_dict(plan)
    status = data.get("status") or "ok"
    errors = []
    warnings = []

    if not data.get("query"):
        errors.append("query is required")
    if status not in TERMINAL_STATUSES:
        if not data.get("metric") and not data.get("metrics"):
            errors.append("metric or metrics is required for executable plan")
        if not data.get("model"):
            errors.append("model is required for executable plan")
        if not data.get("time_range"):
            errors.append("time_range is required for executable plan")

    dimensions = data.get("dimensions") or []
    if not isinstance(dimensions, list):
        errors.append("dimensions must be a list")
    filters = data.get("filters") or []
    if not isinstance(filters, list):
        errors.append("filters must be a list")
    else:
        errors.extend(_filter_errors(filters))
    if data.get("execution_mode") not in VALID_EXECUTION_MODES:
        errors.append("execution_mode must be plan_act or react")
    if not isinstance(data.get("join_strategy") or {}, dict):
        errors.append("join_strategy must be an object")
    if not isinstance(data.get("verification_policy") or {}, dict):
        errors.append("verification_policy must be an object")
    if not isinstance(data.get("fallback_policy") or {}, dict):
        errors.append("fallback_policy must be an object")
    if data.get("schema_version") not in (None, "v1", "v2"):
        warnings.append("unknown schema_version")

    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "contract": "analysis_plan_v2",
    }


def normalize_analysis_plan_v2(plan):
    """Normalize additive v2 planning fields without changing legacy semantics."""
    data = dict(_as_dict(plan))
    # The boundary upgrades legacy v1 plans; the public object remains
    # additive-compatible but downstream execution sees one canonical version.
    data["schema_version"] = "v2"
    data["plan_version"] = "v2"
    data["metrics"] = _list(data.get("metrics") or data.get("metric"))
    data["dimensions"] = _list(data.get("dimensions"))
    data["filters"] = _list(data.get("filters"))
    data["join_strategy"] = _normalized_policy(data.get("join_strategy"), "semantic")
    data["verification_policy"] = _normalized_policy(data.get("verification_policy"), "basic")
    data["fallback_policy"] = _normalized_policy(data.get("fallback_policy"), "safe_fail")
    data.setdefault("execution_mode", "plan_act")
    diagnostics = _as_dict(data.get("diagnostics"))
    diagnostics["analysis_plan_contract"] = "v2"
    data["diagnostics"] = diagnostics
    return data


def enforce_analysis_plan_v2(plan):
    """Normalize and validate, returning ``(data, report)`` for callers."""
    data = normalize_analysis_plan_v2(plan)
    report = validate_analysis_plan(data)
    return data, report


__all__ = ["validate_analysis_plan", "normalize_analysis_plan_v2", "enforce_analysis_plan_v2"]
