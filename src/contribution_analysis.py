# -*- coding: utf-8 -*-
"""Contribution analysis (Pareto) for dimension-level driver decomposition.

Provides:
- pareto_analysis: rank dimensions by contribution to a metric
- contribution_breakdown: decompose total metric into dimension-level shares
- top_n_drivers: identify the top N drivers of a metric change

Python 2.7 compatible and deterministic.
"""

from __future__ import unicode_literals


def _to_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def pareto_analysis(rows, metric="gmv", dimension="channel", top_n=5):
    """Pareto analysis: rank dimension values by contribution to a metric.

    Returns:
        {
            "status": "ok",
            "dimension": "channel",
            "metric": "gmv",
            "total": 100000.0,
            "items": [
                {"dimension": "online", "value": 50000.0, "pct": 0.5, "cumulative_pct": 0.5},
                ...
            ],
            "pareto_cutoff": 3,  # number of items to reach 80%
            "top_n": 5,
        }
    """
    rows = rows or []
    items = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        dim_val = row.get(dimension) or row.get("dimension") or "unknown"
        value = _to_float(row.get(metric))
        items.append({"dimension": dim_val, "value": value})

    items.sort(key=lambda x: -x["value"])
    total = sum(item["value"] for item in items)
    cumulative = 0.0
    pareto_cutoff = 0
    for idx, item in enumerate(items):
        item["pct"] = None if total == 0 else item["value"] / total
        cumulative += item["value"]
        item["cumulative_pct"] = None if total == 0 else cumulative / total
        if pareto_cutoff == 0 and item.get("cumulative_pct") is not None and item["cumulative_pct"] >= 0.8:
            pareto_cutoff = idx + 1

    return {
        "status": "ok" if items else "insufficient_data",
        "dimension": dimension,
        "metric": metric,
        "total": total,
        "items": items[:top_n],
        "pareto_cutoff": pareto_cutoff,
        "top_n": top_n,
    }


def contribution_breakdown(rows, metric="gmv", dimension="channel"):
    """Decompose total metric into dimension-level shares.

    Returns:
        {
            "status": "ok",
            "dimension": "channel",
            "metric": "gmv",
            "total": 100000.0,
            "shares": [
                {"dimension": "online", "value": 50000.0, "share_pct": 0.5},
                ...
            ],
        }
    """
    rows = rows or []
    items = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        dim_val = row.get(dimension) or row.get("dimension") or "unknown"
        value = _to_float(row.get(metric))
        items.append({"dimension": dim_val, "value": value})

    total = sum(item["value"] for item in items)
    shares = []
    for item in items:
        shares.append({
            "dimension": item["dimension"],
            "value": item["value"],
            "share_pct": None if total == 0 else item["value"] / total,
        })
    shares.sort(key=lambda x: -x["value"])

    return {
        "status": "ok" if shares else "insufficient_data",
        "dimension": dimension,
        "metric": metric,
        "total": total,
        "shares": shares,
    }


def top_n_drivers(rows, metric="gmv", dimension="channel", top_n=3):
    """Identify the top N drivers of a metric.

    This is a simplified version of pareto_analysis that returns
    only the top N dimension values sorted by contribution.

    Returns:
        {
            "status": "ok",
            "dimension": "channel",
            "metric": "gmv",
            "top_drivers": [
                {"dimension": "online", "value": 50000.0, "pct": 0.5},
                ...
            ],
        }
    """
    result = pareto_analysis(rows, metric=metric, dimension=dimension, top_n=top_n)
    return {
        "status": result["status"],
        "dimension": result["dimension"],
        "metric": result["metric"],
        "top_drivers": result["items"],
    }


__all__ = ["pareto_analysis", "contribution_breakdown", "top_n_drivers"]
