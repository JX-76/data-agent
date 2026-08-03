"""Multi-model fallback: tries primary model, cascades to backups on failure.

Chain: DeepSeek V4 Flash → DeepSeek Chat → Regex routing (always succeeds)

Usage:
    from model_fallback import route_with_fallback
    result = route_with_fallback(query, fallbacks=["deepseek-chat", "regex"])
"""

from __future__ import annotations

import json
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from typing import Optional, Callable

import structlog

from config import DEEPSEEK_BASE, DEEPSEEK_KEY, ROUTER_MODEL
from token_tracker import track_call

logger = structlog.get_logger("model-fallback")


@dataclass
class FallbackEvent:
    model: str
    success: bool
    duration_ms: float
    error: Optional[str] = None
    prompt_tokens: int = 0
    completion_tokens: int = 0


@dataclass
class FallbackChain:
    """Ordered list of fallback models."""

    models: list[str] = field(default_factory=lambda: ["deepseek-v4-flash", "deepseek-chat"])
    timeout_seconds: float = 15.0
    max_retries_per_model: int = 1

    def __post_init__(self):
        self._events: list[FallbackEvent] = []

    @property
    def last_events(self) -> list[dict]:
        return [e.__dict__ for e in self._events[-10:]]

    @property
    def stats(self) -> dict:
        total = len(self._events)
        if not total:
            return {"total_attempts": 0, "success_rate": "N/A"}
        successes = sum(1 for e in self._events if e.success)
        by_model = {}
        for e in self._events:
            if e.model not in by_model:
                by_model[e.model] = {"attempts": 0, "successes": 0}
            by_model[e.model]["attempts"] += 1
            if e.success:
                by_model[e.model]["successes"] += 1
        return {
            "total_attempts": total,
            "success_rate": f"{successes / total:.1%}",
            "by_model": by_model,
            "last_error": next((e.error for e in reversed(self._events) if not e.success), None),
        }


# ── LLM Call Helpers ──

def _call_deepseek(
    model: str,
    system_prompt: str,
    user_message: str,
    timeout: float = 15.0,
    temperature: float = 0.0,
    max_tokens: int = 512,
) -> dict:
    """Call DeepSeek API. Returns {"ok": True, "content": ...} or {"ok": False, "error": ...}."""
    if not DEEPSEEK_KEY:
        return {"ok": False, "error": "No API key configured"}

    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "response_format": {"type": "json_object"},
    }

    t0 = time.time()
    try:
        req = urllib.request.Request(
            f"{DEEPSEEK_BASE}/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {DEEPSEEK_KEY}",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            msg = result["choices"][0]["message"]
            usage = result.get("usage", {})

            dt = (time.time() - t0) * 1000

            # Token tracking
            prompt_tokens = usage.get("prompt_tokens", 0)
            completion_tokens = usage.get("completion_tokens", 0)
            track_call(model=model, operation="route",
                      prompt_tokens=prompt_tokens,
                      completion_tokens=completion_tokens,
                      duration_ms=dt)

            return {"ok": True, "content": msg["content"],
                    "model": model, "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "duration_ms": dt}
    except urllib.error.HTTPError as e:
        dt = (time.time() - t0) * 1000
        err_body = ""
        try:
            err_body = e.read().decode("utf-8")[:200]
        except Exception as e:
            logger.warning("bare_exception_caught", error=str(e))
            pass
        return {"ok": False, "error": f"HTTP {e.code}: {err_body}",
                "model": model, "duration_ms": dt}
    except Exception as e:
        dt = (time.time() - t0) * 1000
        return {"ok": False, "error": str(e), "model": model,
                "duration_ms": dt}


# ── Fallback Router ──

def route_with_fallback(
    query: str,
    system_prompt: str,
    chain: FallbackChain | None = None,
    regex_router: Callable | None = None,
) -> dict:
    """Route with automatic fallback across models.

    Args:
        query: User's natural language query
        system_prompt: Router system prompt
        chain: Fallback chain config (default: deepseek-v4-flash → deepseek-chat → regex)
        regex_router: Regex-based router as ultimate fallback

    Returns:
        Routing plan dict
    """
    if chain is None:
        chain = FallbackChain()
    chain._events = []

    # Attempt each model in sequence
    for model in chain.models:
        for attempt in range(chain.max_retries_per_model):
            t0 = time.time()

            if attempt > 0:
                logger.info("retry_attempt", model=model, attempt=attempt + 1)

            result = _call_deepseek(
                model=model,
                system_prompt=system_prompt,
                user_message=query,
                timeout=chain.timeout_seconds,
            )

            dt = (time.time() - t0) * 1000

            if result["ok"]:
                chain._events.append(FallbackEvent(
                    model=model, success=True, duration_ms=dt,
                    prompt_tokens=result.get("prompt_tokens", 0),
                    completion_tokens=result.get("completion_tokens", 0),
                ))
                logger.info("llm_route_success", model=model,
                           duration_ms=int(dt),
                           fallback_attempts=len(chain._events) - 1)

                try:
                    plan = json.loads(result["content"])
                    plan["_routed_by"] = model
                    plan["_fallback_attempts"] = len(chain._events) - 1
                    return plan
                except json.JSONDecodeError:
                    chain._events.append(FallbackEvent(
                        model=model, success=False, duration_ms=dt,
                        error="JSON parse failed",
                    ))
                    continue  # Try next model
            else:
                chain._events.append(FallbackEvent(
                    model=model, success=False, duration_ms=dt,
                    error=result["error"],
                ))
                logger.warning("llm_route_failed", model=model,
                             error=result["error"][:100])
                break  # Don't retry same model on API error

    # All models failed → fallback to regex
    if regex_router:
        logger.warning("all_models_failed", falling_back="regex",
                      attempts=len(chain._events))
        chain._events.append(FallbackEvent(
            model="regex", success=True, duration_ms=0,
        ))
        return regex_router(query)

    # Ultimate fallback
    return {"status": "error", "reason": "All routing models failed",
            "fallback_events": chain.last_events}


# ── Global ──

_default_chain: FallbackChain | None = None


def get_fallback_chain() -> FallbackChain:
    global _default_chain
    if _default_chain is None:
        _default_chain = FallbackChain()
    return _default_chain
