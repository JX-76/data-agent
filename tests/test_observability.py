# -*- coding: utf-8 -*-
"""Tests for observability helpers."""

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, "src"))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def test_observation_recorder_summarize():
    from observability import ObservationRecorder

    recorder = ObservationRecorder()
    recorder.record("route", trace_id="t1", status="ok", phase="route")
    recorder.record("execute", trace_id="t1", status="error", phase="execute")
    summary = recorder.summarize("t1")

    assert summary["trace_id"] == "t1"
    assert summary["event_count"] == 2
    assert summary["event_names"]["route"] == 1
    assert summary["status_counts"]["error"] == 1
    assert summary["failed"] is True
    assert summary["failure_stage"] == "execute"
    assert summary["failure_type"] == "error"


def test_observation_event_standard_fields():
    from observability import ObservationRecorder

    recorder = ObservationRecorder()
    recorder.record(
        "governance",
        trace_id="t2",
        status="blocked",
        stage="governance",
        failure_type="dangerous_query",
        task_id="task-1",
        session_id="session-1",
        metadata={"policy_id": "governance.dangerous_query"},
    )
    event = recorder.events_as_dicts("t2")[0]
    summary = recorder.summarize("t2")

    assert event["stage"] == "governance"
    assert event["failure_type"] == "dangerous_query"
    assert event["task_id"] == "task-1"
    assert event["session_id"] == "session-1"
    assert event["metadata"]["policy_id"] == "governance.dangerous_query"
    assert summary["failed"] is True
    assert summary["failure_stage"] == "governance"
    assert summary["failure_type"] == "dangerous_query"
    assert summary["task_id"] == "task-1"


if __name__ == "__main__":
    test_observation_recorder_summarize()
    test_observation_event_standard_fields()
    print("All observability tests passed!")
