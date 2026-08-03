# -*- coding: utf-8 -*-
"""Small, dependency-free adapter for a local Ollama service.

The adapter is intentionally presentation-only in the release path: it may
produce a non-factual explanation or suggested next question, but never
creates SQL, evidence, permissions, or business facts.
"""
from __future__ import unicode_literals

import json
import os
import time
try:
    from urllib.error import HTTPError, URLError
    from urllib.request import Request, urlopen
except ImportError:  # pragma: no cover - Python 2 compatibility
    from urllib2 import HTTPError, URLError, Request, urlopen


DEFAULT_BASE_URL = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen3.5:latest"
_FORBIDDEN_OUTPUT = ("select ", " from ", "traceback", "password", "token", "secret", "http://", "https://")


class OllamaError(Exception):
    """Structured local-provider failure safe to expose as a category only."""
    def __init__(self, code, message):
        Exception.__init__(self, message)
        self.code = code
        self.message = message


class OllamaAdapter(object):
    contract = "ollama_local_adapter_v1"

    def __init__(self, base_url=None, model=None, timeout_seconds=None, opener=None):
        self.base_url = (base_url or os.environ.get("DATA_AGENT_OLLAMA_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.model = model or os.environ.get("DATA_AGENT_OLLAMA_MODEL") or DEFAULT_MODEL
        self.timeout_seconds = float(timeout_seconds or os.environ.get("DATA_AGENT_OLLAMA_TIMEOUT_SECONDS", "45"))
        self._opener = opener or urlopen

    def _request_json(self, path, payload=None, timeout_seconds=None):
        body = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(self.base_url + path, data=body, headers=headers)
        try:
            response = self._opener(request, timeout=timeout_seconds or self.timeout_seconds)
            raw = response.read()
            if not isinstance(raw, str):
                raw = raw.decode("utf-8")
            return json.loads(raw)
        except HTTPError as exc:
            # The body may contain provider-specific diagnostics (for example
            # CUDA/runtime incompatibility). Do not surface it to clients, but
            # preserve a stable category for health/UI troubleshooting.
            code = "http_%s" % exc.code
            if exc.code == 500:
                code = "provider_runtime_error"
            raise OllamaError(code, "本地 Ollama 模型运行失败；请检查 Ollama/GPU 运行时配置。")
        except URLError:
            raise OllamaError("unavailable", "本地 Ollama 服务未启动或不可访问。")
        except ValueError:
            raise OllamaError("invalid_response", "本地 Ollama 服务返回了无效响应。")
        except Exception as exc:
            message = str(exc).lower()
            if "timed out" in message or "timeout" in message:
                raise OllamaError("timeout", "本地模型响应超时。")
            raise OllamaError("connection_error", "本地 Ollama 服务连接失败。")

    def health(self):
        """Return safe readiness metadata; no prompt or server internals."""
        try:
            data = self._request_json("/api/tags", timeout_seconds=min(self.timeout_seconds, 5.0))
            names = [item.get("name") for item in data.get("models", []) if isinstance(item, dict)]
            return {"contract": self.contract, "provider": "ollama", "model": self.model,
                    "ready": self.model in names, "available_models": names,
                    "reason": None if self.model in names else "configured_model_not_found"}
        except OllamaError as exc:
            return {"contract": self.contract, "provider": "ollama", "model": self.model,
                    "ready": False, "available_models": [], "reason": exc.code}

    def explain_safe_analysis(self, question, release_chain):
        """Return only a non-factual, bounded UI note from an already safe chain."""
        overview = (release_chain or {}).get("report_sections") or {}
        metrics = (release_chain or {}).get("metrics") or {}
        prompt = (
            "你是本地数据分析助手。只能根据下面已经过证据边界校验的公开摘要，"
            "给出 1-2 句简短的分析阅读建议或下一步问题。不要创造数值、事实、引用、SQL、权限结论或执行承诺；"
            "如果数据或证据不足，只建议用户补充筛选条件。\n"
            "用户问题：%s\n指标：%s\n时间范围：%s\n已发布摘要：%s\n"
            "仅输出中文纯文本，不使用 Markdown。"
        ) % (str(question or "")[:500], str(metrics.get("metric") or "")[:100],
             str(metrics.get("time_range") or "")[:120], str(overview.get("summary") or "")[:800])
        started = time.time()
        data = self._request_json("/api/chat", {
            "model": self.model,
            "stream": False,
            "messages": [
                {"role": "system", "content": "你必须遵守用户提供的已验证摘要边界。"},
                {"role": "user", "content": prompt},
            ],
            "options": {"temperature": 0.2, "num_predict": 96},
        })
        message = data.get("message") or {}
        text = (message.get("content") or "").strip()
        lowered = text.lower()
        if not text:
            raise OllamaError("empty_response", "本地模型没有返回可展示的说明。")
        if any(item in lowered for item in _FORBIDDEN_OUTPUT):
            raise OllamaError("unsafe_response", "本地模型返回了不适合公开展示的内容。")
        return {"contract": "ollama_presentation_assist_v1", "provider": "ollama", "model": self.model,
                "status": "ok", "text": text[:700], "latency_ms": int((time.time() - started) * 1000),
                "limitations": ["该说明只用于阅读引导，不是事实、证据、SQL 或执行结果。"]}


def get_ollama_adapter():
    return OllamaAdapter()
