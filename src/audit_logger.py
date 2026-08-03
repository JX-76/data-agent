# -*- coding: utf-8 -*-
"""Append-only audit logger for Data Agent governance events."""

import hashlib
import json
import os
import time


DEFAULT_AUDIT_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, "sessions", "audit.jsonl"))


try:
    unicode
except NameError:  # pragma: no cover - Python 3 compatibility
    unicode = str


def _hash_sql(sql):
    if not sql:
        return None
    data = sql.encode("utf-8") if isinstance(sql, unicode) else str(sql).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _serialize_event(event):
    payload = json.dumps(event, ensure_ascii=False, sort_keys=True)
    if isinstance(payload, unicode):
        payload = payload.encode("utf-8")
    return payload


class AuditLogger(object):
    def __init__(self, path=None):
        self.path = path or os.environ.get("DATA_AGENT_AUDIT_PATH") or DEFAULT_AUDIT_PATH
        parent = os.path.dirname(os.path.abspath(self.path))
        if parent and not os.path.exists(parent):
            os.makedirs(parent)

    def log_query(self, user_id, query, status="allow", sql=None, blocked_reason=None, trace_id="", details=None, intent=None):
        event = {
            "timestamp": int(time.time()),
            "event_type": "query",
            "user_id": user_id,
            "query": query,
            "status": status or intent or "unknown",
            "sql_hash": _hash_sql(sql),
            "blocked_reason": blocked_reason,
            "trace_id": trace_id,
            "details": details or {},
        }
        return self.write(event)

    def log(self, event_type, user_id, action, resource, details=None):
        event = {
            "timestamp": int(time.time()),
            "event_type": event_type,
            "user_id": user_id,
            "action": action,
            "resource": resource,
            "details": details or {},
        }
        return self.write(event)

    def log_event(self, event_type, payload=None):
        event = dict(payload or {})
        event.setdefault("timestamp", int(time.time()))
        event.setdefault("event_type", event_type)
        return self.write(event)

    def write(self, event):
        try:
            f = open(self.path, "a", encoding="utf-8")
        except TypeError:
            # Python 2 fallback: write bytes explicitly to avoid unicode/str mixing.
            f = open(self.path, "ab")
            try:
                f.write(_serialize_event(event) + b"\n")
                return event
            finally:
                f.close()
        try:
            f.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        finally:
            f.close()
        return event


audit_logger = AuditLogger()


__all__ = ["AuditLogger", "audit_logger", "DEFAULT_AUDIT_PATH"]
