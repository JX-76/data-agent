# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from deepseek_adapter import DeepSeekAdapter
from user_provider_config import (InMemoryProviderConfigStore, ProviderConfigError,
                                  get_runtime_provider_config, public_view)


def test_saved_key_is_masked_in_public_contract_and_never_returned():
    store = InMemoryProviderConfigStore()
    store.save("deepseek", api_key="sk-super-secret-1234", model="deepseek-chat")
    result = public_view(store.load())
    assert result["masked_key"] == "sk-s****1234"
    assert "api_key" not in result
    assert "super-secret" not in repr(result)
    assert result["source"] == "user_config"


def test_user_config_has_priority_over_environment(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "environment-secret")
    store = InMemoryProviderConfigStore()
    store.save("deepseek", api_key="user-secret-9876", base_url="https://example.invalid", model="chosen-model")
    adapter = DeepSeekAdapter(provider_store=store)
    assert adapter.api_key == "user-secret-9876"
    assert adapter.base_url == "https://example.invalid"
    assert adapter.model == "chosen-model"
    assert adapter.config_source == "user_config"


def test_environment_is_safe_fallback_when_user_config_is_missing(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "environment-secret")
    store = InMemoryProviderConfigStore()
    runtime = get_runtime_provider_config(store)
    assert runtime["api_key"] == "environment-secret"
    assert runtime["source"] == "environment"


def test_partial_save_keeps_existing_key_but_can_change_model():
    store = InMemoryProviderConfigStore()
    store.save("deepseek", api_key="sk-first-key-1234", model="deepseek-chat")
    result = store.save("deepseek", api_key=None, model="deepseek-reasoner")
    assert result["api_key"] == "sk-first-key-1234"
    assert result["model"] == "deepseek-reasoner"


def test_unsupported_provider_and_non_secure_url_fail_closed():
    store = InMemoryProviderConfigStore()
    try:
        store.save("openai", api_key="test-key-1234")
        assert False, "expected unsupported provider"
    except ProviderConfigError as exc:
        assert exc.code == "unsupported_provider"
    try:
        store.save("deepseek", api_key="test-key-1234", base_url="http://remote.invalid")
        assert False, "expected invalid base URL"
    except ProviderConfigError as exc:
        assert exc.code == "invalid_base_url"
