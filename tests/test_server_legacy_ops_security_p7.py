# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import asyncio
import json
import os
import sys
import types

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import server


class _DummyRequest(object):
    def __init__(self, headers=None):
        self.headers = headers or {}
        self.client = type("Client", (), {"host": "127.0.0.1"})()


def _headers(user="u1", tenant="tenant-a", roles="analyst"):
    return {"x-dev-user-id": user, "x-dev-tenant-id": tenant, "x-dev-roles": roles}


def _json_response(resp):
    return json.loads(resp.body.decode("utf-8"))


def test_legacy_observability_endpoints_are_admin_only_for_non_admin():
    req = _DummyRequest(_headers("u1", "tenant-a", "analyst"))
    calls = [
        server.clear_cache(req, None),
        server.cost_report(req, 24, None),
        server.mask_rules(req, None),
        server.fallback_stats(req, None),
        server.prompt_info("sql_agent", req, None),
        server.prompt_rollback("sql_agent", req, None),
        server.router_benchmark(req, None),
    ]
    for coro in calls:
        payload = _json_response(asyncio.run(coro))
        assert payload["contract"] == "release_v1_envelope"
        assert payload["status"] == "blocked"
        assert payload["raw"]["blocked_reason"] == "admin_required"


def test_prompt_info_admin_returns_metadata_not_prompt_body(monkeypatch):
    class _PromptManager(object):
        def history(self, name):
            return [{"version": "v1", "prompt": "SECRET SYSTEM PROMPT", "template": "select token", "author": "ops"}]

        def get_prompt(self, name):
            return "SECRET SYSTEM PROMPT with token abc123 and few-shot examples"

    fake_module = types.SimpleNamespace(get_prompt_manager=lambda: _PromptManager())
    monkeypatch.setitem(sys.modules, "prompt_manager", fake_module)

    payload = asyncio.run(server.prompt_info("sql_agent", _DummyRequest(_headers("admin", "tenant-a", "admin")), None))
    dumped = json.dumps(payload, ensure_ascii=False)
    assert payload["contract"] == "legacy_prompt_metadata_v1"
    assert payload["status"] == "ok"
    assert "active_hash" in payload
    assert "SECRET SYSTEM PROMPT" not in dumped
    assert "select token" not in dumped
    assert "few-shot examples" not in dumped
    assert "prompt" not in payload["history"][0]
    assert "template" not in payload["history"][0]


def test_masks_admin_returns_rule_metadata_without_raw_patterns(monkeypatch):
    class _Masker(object):
        active_rules = [{"name": "phone", "field": "phone", "pattern": "13812345678", "replacement": "***", "enabled": True}]

    fake_module = types.SimpleNamespace(get_masker=lambda: _Masker())
    monkeypatch.setitem(sys.modules, "masking", fake_module)

    payload = asyncio.run(server.mask_rules(_DummyRequest(_headers("admin", "tenant-a", "admin")), None))
    dumped = json.dumps(payload, ensure_ascii=False)
    assert payload["contract"] == "legacy_observability_metadata_v1"
    assert payload["status"] == "ok"
    assert payload["rule_count"] == 1
    assert "13812345678" not in dumped
    assert "replacement" not in dumped
    assert payload["rules"][0]["name"] == "phone"


def test_costs_and_fallback_admin_are_sanitized_aggregate_metadata(monkeypatch):
    summary = types.SimpleNamespace(period_hours=24, total_calls=2, total_tokens=100,
                                    total_cost_usd=0.012345, by_model={"m": 2}, by_operation={"query": 2})
    monkeypatch.setitem(sys.modules, "token_tracker", types.SimpleNamespace(get_tracker=lambda: types.SimpleNamespace(summary=lambda hours: summary)))
    monkeypatch.setitem(sys.modules, "model_fallback", types.SimpleNamespace(get_fallback_chain=lambda: types.SimpleNamespace(stats={"calls": 1, "token": "secret"})))
    req = _DummyRequest(_headers("admin", "tenant-a", "admin"))

    costs = asyncio.run(server.cost_report(req, 999, None))
    assert costs["contract"] == "legacy_observability_metadata_v1"
    assert costs["period_hours"] == 24
    assert costs["total_calls"] == 2

    fallback = asyncio.run(server.fallback_stats(req, None))
    dumped = json.dumps(fallback, ensure_ascii=False)
    assert fallback["contract"] == "legacy_observability_metadata_v1"
    assert "secret" not in dumped.lower()
