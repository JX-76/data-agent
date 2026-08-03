# -*- coding: utf-8 -*-
"""Local user-managed model provider configuration.

This module provides the minimal offline/testable control plane for users to
configure their own LLM provider API key without committing secrets to source
control.  The default adapter is a single-machine JSON file intended for local
MVP usage; production deployments should replace it with a KMS/secret-manager
backed implementation.
"""
from __future__ import unicode_literals

import json
import os
import time


DEFAULT_PROVIDER = "deepseek"
DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"
CONTRACT = "user_provider_config_v1"
_PUBLIC_KEYS = ("contract", "provider", "base_url", "model", "masked_key", "status",
                "source", "created_at", "updated_at", "last_validated_at", "limitations")


class ProviderConfigError(Exception):
    """Safe configuration error; message must not contain secrets."""

    def __init__(self, code, message):
        Exception.__init__(self, message)
        self.code = code
        self.message = message


class LocalProviderConfigStore(object):
    """Tiny JSON-file store for local single-user provider settings."""

    def __init__(self, path=None):
        self.path = path or provider_config_path()

    def load(self):
        try:
            handle = open(self.path, "r")
        except Exception:
            return None
        try:
            data = json.loads(handle.read() or "{}")
        except Exception:
            return None
        finally:
            handle.close()
        if not isinstance(data, dict):
            return None
        return _normalize_record(data, source="user_config")

    def save(self, provider, api_key=None, base_url=None, model=None, validate_status=None):
        provider = _normalize_provider(provider)
        existing = self.load() or {}
        now = _now()
        key = api_key if api_key is not None else existing.get("api_key")
        if not key:
            raise ProviderConfigError("missing_api_key", "API Key 不能为空。")
        record = {
            "contract": CONTRACT,
            "provider": provider,
            "api_key": str(key),
            "base_url": _clean_url(base_url or existing.get("base_url") or DEFAULT_BASE_URL),
            "model": str(model or existing.get("model") or DEFAULT_MODEL),
            "status": validate_status or existing.get("status") or "configured",
            "source": "user_config",
            "created_at": existing.get("created_at") or now,
            "updated_at": now,
            "last_validated_at": existing.get("last_validated_at"),
        }
        _ensure_parent(self.path)
        handle = open(self.path, "w")
        try:
            handle.write(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True))
        finally:
            handle.close()
        _best_effort_private_file(self.path)
        return _normalize_record(record, source="user_config")

    def mark_validated(self, status):
        record = self.load()
        if not record:
            raise ProviderConfigError("not_configured", "尚未配置 Provider。")
        record["status"] = status or "validated"
        record["last_validated_at"] = _now()
        record["updated_at"] = _now()
        _ensure_parent(self.path)
        handle = open(self.path, "w")
        try:
            handle.write(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True))
        finally:
            handle.close()
        _best_effort_private_file(self.path)
        return record

    def delete(self):
        existed = os.path.exists(self.path)
        try:
            if existed:
                os.remove(self.path)
        except Exception:
            raise ProviderConfigError("delete_failed", "删除本地 Provider 配置失败。")
        return existed


class InMemoryProviderConfigStore(object):
    """Test double with the same contract as the local file store."""

    def __init__(self):
        self.record = None

    def load(self):
        return _normalize_record(dict(self.record), source="user_config") if self.record else None

    def save(self, provider, api_key=None, base_url=None, model=None, validate_status=None):
        provider = _normalize_provider(provider)
        now = _now()
        existing = self.record or {}
        key = api_key if api_key is not None else existing.get("api_key")
        if not key:
            raise ProviderConfigError("missing_api_key", "API Key 不能为空。")
        self.record = {
            "contract": CONTRACT,
            "provider": provider,
            "api_key": str(key),
            "base_url": _clean_url(base_url or existing.get("base_url") or DEFAULT_BASE_URL),
            "model": str(model or existing.get("model") or DEFAULT_MODEL),
            "status": validate_status or existing.get("status") or "configured",
            "source": "user_config",
            "created_at": existing.get("created_at") or now,
            "updated_at": now,
            "last_validated_at": existing.get("last_validated_at"),
        }
        return self.load()

    def mark_validated(self, status):
        if not self.record:
            raise ProviderConfigError("not_configured", "尚未配置 Provider。")
        self.record["status"] = status or "validated"
        self.record["last_validated_at"] = _now()
        self.record["updated_at"] = _now()
        return self.load()

    def delete(self):
        existed = bool(self.record)
        self.record = None
        return existed


def provider_config_path():
    return os.environ.get("DATA_AGENT_PROVIDER_CONFIG_PATH") or os.path.join(_project_root(), ".data_agent_provider_config.json")


def _project_root():
    return os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


def _now():
    return int(time.time())


def _ensure_parent(path):
    parent = os.path.dirname(os.path.abspath(path))
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)


def _best_effort_private_file(path):
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass


def _normalize_provider(provider):
    provider = (provider or DEFAULT_PROVIDER).strip().lower()
    if provider != DEFAULT_PROVIDER:
        raise ProviderConfigError("unsupported_provider", "当前版本仅支持 DeepSeek Provider。")
    return provider


def _clean_url(value):
    value = (value or DEFAULT_BASE_URL).strip().rstrip("/")
    if not (value.startswith("https://") or value.startswith("http://localhost") or value.startswith("http://127.0.0.1")):
        raise ProviderConfigError("invalid_base_url", "Base URL 必须使用 https，或本地 localhost/127.0.0.1。")
    return value


def mask_api_key(value):
    value = str(value or "")
    if not value:
        return ""
    if len(value) <= 8:
        return value[:2] + "****"
    return value[:4] + "****" + value[-4:]


def _normalize_record(record, source=None):
    if not record:
        return None
    provider = _normalize_provider(record.get("provider") or DEFAULT_PROVIDER)
    base_url = _clean_url(record.get("base_url") or DEFAULT_BASE_URL)
    model = str(record.get("model") or DEFAULT_MODEL)
    return {
        "contract": CONTRACT,
        "provider": provider,
        "api_key": record.get("api_key") or "",
        "base_url": base_url,
        "model": model,
        "masked_key": mask_api_key(record.get("api_key") or ""),
        "status": record.get("status") or ("configured" if record.get("api_key") else "missing_api_key"),
        "source": source or record.get("source") or "unknown",
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
        "last_validated_at": record.get("last_validated_at"),
    }


def public_view(record):
    record = _normalize_record(record or {}, source=(record or {}).get("source"))
    if not record:
        return default_public_view()
    out = dict((k, record.get(k)) for k in _PUBLIC_KEYS if k in record)
    out["limitations"] = ["API Key 仅本地保存，接口永不返回明文；生产部署应接入 KMS/Secrets Manager。"]
    return out


def default_public_view():
    return {
        "contract": CONTRACT,
        "provider": DEFAULT_PROVIDER,
        "base_url": os.environ.get("DATA_AGENT_DEEPSEEK_BASE_URL") or os.environ.get("DEEPSEEK_BASE_URL") or DEFAULT_BASE_URL,
        "model": os.environ.get("DATA_AGENT_DEEPSEEK_MODEL") or os.environ.get("DEEPSEEK_MODEL") or DEFAULT_MODEL,
        "masked_key": mask_api_key(os.environ.get("DATA_AGENT_DEEPSEEK_API_KEY") or os.environ.get("DEEPSEEK_API_KEY") or ""),
        "status": "configured" if (os.environ.get("DATA_AGENT_DEEPSEEK_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")) else "missing_api_key",
        "source": "environment" if (os.environ.get("DATA_AGENT_DEEPSEEK_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")) else "none",
        "created_at": None,
        "updated_at": None,
        "last_validated_at": None,
        "limitations": ["未找到用户级配置时会回退到环境变量；接口不返回明文 API Key。"],
    }


def get_user_provider_config(store=None):
    store = store or LocalProviderConfigStore()
    return store.load()


def get_runtime_provider_config(store=None):
    """Return user config first, then environment fallback, including secret."""
    record = get_user_provider_config(store)
    if record and record.get("api_key"):
        return record
    key = os.environ.get("DATA_AGENT_DEEPSEEK_API_KEY") or os.environ.get("DEEPSEEK_API_KEY") or ""
    if not key:
        return None
    return _normalize_record({
        "provider": DEFAULT_PROVIDER,
        "api_key": key,
        "base_url": os.environ.get("DATA_AGENT_DEEPSEEK_BASE_URL") or os.environ.get("DEEPSEEK_BASE_URL") or DEFAULT_BASE_URL,
        "model": os.environ.get("DATA_AGENT_DEEPSEEK_MODEL") or os.environ.get("DEEPSEEK_MODEL") or DEFAULT_MODEL,
        "status": "configured",
        "source": "environment",
    }, source="environment")
