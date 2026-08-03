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

import audit
import server
from session import Turn


class _DummyRequest(object):
    def __init__(self, headers=None):
        self.headers = headers or {}
        self.client = type("Client", (), {"host": "127.0.0.1"})()


def _headers(user="u1", tenant="tenant-a", roles="analyst"):
    return {"x-dev-user-id": user, "x-dev-tenant-id": tenant, "x-dev-roles": roles}


def _json_response(resp):
    return json.loads(resp.body.decode("utf-8"))


def setup_function(_fn):
    server.session_manager = None
    server._session_access_index.clear()


def test_session_history_returns_metadata_only_and_masks_sensitive_content():
    created = asyncio.run(server.create_session(_DummyRequest(_headers("u1", "tenant-a")), None))
    sid = created["session_id"]
    sm = server._get_session_manager()
    sm.sessions[sid].turns.append(Turn(
        query="用户手机号 13812345678 的订单 SQL",
        result={
            "status": "ok",
            "answer": "手机号 13812345678 的 GMV 为 100",
            "sql": "select phone, gmv from orders where phone='13812345678'",
            "results": [{"phone": "13812345678", "gmv": 100}],
            "metric": "gmv",
            "trace_id": "trace_safe",
            "evidence_ids": ["ev_safe"],
        },
    ))

    payload = asyncio.run(server.session_history(sid, _DummyRequest(_headers("u1", "tenant-a")), None))
    dumped = json.dumps(payload, ensure_ascii=False)
    assert payload["contract"] == "legacy_session_metadata_v1"
    assert payload["status"] == "ok"
    assert payload["turn_count"] == 1
    assert payload["turns"][0]["trace_id"] == "trace_safe"
    assert payload["turns"][0]["evidence_ids"] == ["ev_safe"]
    assert "13812345678" not in dumped
    assert "select phone" not in dumped.lower()
    assert "手机号" not in dumped
    assert "results" not in payload["turns"][0]


def test_session_history_cross_user_or_tenant_is_blocked_without_leaking_existence():
    created = asyncio.run(server.create_session(_DummyRequest(_headers("u1", "tenant-a")), None))
    sid = created["session_id"]

    resp = asyncio.run(server.session_history(sid, _DummyRequest(_headers("u2", "tenant-a")), None))
    payload = _json_response(resp)
    assert payload["contract"] == "release_v1_envelope"
    assert payload["status"] == "blocked"
    assert payload["raw"]["blocked_reason"] == "session_access_denied"

    resp = asyncio.run(server.session_history(sid, _DummyRequest(_headers("u1", "tenant-b")), None))
    payload = _json_response(resp)
    assert payload["status"] == "blocked"
    assert payload["raw"]["blocked_reason"] == "session_access_denied"


def test_admin_same_tenant_can_read_owner_metadata_but_not_raw_history():
    created = asyncio.run(server.create_session(_DummyRequest(_headers("u1", "tenant-a")), None))
    sid = created["session_id"]
    sm = server._get_session_manager()
    sm.sessions[sid].turns.append(Turn(query="secret query", result={"status": "error", "reason": "boom"}))

    payload = asyncio.run(server.session_history(sid, _DummyRequest(_headers("admin", "tenant-a", "admin")), None))
    dumped = json.dumps(payload, ensure_ascii=False)
    assert payload["status"] == "ok"
    assert payload["owner_user_id"] == "u1"
    assert "secret query" not in dumped


def test_delete_session_enforces_owner_scope():
    created = asyncio.run(server.create_session(_DummyRequest(_headers("u1", "tenant-a")), None))
    sid = created["session_id"]

    blocked = asyncio.run(server.delete_session(sid, _DummyRequest(_headers("u2", "tenant-a")), None))
    blocked_payload = _json_response(blocked)
    assert blocked_payload["status"] == "blocked"
    assert sid in server._get_session_manager().sessions

    deleted = asyncio.run(server.delete_session(sid, _DummyRequest(_headers("u1", "tenant-a")), None))
    assert deleted["contract"] == "legacy_session_metadata_v1"
    assert deleted["deleted"] is True
    assert sid not in server._get_session_manager().sessions


class _FakeAudit(object):
    def query(self, identity=None, status=None, since=None, limit=100):
        rows = [
            {
                "timestamp": "2026-07-26T00:00:00",
                "event_type": "query",
                "user_id": identity or "u1",
                "action": "execute",
                "resource": "query",
                "event_hash": "hash1",
                "details": {
                    "status": "ok",
                    "trace_id": "trace_audit",
                    "query": "手机号 13812345678",
                    "sql": "select phone from users",
                    "token": "secret=abc",
                    "audit_id": "audit1",
                },
            }
        ]
        if status:
            rows = [r for r in rows if r.get("details", {}).get("status") == status]
        return rows[:limit]


def test_audit_endpoint_is_metadata_only_and_sanitized(monkeypatch):
    monkeypatch.setattr(audit, "get_audit", lambda: _FakeAudit())
    payload = asyncio.run(server.audit_log(_DummyRequest(_headers("u1", "tenant-a")), None, None, None, 20, None))
    dumped = json.dumps(payload, ensure_ascii=False)
    assert payload["contract"] == "legacy_audit_metadata_v1"
    assert payload["status"] == "ok"
    assert payload["entries"][0]["trace_id"] == "trace_audit"
    assert "query" not in payload["entries"][0]["details"]
    assert "sql" not in payload["entries"][0]["details"]
    assert "13812345678" not in dumped
    assert "select phone" not in dumped.lower()
    assert "abc" not in dumped


def test_audit_identity_filter_cannot_read_other_user_without_admin(monkeypatch):
    monkeypatch.setattr(audit, "get_audit", lambda: _FakeAudit())
    resp = asyncio.run(server.audit_log(_DummyRequest(_headers("u1", "tenant-a")), "u2", None, None, 20, None))
    payload = _json_response(resp)
    assert payload["contract"] == "release_v1_envelope"
    assert payload["status"] == "blocked"
    assert payload["raw"]["blocked_reason"] == "audit_identity_access_denied"
