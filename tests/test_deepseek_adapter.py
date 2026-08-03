# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from deepseek_adapter import DeepSeekAdapter, DeepSeekError, HTTPError


class _Response(object):
    def __init__(self, value):
        self.value = value

    def read(self):
        return json.dumps(self.value).encode("utf-8")


def test_deepseek_adapter_sends_bearer_auth_without_returning_secret():
    captured = {}

    def opener(request, timeout):
        captured["url"] = request.get_full_url()
        captured["auth"] = request.get_header("Authorization")
        captured["body"] = request.data.decode("utf-8")
        return _Response({"choices": [{"message": {"content": "建议先确认筛选范围。"}}]})

    adapter = DeepSeekAdapter(api_key="unit-secret", model="unit-model", opener=opener)
    result = adapter.explain_safe_analysis("查看 GMV", {"report_sections": {"summary": "已验证摘要"}})
    assert captured["url"].endswith("/chat/completions")
    assert captured["auth"] == "Bearer unit-secret"
    assert "unit-secret" not in repr(result)
    assert result["provider"] == "deepseek"
    assert result["status"] == "ok"
    assert "不要创造数值" in captured["body"]


def test_deepseek_adapter_normalizes_provider_diagnostic():
    def opener(request, timeout):
        raise HTTPError("https://fixture", 500, "provider secret diagnostic", {}, None)

    adapter = DeepSeekAdapter(api_key="unit-secret", opener=opener)
    try:
        adapter._request_json("/chat/completions", {"model": "fixture"})
        assert False, "expected normalized provider failure"
    except DeepSeekError as exc:
        assert exc.code == "provider_runtime_error"
        assert "diagnostic" not in exc.message.lower()
        assert "secret" not in exc.message.lower()


def test_deepseek_adapter_rejects_unsafe_presentation_output():
    def opener(request, timeout):
        return _Response({"choices": [{"message": {"content": "SELECT * FROM orders"}}]})

    adapter = DeepSeekAdapter(api_key="unit-secret", opener=opener)
    try:
        adapter.explain_safe_analysis("查看 GMV", {})
        assert False, "expected unsafe response rejection"
    except DeepSeekError as exc:
        assert exc.code == "unsafe_response"


def test_deepseek_health_does_not_expose_api_key():
    adapter = DeepSeekAdapter(api_key="unit-secret", model="unit-model")
    health = adapter.health()
    assert health["ready"] is True
    assert health["provider"] == "deepseek"
    assert "unit-secret" not in repr(health)
