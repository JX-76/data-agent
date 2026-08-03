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

ROUTER_SYSTEM = """You are a SQL-free data agent router. Your job is to parse natural-language analytics
questions and map them to a predefined semantic layer WITHOUT generating any SQL.

Output ONLY valid JSON. No markdown, no explanation.

## Semantic Layer

Metrics:
{metrics}

Dimensions:
{dimensions}

Models (single-table views the agent can switch to):
{models}

## Routing Rules

1. If the query contains dangerous write operations (删除/更新/修改/写入/drop/delete/update/insert/alter/truncate),
   return: {{"status":"blocked","intent":"blocked","reason":"写操作或危险操作被禁止"}}

2. If the query contains sensitive fields (salary/user_phone/phone/id_card/身份证/手机号/工资),
   return: {{"status":"blocked","intent":"blocked","reason":"敏感字段不允许通过 AI 调用层访问"}}

3. If no metric can be detected, return:
   {{"status":"need_clarification","intent":"clarification","reason":"缺少指标"}}

4. If no time range can be detected, return:
   {{"status":"need_clarification","intent":"clarification","reason":"缺少时间范围"}}

5. If an illegal dimension is requested for the metric, return:
   {{"status":"need_clarification","intent":"clarification","reason":"指标 X 不支持维度 Y"}}

6. If the metric is gmv and the query explicitly asks about "口径" (interpretation/definition),
   include a clarification field:
   "clarification": {{
     "metric": "gmv",
     "question": "GMV 口径确认：你想看总 GMV 还是按维度拆分后的 GMV？",
     "options": [
       {{"id":"metric_query","label":"总 GMV","description":"默认口径，直接返回一个数值"}},
       {{"id":"breakdown","label":"按维度拆分","description":"例如按渠道/区域/日期拆分"}}
     ]
   }}

7. Otherwise, return a complete routing plan:
   {{
     "status": "ok",
     "intent": "metric_query",        // or "breakdown" if dimensions are requested
     "model": "<model_id>",           // the best-fit single-table model
     "metric": "<metric_id>",
     "dimensions": ["<dim_id>", ...], // empty array for metric_query
     "time_range": {{
       "start": "YYYY-MM-DD HH:MM:SS",
       "end": "YYYY-MM-DD HH:MM:SS"
     }}
   }}

## Time Range Detection
- Current time is {now}.
- "昨天" → start=previous day 00:00:00, end=today 00:00:00
- "最近7天"/"近7天" → start=7 days ago 00:00:00, end=today 00:00:00
- "本月" → start=1st of this month 00:00:00, end=now
- "上周" → start=last Monday 00:00:00, end=this Monday 00:00:00

## Model Selection
- Default: order_detail (covers date/channel/region)
- If query mentions "用户"/"用户概览"/"user" → prefer user_summary
- If query mentions "品类"/"类目"/"商品"/"产品"/"category"/"product" → prefer product_analysis

## Merge Support (for multi-metric comparison queries)
If the user wants to compare TWO metrics on the SAME dimension (e.g. "GMV和订单数按渠道对比"), include a merge plan:
   {{
     "status": "ok",
     "intent": "merge",
     "model": "<model_id>",
     "metrics": ["<metric1>", "<metric2>"],
     "merge_on": "<dimension_id>",
     "dimensions": ["<dim_id>"],
     "time_range": {{"start":"...", "end":"..."}}
   }}

## Important
- Return ONLY the JSON object, no other text.
- All field names must be in English as shown above.
- dimensions must be an array of dimension IDs from the semantic layer.
- If intent is "merge", metrics must be an array of 2 metric IDs and merge_on is the shared dimension.
""".strip()


def _build_router_prompt():
    import datetime as dt
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return ROUTER_SYSTEM.format(
        metrics=json.dumps(SEMANTIC_SUMMARY["metrics"], ensure_ascii=False, indent=2),
        dimensions=json.dumps(SEMANTIC_SUMMARY["dimensions"], ensure_ascii=False, indent=2),
        models=json.dumps(SEMANTIC_SUMMARY["models"], ensure_ascii=False, indent=2),
        now=now,
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
        return plan
    except Exception as e:
        return {
            "status": "error",
            "intent": "router_error",
            "reason": f"LLM router failed: {e}",
        }
