"""LLM-based routing for the Data Agent AI calling layer.

Replaces regex/synonym matching with structured LLM calls that understand
natural language queries and map them to the semantic layer.
"""
import json
import sys
import urllib.request
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import DEEPSEEK_BASE, DEEPSEEK_KEY, ROUTER_MODEL, SEMANTIC_SUMMARY

from system_prompt_contract import build_router_prompt, prompt_metadata


def _build_router_prompt():
    import datetime as dt
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return build_router_prompt(
        SEMANTIC_SUMMARY["metrics"],
        SEMANTIC_SUMMARY["dimensions"],
        SEMANTIC_SUMMARY["models"],
        now,
    )


def call_llm(system_prompt, user_message, model=ROUTER_MODEL, max_tokens=1024):
    """Call DeepSeek API with a system prompt and return the response text."""
    if not DEEPSEEK_KEY:
        raise RuntimeError("DEEPSEEK_API_KEY is not set")

    url = f"{DEEPSEEK_BASE}/chat/completions"
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.0,
        "response_format": {"type": "json_object"},
    }).encode("utf-8")

    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {DEEPSEEK_KEY}",
    })

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"API error {e.code}: {e.read().decode('utf-8', errors='replace')}")
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        raise RuntimeError(f"Unexpected API response: {e}")


def _parse_router_json(raw):
    """Parse the LLM's JSON response into the standard plan format."""
    plan = json.loads(raw)

    # Normalize time_range if present
    if isinstance(plan.get("time_range"), dict) and "start" in plan["time_range"]:
        import datetime as dt
        plan["time_range"] = (
            dt.datetime.fromisoformat(plan["time_range"]["start"]),
            dt.datetime.fromisoformat(plan["time_range"]["end"]),
        )

    return plan


def llm_route_and_plan(query: str) -> dict:
    """Route a natural-language query using the LLM + semantic layer.

    Returns a plan dict in the same format as the regex-based route_and_plan().
    Falls back gracefully if the LLM is unavailable.
    """
    try:
        system_prompt = _build_router_prompt()
        raw = call_llm(system_prompt, query)
        plan = _parse_router_json(raw)
        plan.setdefault("prompt_metadata", prompt_metadata("router", system_prompt))
        return plan
    except Exception as e:
        return {
            "status": "error",
            "intent": "router_error",
            "reason": f"LLM router failed: {e}",
        }
