# -*- coding: utf-8 -*-
"""Vendor-neutral operational monitoring for Data Agent requests.

The observer owns raw trace events.  This module turns those events into a
safe request-level monitoring card and rolling counters that can be served by
an API, exported to Prometheus, or rendered by a dashboard without coupling
core agent code to a monitoring vendor.
"""
from __future__ import unicode_literals

import time


MONITORING_CONTRACT = "agent_monitoring_v1"


class AgentMonitoring(object):
    def __init__(self, observer=None, max_recent=200):
        self.observer = observer
        self.max_recent = max_recent
        self._requests = []

    def summarize_request(self, trace_id, result=None):
        result = result.to_dict() if hasattr(result, "to_dict") else dict(result or {})
        trace = self.observer.summarize(trace_id) if self.observer else {}
        diagnostics = result.get("diagnostics") or {}
        execution = result.get("execution") or {}
        errors = result.get("errors") or []
        failure_stage = trace.get("failure_stage")
        failure_type = trace.get("failure_type")
        if not failure_stage and errors:
            first = errors[0] if isinstance(errors[0], dict) else {}
            failure_stage = first.get("stage") or "agent"
            failure_type = first.get("error") or "unknown_error"
        if not failure_type:
            failure_type = execution.get("failure_type") or diagnostics.get("failure_type")
        status = result.get("status") or "error"
        retry_count = execution.get("retry_count")
        if retry_count is None:
            retry_count = diagnostics.get("retry_count", 0)
        card = {
            "contract": MONITORING_CONTRACT,
            "trace_id": trace_id,
            "session_id": result.get("session_id") or trace.get("session_id"),
            "task_id": result.get("task_id") or trace.get("task_id"),
            "status": status,
            "intent": result.get("intent"),
            "task_type": result.get("task_type") or (result.get("plan") or {}).get("task_type"),
            "elapsed_ms": result.get("elapsed_ms") if result.get("elapsed_ms") is not None else trace.get("duration_ms"),
            "event_count": trace.get("event_count", 0),
            "stage_counts": trace.get("event_names", {}),
            "status_counts": trace.get("status_counts", {}),
            "failure_stage": failure_stage,
            "failure_type": failure_type,
            "timeout": failure_type == "timeout",
            "retry_count": retry_count or 0,
            "retry_exhausted": bool(diagnostics.get("retry_exhausted")),
            "from_cache": bool(result.get("from_cache")),
            "human_review": bool(result.get("requires_human_review")),
            "clarification_pending": status == "need_clarification",
            "recorded_at": time.time(),
        }
        return card

    def record_completed(self, trace_id, result=None):
        card = self.summarize_request(trace_id, result)
        self._requests.append(card)
        if len(self._requests) > self.max_recent:
            self._requests = self._requests[-self.max_recent:]
        return dict(card)

    def dashboard(self):
        items = list(self._requests)
        total = len(items)
        failures = [x for x in items if x.get("status") == "error"]
        timeouts = [x for x in items if x.get("timeout")]
        latencies = sorted([x.get("elapsed_ms") for x in items if isinstance(x.get("elapsed_ms"), (int, float))])
        by_stage = {}
        by_intent = {}
        for item in items:
            if item.get("failure_stage"):
                by_stage[item["failure_stage"]] = by_stage.get(item["failure_stage"], 0) + 1
            key = item.get("intent") or "unknown"
            by_intent[key] = by_intent.get(key, 0) + 1
        def percentile(p):
            if not latencies:
                return None
            index = int((len(latencies) - 1) * p)
            return latencies[index]
        return {
            "contract": MONITORING_CONTRACT,
            "total_requests": total,
            "success_rate": float(total - len(failures)) / total if total else None,
            "error_rate": float(len(failures)) / total if total else None,
            "timeout_count": len(timeouts),
            "latency_ms": {"p50": percentile(0.50), "p95": percentile(0.95), "max": max(latencies) if latencies else None},
            "failure_stages": by_stage,
            "intent_distribution": by_intent,
            "recent_requests": items[-20:],
        }


__all__ = ["AgentMonitoring", "MONITORING_CONTRACT"]
