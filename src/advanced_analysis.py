# -*- coding: utf-8 -*-
"""Extensible analysis helpers for comparison, anomaly, attribution, funnel and retention.

These helpers are intentionally deterministic and dependency-free. They provide
stable payload shapes now and can later be replaced by richer statistical or SQL
strategies without changing AgentFacade.
"""


def _to_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def build_comparison(rows, metric="gmv"):
    rows = rows or []
    if len(rows) < 2:
        return {"status": "insufficient_data", "metric": metric, "items": [], "delta": None, "delta_pct": None}
    current = _to_float(rows[-1].get(metric) if isinstance(rows[-1], dict) else None)
    previous = _to_float(rows[-2].get(metric) if isinstance(rows[-2], dict) else None)
    delta = current - previous
    delta_pct = None if previous == 0 else delta / previous
    return {"status": "ok", "metric": metric, "current": current, "previous": previous, "delta": delta, "delta_pct": delta_pct, "items": rows[-2:]}


def detect_anomalies(rows, metric="gmv", threshold=2.0):
    rows = rows or []
    values = [_to_float(r.get(metric) if isinstance(r, dict) else None) for r in rows]
    if len(values) < 3:
        return {"status": "insufficient_data", "metric": metric, "items": []}
    mean = sum(values) / float(len(values))
    variance = sum((v - mean) ** 2 for v in values) / float(len(values))
    std = variance ** 0.5
    items = []
    for idx, value in enumerate(values):
        z = 0.0 if std == 0 else (value - mean) / std
        if abs(z) >= threshold:
            item = dict(rows[idx]) if isinstance(rows[idx], dict) else {"value": value}
            item["z_score"] = z
            item["anomaly_direction"] = "high" if z > 0 else "low"
            items.append(item)
    return {"status": "ok", "metric": metric, "mean": mean, "std": std, "threshold": threshold, "items": items}


def attribute_change(rows, metric="gmv", dimension=None, top_n=5):
    rows = rows or []
    contributions = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        current = _to_float(row.get("current", row.get(metric)))
        previous = _to_float(row.get("previous", 0))
        delta = current - previous
        contributions.append({"dimension": row.get(dimension) if dimension else row.get("dimension"), "current": current, "previous": previous, "delta": delta})
    total_delta = sum(item["delta"] for item in contributions)
    for item in contributions:
        item["contribution_pct"] = None if total_delta == 0 else item["delta"] / total_delta
    contributions.sort(key=lambda x: abs(x["delta"]), reverse=True)
    return {"status": "ok", "metric": metric, "dimension": dimension, "total_delta": total_delta, "top_drivers": contributions[:top_n]}


def build_funnel(rows, step_field="step", value_field="users"):
    rows = rows or []
    steps = []
    first = None
    prev = None
    for row in rows:
        if not isinstance(row, dict):
            continue
        value = _to_float(row.get(value_field))
        if first is None:
            first = value
        conversion = None if first in (None, 0) else value / first
        step_conversion = None if prev in (None, 0) else value / prev
        steps.append({"step": row.get(step_field), "value": value, "conversion_rate": conversion, "step_conversion_rate": step_conversion})
        prev = value
    return {"status": "ok" if steps else "insufficient_data", "steps": steps}


def build_retention(rows, cohort_field="cohort", period_field="period", value_field="retention_rate"):
    rows = rows or []
    cohorts = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        cohort = row.get(cohort_field)
        cohorts.setdefault(cohort, [])
        cohorts[cohort].append({"period": row.get(period_field), "retention_rate": _to_float(row.get(value_field))})
    return {"status": "ok" if cohorts else "insufficient_data", "cohorts": cohorts}


def summarize_by_task_type(task_type, rows, metric="gmv", dimension=None):
    if task_type == "comparison":
        return {"comparison": build_comparison(rows, metric=metric)}
    if task_type == "anomaly":
        return {"anomaly": detect_anomalies(rows, metric=metric)}
    if task_type == "attribution":
        return {"attribution": attribute_change(rows, metric=metric, dimension=dimension)}
    if task_type == "funnel":
        return {"funnel": build_funnel(rows)}
    if task_type == "retention":
        return {"retention": build_retention(rows)}
    return {}


__all__ = ["build_comparison", "detect_anomalies", "attribute_change", "build_funnel", "build_retention", "summarize_by_task_type"]
