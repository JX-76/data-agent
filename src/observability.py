# -*- coding: utf-8 -*-
"""Unified observability helpers.

This module provides a single lightweight event recorder so routing, memory,
execution, and evaluation can emit structured events without binding to a
specific vendor tracer.
"""

import time
import uuid


class ObservationEvent(object):
    def __init__(self, trace_id, name, status="ok", payload=None, timestamp=None):
        self.trace_id = trace_id
        self.name = name
        self.status = status
        self.payload = payload or {}
        self.timestamp = timestamp if timestamp is not None else time.time()

    def to_dict(self):
        stage = self.payload.get("stage") or self.payload.get("phase") or self.name
        elapsed_ms = self.payload.get("elapsed_ms")
        if elapsed_ms is None:
            elapsed_ms = self.payload.get("latency_ms")
        return {
            "trace_id": self.trace_id,
            "name": self.name,
            "stage": stage,
            "status": self.status,
            "payload": dict(self.payload),
            "metadata": dict(self.payload.get("metadata") or {}),
            "timestamp": self.timestamp,
            "phase": self.payload.get("phase"),
            "step": self.payload.get("step"),
            "latency_ms": self.payload.get("latency_ms"),
            "elapsed_ms": elapsed_ms,
            "failure_type": self.payload.get("failure_type"),
            "task_id": self.payload.get("task_id"),
            "session_id": self.payload.get("session_id"),
        }


class ObservationRecorder(object):
    def __init__(self):
        self._events = []

    def record(self, name, trace_id=None, status="ok", **payload):
        event = ObservationEvent(trace_id=trace_id or uuid.uuid4().hex, name=name, status=status, payload=dict(payload))
        self._events.append(event)
        return event

    def events(self, trace_id=None):
        if trace_id is None:
            return list(self._events)
        return [e for e in self._events if e.trace_id == trace_id]

    def events_as_dicts(self, trace_id=None):
        return [e.to_dict() for e in self.events(trace_id=trace_id)]

    def summarize(self, trace_id=None):
        events = self.events(trace_id=trace_id)
        counts = {}
        statuses = {}
        failure_stage = None
        failure_type = None
        task_id = None
        session_id = None
        first_timestamp = None
        last_timestamp = None
        for event in events:
            data = event.to_dict()
            counts[event.name] = counts.get(event.name, 0) + 1
            statuses[event.status] = statuses.get(event.status, 0) + 1
            first_timestamp = event.timestamp if first_timestamp is None else min(first_timestamp, event.timestamp)
            last_timestamp = event.timestamp if last_timestamp is None else max(last_timestamp, event.timestamp)
            task_id = task_id or data.get("task_id")
            session_id = session_id or data.get("session_id")
            if event.status in ("error", "failed", "blocked") and failure_stage is None:
                failure_stage = data.get("stage") or event.name
                failure_type = data.get("failure_type") or event.status
        duration_ms = None
        if first_timestamp is not None and last_timestamp is not None:
            duration_ms = int((last_timestamp - first_timestamp) * 1000)
        return {
            "trace_id": trace_id,
            "event_count": len(events),
            "event_names": counts,
            "status_counts": statuses,
            "failed": failure_stage is not None,
            "failure_stage": failure_stage,
            "failure_type": failure_type,
            "task_id": task_id,
            "session_id": session_id,
            "first_timestamp": first_timestamp,
            "last_timestamp": last_timestamp,
            "duration_ms": duration_ms,
        }

    def clear(self):
        self._events[:] = []


_OBSERVER = ObservationRecorder()


def get_observer():
    return _OBSERVER


# Backward-compatible alias for agent_facade.py
Observer = ObservationRecorder

__all__ = ["Observer", "ObservationEvent", "ObservationRecorder", "get_observer"]


