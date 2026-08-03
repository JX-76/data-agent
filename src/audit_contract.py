# -*- coding: utf-8 -*-
"""Normalized R23 governance audit event contract."""
from __future__ import unicode_literals
import time


def build_governance_audit_event(access_context=None, query="", task_id=None,
                                 trace_id=None, tables=None, fields=None,
                                 decision="allowed", reason=""):
    context = dict(access_context or {})
    return {
        "timestamp": int(time.time()),
        "event_type": "governance_decision",
        "user_id": context.get("user_id") or "anonymous",
        "role": context.get("role") or "anonymous",
        "tenant_id": context.get("tenant_id") or "default",
        "query": query or "",
        "task_id": task_id,
        "trace_id": trace_id,
        "tables": list(tables or []),
        "fields": list(fields or []),
        "decision": decision,
        "reason": reason or "",
        "contract": "governance_audit_v1",
    }


__all__ = ["build_governance_audit_event"]
