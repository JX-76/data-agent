# -*- coding: utf-8 -*-
"""Aggregate-only retention execution.

Produces cohort/period/size/active/rate rows. It never selects or returns
user_id or per-user trajectories; entity ids are only used inside COUNT(DISTINCT)
and de-duplication. Callers pass either a sandbox connection (SQL path) or raw
events (in-memory path, sandbox/tests).
"""
from __future__ import unicode_literals

from cohort_registry import get_cohort_registry
from cohort_analysis import analyze_cohort_events


def _period_days(grain):
    return {"day": 1, "week": 7, "month": 30}.get(grain, 1)


def build_retention_sql(definition, horizons):
    """Return a read-only aggregate SQL string (documentation/provenance).

    Grain offset is expressed in days for the sandbox schema. The SQL only
    projects aggregate columns and forbids row-level identifiers.
    """
    d = definition
    grain_days = _period_days(d.get("period_grain"))
    horizon_days = ", ".join(str(int(h[1:]) * grain_days) for h in horizons if h and h[1:].isdigit())
    tenant_col = d.get("tenant_column") or "tenant_id"
    return (
        "WITH acq AS (SELECT %s AS entity, MIN(event_date) AS cohort_start, %s AS tenant "
        "FROM %s WHERE event_name = '%s' GROUP BY %s, %s) "
        "SELECT cohort_start AS cohort, COUNT(DISTINCT entity) AS cohort_size "
        "-- retention horizons (days): %s; active event: %s\n"
        "FROM acq GROUP BY cohort_start ORDER BY cohort_start"
    ) % (d.get("entity_key"), tenant_col, d.get("model"), d.get("acquisition_event"),
         d.get("entity_key"), tenant_col, horizon_days or "n/a", d.get("active_event"))


def run_retention(cohort_id=None, grain=None, horizons=None, tenant_id=None,
                  events=None, registry=None, sample=True):
    """Resolve cohort definition, validate, and compute an aggregate matrix.

    Returns an execution-result-shaped dict compatible with the mainline:
    status/results/results_summary/diagnostics plus retention metadata.
    """
    registry = registry or get_cohort_registry()
    resolution = registry.resolve(cohort_id, grain, horizons)
    if not resolution.get("ok"):
        errors = resolution.get("errors") or []
        status = "unsupported" if any(e.startswith("cohort_capability_") or e.startswith("unsupported_") for e in errors) else "need_clarification"
        return {"status": status, "results": [], "results_summary": {"row_count": 0, "source": "cohort"},
                "diagnostics": {"quality": {"empty_result": True}, "cohort_errors": errors, "phase": "cohort_resolution"},
                "task_type": "retention", "cohort_resolution": resolution}
    definition = resolution.get("definition") or {}
    use_horizons = list(horizons or definition.get("retention_horizons") or [])
    sql = build_retention_sql(definition, use_horizons)
    analysis = analyze_cohort_events(events or [], definition, use_horizons, tenant_id)
    matrix = analysis.get("matrix") or []
    status = "ok" if matrix else ("need_clarification" if analysis.get("status") == "need_clarification" else "insufficient_data")
    return {
        "status": status,
        "results": matrix,
        "results_summary": {"row_count": len(matrix), "source": "cohort_sample" if sample else "cohort"},
        "sql": sql,
        "diagnostics": {"quality": {"empty_result": not matrix},
                        "strategy": "retention",
                        "cohort_definition": definition,
                        "is_sample": bool(sample),
                        "sample_size": analysis.get("summary_facts", {}).get("sample_size")},
        "task_type": "retention",
        "cohort_resolution": resolution,
        "analysis": analysis,
    }


__all__ = ["build_retention_sql", "run_retention"]
