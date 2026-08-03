# -*- coding: utf-8 -*-
"""Trace helpers for external tool calls."""
from __future__ import unicode_literals

import hashlib
import json
import time
import uuid

try:  # pragma: no cover - Python 3 compatibility
    unicode
except NameError:
    unicode = str


def stable_hash(value):
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True)
    except Exception:
        raw = unicode(value)

    if not isinstance(raw, bytes):
        raw = raw.encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def redact_args(args):
    args = dict(args or {})
    clean = {}
    for key, value in args.items():
        low = str(key).lower()
        if "password" in low or "token" in low or "secret" in low:
            clean[key] = "***"
        elif key == "sql":
            text = value or ""
            clean["sql_preview"] = text[:160]
            clean["sql_hash"] = stable_hash(text)
        else:
            clean[key] = value
    return clean


class ExternalToolTraceRecorder(object):
    def __init__(self, observer=None):
        self.observer = observer
        self.events = []

    def record(self, trace_id, tool_id, status, args=None, output=None, spec=None,
               policy=None, started_at=None, error=None, failure_type=None,
               tool_plan=None, dag_event=None):
        now = time.time()
        latency_ms = int((now - started_at) * 1000) if started_at else None
        event = {
            "trace_id": trace_id or uuid.uuid4().hex,
            "name": "external_tool_call",
            "stage": "external_tool_call",
            "tool_id": tool_id,
            "call_id": "etc_%s" % uuid.uuid4().hex[:8],
            "status": status,
            "input_hash": stable_hash(args or {}),
            "sanitized_input": redact_args(args or {}),
            "output_hash": stable_hash(output or {}),
            "output_summary": self._summarize_output(output),
            "latency_ms": latency_ms,
            "timeout_ms": (spec or {}).get("timeout_ms"),
            "risk_level": (spec or {}).get("risk_level"),
            "side_effect": (spec or {}).get("side_effect"),
            "policy_decision": "allow" if (policy or {}).get("allowed", True) else "deny",
            "failure_type": failure_type,
            "error": error,
            "tool_plan": tool_plan or {},
            "dag_event": dag_event or {},
        }
        self.events.append(event)
        if self.observer is not None and hasattr(self.observer, "record"):
            payload = dict(event)
            name = payload.pop("name")
            status_value = payload.pop("status")
            trace_value = payload.pop("trace_id")
            self.observer.record(name, trace_id=trace_value, status=status_value, **payload)
        return event

    def _summarize_output(self, output):
        output = output or {}
        if not isinstance(output, dict):
            return {"type": type(output).__name__}
        summary = {}
        for key in ["row_count", "source", "semantic_version", "suite"]:
            if key in output:
                summary[key] = output.get(key)
        if "rows" in output:
            summary["row_count"] = output.get("row_count", len(output.get("rows") or []))
        if "schema" in output:
            schema = output.get("schema") or {}
            summary["tables"] = sorted(schema.keys()) if isinstance(schema, dict) else []
        if "metrics" in output:
            summary["metric_count"] = len(output.get("metrics") or [])
        if "dimensions" in output:
            summary["dimension_count"] = len(output.get("dimensions") or [])
        return summary


__all__ = ["ExternalToolTraceRecorder", "stable_hash", "redact_args"]
