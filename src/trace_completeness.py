# -*- coding: utf-8 -*-
"""Lightweight trace aggregation and completeness helpers for Phase 4.

The helpers are read-only and additive. They summarize existing trace events
without changing execution behavior or requiring a new runtime contract.
"""
from __future__ import unicode_literals


_TERMINAL_EXPECTATIONS = {
    "ok": ["route", "execute", "analyze", "report"],
    "blocked": ["precheck"],
    "need_clarification": ["precheck", "route"],
    "pending_human_review": ["precheck", "route"],
    "fallback": ["precheck", "route"],
    "error": ["precheck"],
}


def _event_name(event):
    if not isinstance(event, dict):
        return None
    return event.get("name") or event.get("stage")


def aggregate_dag_trace(trace):
    """Return a compact DAG summary from raw trace events."""
    nodes = []
    node_counts = {}
    first_failure = None
    for event in trace or []:
        if not isinstance(event, dict):
            continue
        name = _event_name(event)
        if not name:
            continue
        if name == "dag_node":
            payload = event.get("metadata") or {}
            node = payload.get("node") or event.get("stage")
            if not node:
                continue
            status = payload.get("status") or event.get("status")
            reason = payload.get("reason")
        else:
            node = name
            status = event.get("status")
            reason = event.get("reason")
        node_counts[node] = node_counts.get(node, 0) + 1
        nodes.append({
            "node": node,
            "status": status,
            "reason": reason,
        })
        if first_failure is None and status in ("blocked", "error", "failed"):
            first_failure = node
    node_order = [item["node"] for item in nodes]
    return {
        "contract": "dag_trace_summary_v1",
        "nodes": nodes,
        "observed_nodes": node_order,
        "node_count": len(nodes),
        "node_counts": node_counts,
        "first_failure": first_failure,
        "node_order": node_order,
    }


def validate_trace_completeness(status, trace):
    """Validate expected DAG nodes for a terminal status."""
    expected = list(_TERMINAL_EXPECTATIONS.get(status, ["precheck"]))
    summary = aggregate_dag_trace(trace)
    observed = set(summary.get("node_order") or [])
    missing = [node for node in expected if node not in observed]
    unexpected_execute = status in ("blocked", "need_clarification", "pending_human_review", "fallback", "error") and "execute" in observed
    complete = not missing and not unexpected_execute
    summary = dict(summary)
    summary["first_failure"] = summary.get("first_failure") or (missing[0] if missing else None)
    return {
        "contract": "trace_completeness_v1",
        "status": status,
        "expected_nodes": expected,
        "observed_nodes": summary.get("node_order") or [],
        "missing_nodes": missing,
        "complete": complete,
        "summary": summary,
    }


__all__ = ["aggregate_dag_trace", "validate_trace_completeness"]
