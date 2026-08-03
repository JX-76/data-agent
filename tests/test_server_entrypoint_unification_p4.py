# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import asyncio
import json
import os
import sys

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
