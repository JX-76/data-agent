# -*- coding: utf-8 -*-
"""Factual, privacy-safe provenance for Data Agent results."""
from __future__ import unicode_literals

import hashlib
import json
import time

try:
    from semantic_registry import get_semantic_registry
except Exception:
    get_semantic_registry = None


def _data(value):
    return value.to_dict() if hasattr(value, "to_dict") else dict(value or {})


def _fingerprint(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    if not isinstance(raw, bytes):
        raw = raw.encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def _sql_fingerprint(sql):
    return _fingerprint({"sql": sql}) if sql else None


def build_provenance(plan, result, semantic_registry=None):
    """Build reproducibility facts without embedding result rows or raw SQL."""
    p = _data(plan)
    r = _data(result)
    diagnostics = r.get("diagnostics") or {}
    execution = r.get("execution") or {}
    registry = semantic_registry or (get_semantic_registry() if get_semantic_registry else None)
    semantic_version = registry.get_version() if registry and hasattr(registry, "get_version") else None
    rows = r.get("results")
    if rows is None:
        rows = execution.get("results") or execution.get("rows")
    row_count = len(rows) if isinstance(rows, (list, tuple)) else None
    quality = diagnostics.get("quality") or {}
    source = p.get("model") or r.get("model")
    cohort_definition = p.get("cohort_definition") or diagnostics.get("cohort_definition") or {}
    if not isinstance(cohort_definition, dict):
        cohort_definition = {}
    cohort_provenance = {}
    if p.get("task_type") == "retention" or r.get("task_type") == "retention":
        cohort_provenance = {
            "cohort_id": cohort_definition.get("cohort_id"),
            "source": cohort_definition.get("source") or source,
            "period_grain": cohort_definition.get("period_grain"),
            "retention_horizons": list(cohort_definition.get("retention_horizons") or p.get("retention_horizons") or []),
            "timezone": cohort_definition.get("timezone"),
            "is_sample": bool(diagnostics.get("is_sample")),
            "sample_size": diagnostics.get("sample_size"),
            "aggregate_only": True,
        }
    return {
        "contract": "provenance_v1",
        "recorded_at": time.time(),
        "trace_id": r.get("trace_id"), "session_id": r.get("session_id"),
        "task_id": r.get("task_id"), "parent_task_id": r.get("parent_task_id"),
        "plan": {"plan_version": p.get("plan_version"), "schema_version": p.get("schema_version"),
                 "fingerprint": _fingerprint({"task_type": p.get("task_type"), "metric": p.get("metric"),
                                                "dimensions": p.get("dimensions"), "filters": p.get("filters"),
                                                "time_range": p.get("time_range"), "model": source})},
        "semantic": {"version": semantic_version, "metric": p.get("metric"),
                     "dimensions": list(p.get("dimensions") or []), "model": source},
        "data_source": {"model": source, "adapter": execution.get("adapter") or diagnostics.get("data_source_adapter"),
                        "used_db": bool(execution.get("used_db"))},
        "execution": {"sql_fingerprint": _sql_fingerprint(r.get("sql")),
                      "sql_preflight": diagnostics.get("sql_preflight"),
                      "retry_count": execution.get("retry_count") or diagnostics.get("retry_count", 0),
                      "row_count": row_count, "elapsed_ms": r.get("elapsed_ms")},
        "quality": {"empty_result": quality.get("empty_result"), "warnings": quality.get("warnings") or []},
        "cohort": cohort_provenance,
        "limitations": ["no_raw_sql_or_rows_in_provenance"],
    }


__all__ = ["build_provenance"]
