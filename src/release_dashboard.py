# -*- coding: utf-8 -*-
"""Safe, product-facing Release v1 operations dashboard."""
from __future__ import unicode_literals

import time

TERMINAL_STATUSES = ("ok", "blocked", "need_clarification", "pending_human_review", "error", "fallback")
TRACE_QUALITY_CONTRACT = "trace_quality_summary_v1"


def _percentile(values, ratio):
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[int(round((len(ordered) - 1) * ratio))]


def compute_dashboard(history, metrics, gate_results=None, trace_quality=None):
    """Return safe aggregates only; never include query, SQL, rows or raw payloads."""
    history = list(history or [])
    metrics = metrics or {}
    gate_results = list(gate_results or [])
    total = int(metrics.get("total", len(history)) or 0)
    denominator = max(1, total)
    terminal_breakdown = dict((status, int(metrics.get(status, 0) or 0)) for status in TERMINAL_STATUSES)
    if not any(terminal_breakdown.values()) and history:
        for item in history:
            status = item.get("status") or "error"
            terminal_breakdown[status] = terminal_breakdown.get(status, 0) + 1
    quality_scores = [item.get("quality_score") for item in history if isinstance(item.get("quality_score"), (int, float))]
    elapsed = [item.get("elapsed_ms") for item in history if isinstance(item.get("elapsed_ms"), (int, float)) and item.get("elapsed_ms") >= 0]
    failure_categories = {}
    capability_coverage = {}
    metric_distribution = {}
    for item in history:
        metric = item.get("metric")
        if metric:
            metric_distribution[metric] = metric_distribution.get(metric, 0) + 1
        task_type = item.get("task_type") or metric or "unknown"
        capability_coverage[task_type] = capability_coverage.get(task_type, 0) + 1
        status = item.get("status") or "error"
        if status != "ok":
            category = item.get("failure_category") or status
            failure_categories[category] = failure_categories.get(category, 0) + 1
    ok_rate = round(terminal_breakdown.get("ok", 0) / float(denominator), 4)
    latest_gate = gate_results[-1] if gate_results else {"name": None, "status": "not_run", "passed": None, "timestamp": None}
    trace_quality = trace_quality or {}
    return {
        "contract": "release_v1_dashboard", "generated_at": int(time.time()), "total_queries": total,
        "success_rate": ok_rate,
        "rates": {"ok_rate": ok_rate, "block_rate": round(terminal_breakdown.get("blocked", 0) / float(denominator), 4), "clarification_rate": round(terminal_breakdown.get("need_clarification", 0) / float(denominator), 4), "error_rate": round(terminal_breakdown.get("error", 0) / float(denominator), 4)},
        "terminal_breakdown": terminal_breakdown, "failure_categories": failure_categories,
        "metric_distribution": metric_distribution,
        "latency": {"count": len(elapsed), "avg_ms": round(sum(elapsed) / float(max(1, len(elapsed))), 2), "max_ms": max(elapsed) if elapsed else 0, "p50_ms": _percentile(elapsed, .50), "p95_ms": _percentile(elapsed, .95)},
        "capability_coverage": {"executed_task_types": sorted(capability_coverage.keys()), "by_task_type": capability_coverage, "count": len(capability_coverage)},
        "quality": {"avg_score": round(sum(quality_scores) / float(max(1, len(quality_scores))), 4), "count": len(quality_scores), "below_threshold": sum(1 for score in quality_scores if score < .78), "below_threshold_rate": round(sum(1 for score in quality_scores if score < .78) / float(max(1, len(quality_scores))), 4)},
        "trace_quality": {
            "contract": TRACE_QUALITY_CONTRACT,
            "evaluated_count": int(trace_quality.get("evaluated_count", 0) or 0),
            "complete_count": int(trace_quality.get("complete_count", 0) or 0),
            "incomplete_count": int(trace_quality.get("incomplete_count", 0) or 0),
            "skipped_count": int(trace_quality.get("skipped_count", 0) or 0),
            "complete_rate": round(float(trace_quality.get("complete_rate", 0) or 0), 4),
            "missing_node_breakdown": dict(trace_quality.get("missing_node_breakdown") or {}),
            "first_failure_breakdown": dict(trace_quality.get("first_failure_breakdown") or {}),
        },
        "recent_gate": latest_gate, "recent_gates": gate_results[-10:], "history_count": len(history),
    }


def format_dashboard_text(dashboard):
    d = dashboard or {}
    terminal = d.get("terminal_breakdown") or {}
    latency = d.get("latency") or {}
    quality = d.get("quality") or {}
    trace_quality = d.get("trace_quality") or {}
    gates = d.get("recent_gate") or {}
    lines = ["=" * 60, "Release v1 Operations Dashboard", "=" * 60,
             "Total queries    : %d" % d.get("total_queries", 0),
             "Success rate     : %.1f%%" % (d.get("success_rate", 0) * 100), "", "Terminal breakdown:"]
    for status in TERMINAL_STATUSES:
        lines.append("  %-16s: %d" % (status, terminal.get(status, 0)))
    lines.extend(["", "Quality scores: count=%s avg=%s below_threshold=%s" % (quality.get("count", 0), quality.get("avg_score", 0), quality.get("below_threshold", 0)),
                  "Latency: avg=%.2fms p50=%sms p95=%sms max=%sms" % (latency.get("avg_ms", 0), latency.get("p50_ms", 0), latency.get("p95_ms", 0), latency.get("max_ms", 0)),
                  "Capabilities: %s" % ", ".join((d.get("capability_coverage") or {}).get("executed_task_types") or ["none"]),
                  "Trace quality : %s/%s complete (%.1f%%)" % (trace_quality.get("complete_count", 0), trace_quality.get("evaluated_count", 0), trace_quality.get("complete_rate", 0) * 100),
                  "Latest gate: %s (%s)" % (gates.get("name") or "not_run", gates.get("status") or "not_run"), "=" * 60])
    return "\n".join(lines)


def release_dashboard_from_api():
    try:
        import importlib
        api = importlib.import_module("release_api")
        return compute_dashboard(api._HISTORY, api._METRICS, getattr(api, "_GATE_RESULTS", []))
    except Exception as exc:
        return {"error": str(exc)}


__all__ = ["compute_dashboard", "format_dashboard_text", "release_dashboard_from_api", "TERMINAL_STATUSES", "TRACE_QUALITY_CONTRACT"]
