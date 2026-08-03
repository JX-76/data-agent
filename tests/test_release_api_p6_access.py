# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import release_api
from release_api import ask_release, followup_release, resume_release, release_history, release_quality_trend, release_recent_audit


class _DummyFacade(object):
    def __init__(self):
        self.access_context = None
        self.followup_called = False
        self.resume_called = False

    def ask(self, query, use_llm=False, access_context=None, analysis_method=None):
        self.access_context = access_context
        return {"status": "ok", "summary": "ok", "results": [{"v": 1}], "tenant_id": access_context.get("tenant_id")}

    def follow_up(self, query, use_llm=False, analysis_method=None):
        self.followup_called = True
        return {"status": "ok", "summary": "follow", "results": [{"v": 2}], "tenant_id": self.access_context.get("tenant_id")}

    def resume_clarification(self, choice_id):
        self.resume_called = True
        return {"status": "ok", "summary": "resume", "results": [{"v": 3}], "tenant_id": self.access_context.get("tenant_id")}


def _headers(user, tenant, roles="analyst"):
    return {"x-dev-user-id": user, "x-dev-tenant-id": tenant, "x-dev-roles": roles}


def test_release_followup_and_resume_enforce_session_owner(monkeypatch):
    facade = _DummyFacade()
    monkeypatch.setattr(release_api, "_facade", lambda session_id: (session_id or "sid", facade))
    release_api._SESSION_ACCESS_INDEX.clear()

    env = ask_release("最近7天GMV", session_id="p6_owner", headers=_headers("alice", "tenant-a"))
    assert env["status"] == "ok"

    denied = followup_release("继续看", session_id="p6_owner", headers=_headers("bob", "tenant-a"))
    assert denied["status"] == "blocked"
    assert denied["raw"]["blocked_reason"] == "session_access_denied"
    assert facade.followup_called is False

    denied_resume = resume_release("p6_owner", "choice-1", headers=_headers("bob", "tenant-a"))
    assert denied_resume["status"] == "blocked"
    assert denied_resume["raw"]["blocked_reason"] == "session_access_denied"
    assert facade.resume_called is False


def test_release_admin_can_resume_same_tenant_but_not_cross_tenant(monkeypatch):
    facade = _DummyFacade()
    monkeypatch.setattr(release_api, "_facade", lambda session_id: (session_id or "sid", facade))
    release_api._SESSION_ACCESS_INDEX.clear()

    ask_release("最近7天GMV", session_id="p6_admin", headers=_headers("alice", "tenant-a"))
    ok = resume_release("p6_admin", "choice-1", headers=_headers("admin", "tenant-a", "admin"))
    assert ok["status"] == "ok"
    assert facade.resume_called is True

    cross = followup_release("继续", session_id="p6_admin", headers=_headers("admin", "tenant-b", "admin"))
    assert cross["status"] == "blocked"
    assert cross["raw"]["blocked_reason"] == "session_access_denied"


def test_release_history_and_quality_are_tenant_scoped_for_non_admin(monkeypatch):
    facade = _DummyFacade()
    monkeypatch.setattr(release_api, "_facade", lambda session_id: (session_id or "sid", facade))
    release_api._HISTORY[:] = []
    release_api._SESSION_ACCESS_INDEX.clear()

    ask_release("A", session_id="p6_ta", headers=_headers("alice", "tenant-a"))
    ask_release("B", session_id="p6_tb", headers=_headers("bob", "tenant-b"))

    tenant_a_access = release_api._resolve_access_context(headers=_headers("alice", "tenant-a"))
    hist = release_history(access_context=tenant_a_access)
    assert hist["total"] == 1
    assert hist["items"][0]["tenant_id"] == "tenant-a"

    trend = release_quality_trend("tenant-b", access_context=tenant_a_access)
    assert trend["tenant_id"] == "tenant-a"
    assert trend["total"] == 1

    admin = release_api._resolve_access_context(headers=_headers("admin", "tenant-a", "admin"))
    admin_trend = release_quality_trend("tenant-b", access_context=admin)
    assert admin_trend["tenant_id"] == "tenant-b"
    assert admin_trend["total"] == 1


def test_release_recent_audit_is_metadata_only_and_user_scoped(monkeypatch, tmp_path):
    audit_path = tmp_path / "audit.jsonl"
    monkeypatch.setattr(release_api.audit_logger, "path", str(audit_path))
    audit_path.write_text(
        '{"user_id":"alice","query":"show email alice@example.com","sql":"select * from users","details":{"raw":{"x":1},"trace_id":"t1"},"status":"ok"}\n'
        '{"user_id":"bob","query":"secret","sql":"select token from users","details":{"prompt":"p"},"status":"ok"}\n',
        encoding="utf-8",
    )
    alice = release_api._resolve_access_context(headers=_headers("alice", "tenant-a"))
    env = release_recent_audit(access_context=alice)
    assert env["total"] == 1
    text = repr(env)
    assert "alice@example.com" not in text
    assert "select" not in text.lower()
    assert "secret" not in text
    assert "raw" not in text
