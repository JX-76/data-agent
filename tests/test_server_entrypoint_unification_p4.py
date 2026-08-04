# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import asyncio
import datetime
import json
import os
import re
import sys
import tempfile
import threading
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import release_api
import server
from server import QueryRequest


class _DummyRequest(object):
    def __init__(self, headers=None):
        self.headers = headers or {}
        self.client = type("Client", (), {"host": "127.0.0.1"})()


def _release_env(status="ok", query="最近7天GMV", trace_id="trace_server_test"):
    result = {
        "status": status,
        "summary": "done" if status == "ok" else "blocked",
        "results": [{"metric": "gmv", "value": 1}],
        "trace_id": trace_id,
        "tenant_id": "tenant-a",
    }
    return release_api._envelope(query, "sid", result, 0, "audit_server_test")


def test_legacy_query_returns_release_envelope(monkeypatch):
    def fake_ask_release(query, session_id=None, use_llm=False, headers=None):
        assert headers.get("x-dev-tenant-id") == "tenant-a"
        return _release_env("ok", query=query)

    monkeypatch.setattr(release_api, "ask_release", fake_ask_release)
    resp = asyncio.run(server.query(
        QueryRequest(query="最近7天GMV", session_id="sid", use_llm=False),
        _DummyRequest(headers={"x-dev-tenant-id": "tenant-a"}),
        None,
    ))
    payload = json.loads(resp.body.decode("utf-8"))
    assert payload["contract"] == "release_v1_envelope"
    assert payload["status"] == "ok"
    assert payload["terminal"] == "ok"
    assert payload["legacy_endpoint"] == "/query"
    assert "answer" in payload
    assert "quality" in payload


def test_legacy_query_serializes_datetime_in_release_envelope(monkeypatch):
    def fake_ask_release(query, session_id=None, use_llm=False, headers=None):
        env = _release_env("ok", query=query)
        env["raw"]["provider_metadata"] = {"completed_at": datetime.datetime(2026, 8, 4, 15, 0, 0)}
        return env

    monkeypatch.setattr(release_api, "ask_release", fake_ask_release)
    resp = asyncio.run(server.query(
        QueryRequest(query="最近7天GMV", session_id="sid", use_llm=False),
        _DummyRequest(),
        None,
    ))
    payload = json.loads(resp.body.decode("utf-8"))
    assert resp.status_code == 200
    assert payload["status"] == "ok"
    assert payload["raw"]["provider_metadata"]["completed_at"].startswith("2026-08-04T15:00:00")


def test_legacy_query_exception_returns_structured_release_error(monkeypatch):
    def fake_ask_release(query, session_id=None, use_llm=False, headers=None):
        raise RuntimeError("legacy boom")

    monkeypatch.setattr(release_api, "ask_release", fake_ask_release)
    resp = asyncio.run(server.query(
        QueryRequest(query="最近7天GMV", session_id="sid", use_llm=False),
        _DummyRequest(),
        None,
    ))
    payload = json.loads(resp.body.decode("utf-8"))
    assert resp.status_code == 200
    assert payload["contract"] == "release_v1_envelope"
    assert payload["status"] == "error"
    assert payload["terminal"] == "error"
    assert payload["raw"]["error"]["code"] == "server_runtime_exception"
    assert payload["raw"]["authority"] == "unverified"


async def _collect_stream_text(response):
    chunks = []
    async for chunk in response.body_iterator:
        if isinstance(chunk, bytes):
            chunks.append(chunk.decode("utf-8"))
        else:
            chunks.append(chunk)
    return "".join(chunks)


def _wait_for(predicate, timeout_seconds=2.0):
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


def test_stream_submission_executes_without_sse_consumption(monkeypatch):
    started = threading.Event()
    release = threading.Event()

    def fake_ask_release(query, session_id=None, use_llm=False, headers=None):
        started.set()
        assert release.wait(2.0)
        return _release_env("ok", query=query, trace_id="trace_detached_stream")

    monkeypatch.setattr(release_api, "ask_release", fake_ask_release)
    before = set(server._STREAM_TASKS.keys())
    response = asyncio.run(server.query_stream(
        QueryRequest(query="最近7天GMV", session_id="sid-detached", use_llm=False),
        _DummyRequest(),
        None,
    ))
    assert response.media_type == "text/event-stream"
    task_ids = set(server._STREAM_TASKS.keys()) - before
    assert len(task_ids) == 1
    task_id = task_ids.pop()
    assert started.wait(1.0), "local durable worker did not start without SSE consumption"
    running = json.loads(asyncio.run(server.task_status(task_id, _DummyRequest(), None)).body.decode("utf-8"))
    assert running["state"] == "running"
    release.set()
    assert _wait_for(lambda: json.loads(asyncio.run(server.task_status(task_id, _DummyRequest(), None)).body.decode("utf-8"))["state"] == "succeeded")


def test_stream_task_status_and_replay_are_owner_scoped(monkeypatch):
    owner_headers = {"x-dev-user-id": "owner", "x-dev-tenant-id": "tenant-a"}

    def fake_ask_release(query, session_id=None, use_llm=False, headers=None):
        return _release_env("ok", query=query, trace_id="trace_owner_scope")

    monkeypatch.setattr(release_api, "ask_release", fake_ask_release)
    initial = asyncio.run(server.query_stream(
        QueryRequest(query="最近7天GMV", session_id="sid-owner", use_llm=False),
        _DummyRequest(headers=owner_headers),
        None,
    ))
    text = asyncio.run(_collect_stream_text(initial))
    task_id = re.search(r'"task_id": "([^"]+)"', text).group(1)

    owner_status = asyncio.run(server.task_status(
        task_id, _DummyRequest(headers=owner_headers), None))
    assert owner_status.status_code == 200
    owner_payload = json.loads(owner_status.body.decode("utf-8"))
    assert owner_payload["requester_scope"] == {
        "tenant_id": "tenant-a", "user_id": "owner", "role": "analyst"}

    other_user = asyncio.run(server.task_status(
        task_id, _DummyRequest(headers={"x-dev-user-id": "other", "x-dev-tenant-id": "tenant-a"}), None))
    other_tenant = asyncio.run(server.resume_task_stream(
        task_id, _DummyRequest(headers={"x-dev-user-id": "owner", "x-dev-tenant-id": "tenant-b"}), None))
    assert other_user.status_code == 404
    assert other_tenant.status_code == 404
    assert json.loads(other_user.body.decode("utf-8"))["error"]["code"] == "task_not_found"

    admin_same_tenant = asyncio.run(server.task_status(
        task_id, _DummyRequest(headers={"x-dev-user-id": "admin-user", "x-dev-tenant-id": "tenant-a", "x-dev-roles": "admin"}), None))
    assert admin_same_tenant.status_code == 200


def test_stream_replay_respects_last_event_id_and_marks_replayed_events(monkeypatch):
    def fake_ask_release(query, session_id=None, use_llm=False, headers=None):
        return _release_env("ok", query=query, trace_id="trace_replay")

    monkeypatch.setattr(release_api, "ask_release", fake_ask_release)
    response = asyncio.run(server.query_stream(
        QueryRequest(query="最近7天GMV", session_id="sid-replay", use_llm=False),
        _DummyRequest(),
        None,
    ))
    text = asyncio.run(_collect_stream_text(response))
    task_id = re.search(r'"task_id": "([^"]+)"', text).group(1)
    assert "id: 0" in text
    assert "id: 1" in text
    assert "id: 2" in text

    replay = asyncio.run(server.resume_task_stream(
        task_id, _DummyRequest(headers={"last-event-id": "1",
                                        "x-dev-user-id": "release_user",
                                        "x-dev-tenant-id": "default"}), None))
    replay_text = asyncio.run(_collect_stream_text(replay))
    assert "id: 2" in replay_text
    assert "id: 1" not in replay_text
    assert '"replay": true' in replay_text
    assert "[DONE]" in replay_text


def test_stream_replay_without_memory_sidecar_is_explicitly_unavailable(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    try:
        server._STREAM_TASKS.clear()
        server._reset_stream_task_control_for_tests(path)

        def fake_ask_release(query, session_id=None, use_llm=False, headers=None):
            return _release_env("ok", query=query, trace_id="trace_replay_missing")

        monkeypatch.setattr(release_api, "ask_release", fake_ask_release)
        initial = asyncio.run(server.query_stream(
            QueryRequest(query="最近7天GMV", session_id="sid-replay-missing", use_llm=False),
            _DummyRequest(),
            None,
        ))
        text = asyncio.run(_collect_stream_text(initial))
        task_id = re.search(r'"task_id": "([^"]+)"', text).group(1)
        server._STREAM_TASKS.clear()
        server._reset_stream_task_control_for_tests(path)

        replay = asyncio.run(server.resume_task_stream(
        task_id, _DummyRequest(headers={"last-event-id": "not-a-number",
                                        "x-dev-user-id": "release_user",
                                        "x-dev-tenant-id": "default"}), None))
        replay_text = asyncio.run(_collect_stream_text(replay))
        assert "replay_unavailable" in replay_text
        assert "process_local_event_sidecar_missing" in replay_text
        assert '"status_url": "/tasks/' in replay_text
    finally:
        server._STREAM_TASKS.clear()
        server._reset_stream_task_control_for_tests("")
        if os.path.exists(path):
            os.remove(path)


def test_legacy_stream_complete_event_uses_release_envelope(monkeypatch):
    def fake_ask_release(query, session_id=None, use_llm=False, headers=None):
        return _release_env("ok", query=query)

    monkeypatch.setattr(release_api, "ask_release", fake_ask_release)
    response = asyncio.run(server.query_stream(
        QueryRequest(query="最近7天GMV", session_id="sid", use_llm=False),
        _DummyRequest(),
        None,
    ))
    text = asyncio.run(_collect_stream_text(response))
    assert "release_v1_envelope" in text
    assert '"type": "complete"' in text
    assert '"legacy_endpoint": "/query/stream"' in text
    assert "[DONE]" in text
    task_id = re.search(r'"task_id": "([^"]+)"', text).group(1)
    status = asyncio.run(server.task_status(task_id, _DummyRequest(), None))
    payload = json.loads(status.body.decode("utf-8"))
    assert payload["contract"] == "stream_task_v2"
    assert payload["state"] == "succeeded"
    assert payload["task_type"] == "release_query_stream"
    assert payload["execution_receipt"]["contract"] == "stream_release_receipt_v1"


def test_legacy_stream_error_event_is_structured_envelope(monkeypatch):
    def fake_ask_release(query, session_id=None, use_llm=False, headers=None):
        raise RuntimeError("stream boom")

    monkeypatch.setattr(release_api, "ask_release", fake_ask_release)
    response = asyncio.run(server.query_stream(
        QueryRequest(query="最近7天GMV", session_id="sid", use_llm=False),
        _DummyRequest(),
        None,
    ))
    text = asyncio.run(_collect_stream_text(response))
    assert "release_v1_envelope" in text
    assert '"type": "error"' in text
    assert "server_stream_exception" in text
    assert "unverified" in text
    task_id = re.search(r'"task_id": "([^"]+)"', text).group(1)
    status = asyncio.run(server.task_status(task_id, _DummyRequest(), None))
    payload = json.loads(status.body.decode("utf-8"))
    assert payload["contract"] == "stream_task_v2"
    assert payload["state"] == "failed"
    assert payload["error_code"] == "server_stream_exception"
    assert payload["safe_error_summary"]


def test_stream_task_status_survives_control_plane_rebuild_with_sqlite(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    try:
        server._STREAM_TASKS.clear()
        server._reset_stream_task_control_for_tests(path)

        def fake_ask_release(query, session_id=None, use_llm=False, headers=None):
            return _release_env("ok", query=query, trace_id="trace_sqlite_stream")

        monkeypatch.setattr(release_api, "ask_release", fake_ask_release)
        response = asyncio.run(server.query_stream(
            QueryRequest(query="最近7天GMV", session_id="sid-sqlite", use_llm=False),
            _DummyRequest(),
            None,
        ))
        text = asyncio.run(_collect_stream_text(response))
        task_id = re.search(r'"task_id": "([^"]+)"', text).group(1)

        # Simulate process-local sidecar loss while retaining durable SQLite state.
        server._STREAM_TASKS.clear()
        server._reset_stream_task_control_for_tests(path)
        status = asyncio.run(server.task_status(task_id, _DummyRequest(), None))
        payload = json.loads(status.body.decode("utf-8"))
        assert payload["contract"] == "stream_task_v2"
        assert payload["state"] == "succeeded"
        assert payload["case_id"] == "sid-sqlite"
        assert payload["input_ref"]["query_hash"]
        assert "最近7天GMV" not in repr(payload.get("input_ref"))
        assert payload.get("result") is None
    finally:
        server._STREAM_TASKS.clear()
        server._reset_stream_task_control_for_tests("")
        if os.path.exists(path):
            os.remove(path)


def test_legacy_resume_blocks_missing_session_with_release_envelope(monkeypatch):
    def fake_resume_release(session_id, choice_id, headers=None):
        return _release_env("need_clarification", query="resume:%s" % choice_id)

    monkeypatch.setattr(release_api, "resume_release", fake_resume_release)
    resp = asyncio.run(server.session_resume("missing-session", server.ResumeRequest(session_id="missing-session", choice_id="c1"), None, None))
    payload = json.loads(resp.body.decode("utf-8"))
    assert payload["contract"] == "release_v1_envelope"
    assert payload["status"] == "blocked"
    assert payload["raw"]["blocked_reason"] == "session_access_denied"
    assert payload["legacy_endpoint"] == "/sessions/{session_id}/resume"
