# -*- coding: utf-8 -*-
"""Dependency-free DeepSeek API adapter for presentation-only assist.

This adapter intentionally mirrors the safety boundary of ``ollama_adapter``:
it can add a short UI reading note from an already release-gated envelope, but
it must never create SQL, evidence, permissions, business facts, or execution
commitments. Secrets are read from environment variables or a project ``.env``
file and are never returned in public metadata.
"""
from __future__ import unicode_literals

import json
import os
import time
from system_prompt_contract import build_presentation_assist_prompt, prompt_metadata
try:
    from urllib.error import HTTPError, URLError
    from urllib.request import Request, urlopen
except ImportError:  # pragma: no cover - Python 2 compatibility
    from urllib2 import HTTPError, URLError, Request, urlopen


DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"
_FORBIDDEN_OUTPUT = ("select ", " from ", "traceback", "password", "token", "secret", "api_key", "http://", "https://")
_ENV_LOADED = False


def _project_root():
    return os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


def load_project_env(path=None):
    """Load simple KEY=VALUE lines from .env only for missing process envs.

    This is intentionally tiny and conservative: it ignores comments, does not
    expand variables, and never returns values. Existing process environment
    variables win over the file.
    """
    global _ENV_LOADED
    if _ENV_LOADED and path is None:
        return
    env_path = path or os.path.join(_project_root(), ".env")
    try:
        handle = open(env_path, "r")
    except Exception:
        _ENV_LOADED = True
        return
    try:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and value and key not in os.environ:
                os.environ[key] = value
    finally:
        handle.close()
        _ENV_LOADED = True


class DeepSeekError(Exception):
    """Structured provider failure safe to expose as a category only."""
    def __init__(self, code, message):
        Exception.__init__(self, message)
        self.code = code
        self.message = message


class DeepSeekAdapter(object):
    contract = "deepseek_api_adapter_v1"

    def __init__(self, base_url=None, model=None, api_key=None, timeout_seconds=None, opener=None, env_path=None, provider_store=None):
        load_project_env(env_path)
        runtime_config = None
        if not (base_url or model or api_key):
            try:
                from user_provider_config import get_runtime_provider_config
                runtime_config = get_runtime_provider_config(provider_store)
            except Exception:
                # Provider settings are optional; the existing environment fallback
                # remains available even when a local settings file is unreadable.
                runtime_config = None
        runtime_config = runtime_config or {}
        self.base_url = (base_url or runtime_config.get("base_url") or os.environ.get("DATA_AGENT_DEEPSEEK_BASE_URL") or
                          os.environ.get("DEEPSEEK_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.model = model or runtime_config.get("model") or os.environ.get("DATA_AGENT_DEEPSEEK_MODEL") or os.environ.get("DEEPSEEK_MODEL") or DEFAULT_MODEL
        self.api_key = api_key or runtime_config.get("api_key") or os.environ.get("DATA_AGENT_DEEPSEEK_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")
        self.config_source = runtime_config.get("source") or ("explicit" if api_key else "environment")
        self.timeout_seconds = float(timeout_seconds or os.environ.get("DATA_AGENT_DEEPSEEK_TIMEOUT_SECONDS", "45") or 45)
        self._opener = opener or urlopen
        self.provider_trace = None

    def _request_json(self, path, payload=None, timeout_seconds=None):
        if not self.api_key:
            raise DeepSeekError("missing_api_key", "DeepSeek API key 未配置。")
        body = None
        headers = {"Accept": "application/json", "Authorization": "Bearer %s" % self.api_key}
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(self.base_url + path, data=body, headers=headers)
        def _send():
            response = self._opener(request, timeout=timeout_seconds or self.timeout_seconds)
            raw = response.read()
            if not isinstance(raw, str):
                raw = raw.decode("utf-8")
            return json.loads(raw)
        try:
            if os.environ.get("DATA_AGENT_DEEPSEEK_RESILIENCE", "1") == "0":
                return _send()
            from provider_resilience import ProviderResilience
            resilience = ProviderResilience(
                "deepseek",
                max_attempts=int(os.environ.get("DATA_AGENT_DEEPSEEK_MAX_ATTEMPTS", "3") or 3),
                base_delay_seconds=float(os.environ.get("DATA_AGENT_DEEPSEEK_RETRY_BASE_SECONDS", "0.25") or 0.25),
                max_delay_seconds=float(os.environ.get("DATA_AGENT_DEEPSEEK_RETRY_MAX_SECONDS", "2.0") or 2.0),
                jitter_seconds=float(os.environ.get("DATA_AGENT_DEEPSEEK_RETRY_JITTER_SECONDS", "0.1") or 0.1),
                failure_threshold=int(os.environ.get("DATA_AGENT_DEEPSEEK_CIRCUIT_FAILURE_THRESHOLD", "3") or 3),
                recovery_timeout_seconds=float(os.environ.get("DATA_AGENT_DEEPSEEK_CIRCUIT_RECOVERY_SECONDS", "15") or 15))
            def _error_code(exc):
                if isinstance(exc, HTTPError):
                    if exc.code in (401, 403):
                        return "auth_failed"
                    if exc.code == 429:
                        return "rate_limited"
                    if exc.code >= 500:
                        return "provider_runtime_error"
                if isinstance(exc, URLError):
                    return "unavailable"
                return getattr(exc, "code", None)
            try:
                result = resilience.call(_send, error_code_getter=_error_code)
            finally:
                # This trace has no request, response, URL, or credential values.
                self.provider_trace = resilience.last_observation
            return result
        except HTTPError as exc:
            code = "http_%s" % exc.code
            if exc.code in (401, 403):
                code = "auth_failed"
            elif exc.code == 429:
                code = "rate_limited"
            elif exc.code >= 500:
                code = "provider_runtime_error"
            raise DeepSeekError(code, "DeepSeek API 暂不可用或拒绝请求。")
        except URLError:
            raise DeepSeekError("unavailable", "DeepSeek API 网络不可访问。")
        except ValueError:
            raise DeepSeekError("invalid_response", "DeepSeek API 返回了无效响应。")
        except Exception as exc:
            if hasattr(exc, "code") and getattr(exc, "code") == "circuit_open":
                raise DeepSeekError("circuit_open", "DeepSeek API 熔断保护已打开，请稍后重试。")
            message = str(exc).lower()
            if "timed out" in message or "timeout" in message:
                raise DeepSeekError("timeout", "DeepSeek API 响应超时。")
            raise DeepSeekError("connection_error", "DeepSeek API 连接失败。")

    def health(self):
        return {"contract": self.contract, "provider": "deepseek", "model": self.model,
                "ready": bool(self.api_key), "reason": None if self.api_key else "missing_api_key",
                "config_source": self.config_source}

    def explain_safe_analysis(self, question, release_chain):
        overview = (release_chain or {}).get("report_sections") or {}
        metrics = (release_chain or {}).get("metrics") or {}
        prompt = (
            "你是数据分析助手。只能根据下面已经过证据边界校验的公开摘要，"
            "给出 1-2 句简短的分析阅读建议或下一步问题。不要创造数值、事实、引用、SQL、权限结论或执行承诺；"
            "如果数据或证据不足，只建议用户补充筛选条件。\n"
            "用户问题：%s\n指标：%s\n时间范围：%s\n已发布摘要：%s\n"
            "仅输出中文纯文本，不使用 Markdown。"
        ) % (str(question or "")[:500], str(metrics.get("metric") or "")[:100],
             str(metrics.get("time_range") or "")[:120], str(overview.get("summary") or "")[:800])
        started = time.time()
        data = self._request_json("/chat/completions", {
            "model": self.model,
            "messages": [
                {"role": "system", "content": build_presentation_assist_prompt("DeepSeek")},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
            "max_tokens": 120,
            "stream": False,
        })
        provider_trace = self.provider_trace
        choices = data.get("choices") or []
        message = (choices[0].get("message") if choices and isinstance(choices[0], dict) else {}) or {}
        text = (message.get("content") or "").strip()
        lowered = text.lower()
        if not text:
            raise DeepSeekError("empty_response", "DeepSeek API 没有返回可展示的说明。")
        if any(item in lowered for item in _FORBIDDEN_OUTPUT):
            raise DeepSeekError("unsafe_response", "DeepSeek API 返回了不适合公开展示的内容。")
        system_prompt = build_presentation_assist_prompt("DeepSeek")
        return {"contract": "deepseek_presentation_assist_v1", "provider": "deepseek", "model": self.model,
                "status": "ok", "text": text[:700], "latency_ms": int((time.time() - started) * 1000),
                "provider_trace": provider_trace,
                "prompt_metadata": prompt_metadata("presentation_assist", system_prompt),
                "limitations": ["该说明只用于阅读引导，不是事实、证据、SQL 或执行结果。"]}


def get_deepseek_adapter():
    return DeepSeekAdapter()
