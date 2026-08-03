"""Natural-language output layer: generates human-readable conclusions + chart configs.

Takes structured analysis results from analysis.py and produces:
1. A natural-language insight paragraph
2. Chart type recommendation + ECharts-ready config
"""

import json
import sys
import urllib.request
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import DEEPSEEK_BASE, DEEPSEEK_KEY, ANALYSIS_MODEL
from system_prompt_contract import build_insight_prompt


SYSTEM_PROMPT = build_insight_prompt()


def _call_analysis_llm(prompt: str) -> dict:
    """Call LLM to generate NL insights and chart config."""
    if not DEEPSEEK_KEY:
        return {"insight": "API key 未配置", "chart": {"type": "none", "reason": "no API key"}}

    url = f"{DEEPSEEK_BASE}/chat/completions"
    body = json.dumps({
        "model": ANALYSIS_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 2048,
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
            return json.loads(result["choices"][0]["message"]["content"])
    except (urllib.error.HTTPError, KeyError, IndexError, json.JSONDecodeError) as e:
        return {"insight": f"分析生成失败: {e}", "chart": {"type": "none", "reason": str(e)}}


def _recommend_chart_heuristic(dimensions, metrics, row_count):
    """Rule-based chart recommendation as fallback."""
    if row_count == 0:
        return {"type": "none", "reason": "no data"}
    if not dimensions:
        return {"type": "number_card", "reason": "single metric value"}
    time_dims = [d for d in dimensions if d in ("date",) or "date" in d.lower()]
    if time_dims and row_count > 1:
        return {"type": "line", "reason": "time series data"}
    if row_count <= 8:
        return {"type": "bar", "reason": f"categorical comparison with {row_count} categories"}
    return {"type": "bar", "reason": f"categorical with {row_count} rows"}


def _build_prompt(query: str, sql: str, results: list[dict], analysis: dict) -> str:
    """Build the LLM prompt from analysis data with budget-aware trimming.

    Uses ResultTrimmer to cap result rows and extract key statistics
    instead of dumping all raw data into the context.
    """
    from context_manager import ResultTrimmer

    trimmed = ResultTrimmer.trim_rows(results, sample_rows=5, max_chars=600)
    stats = ResultTrimmer.extract_key_stats(results)

    prompt_parts = [f"Original query: {query}", "", f"Generated SQL:\n{sql}", ""]

    # Summarized results (NOT full dump)
    prompt_parts.append(f"Query results: {trimmed['row_count']} rows, {len(trimmed.get('columns', []))} columns")
    prompt_parts.append(f"Columns: {trimmed.get('columns', [])}")
    if stats:
        prompt_parts.append(f"Key statistics: {json.dumps(stats, ensure_ascii=False)}")
    prompt_parts.append(f"Sample data:\n{json.dumps(trimmed.get('sample', []), ensure_ascii=False, indent=2)}")
    prompt_parts.append("")
    prompt_parts.append(f"Structured analysis:\n{json.dumps(analysis, ensure_ascii=False, indent=2)}")

    # Critical: place the most important instruction LAST
    prompt_parts.append("\nPlease generate the insight paragraph and chart config. Return ONLY valid JSON.")

    return "\n".join(prompt_parts)


def generate_insight(query: str, sql: str, results: list[dict], analysis: dict = None,
                     dimensions: list[str] = None, metrics: list[str] = None,
                     use_llm: bool = True) -> dict:
    """Generate natural-language insight + chart config for a query result.

    Returns:
        {"insight": str, "chart": {"type": str, "reason": str, "config": dict}}
    """
    if analysis is None:
        analysis = {}
    if results is None:
        results = []

    row_count = len(results)
    dims = dimensions or []
    mets = metrics or []

    if row_count == 0:
        return {"insight": "未查询到数据，请尝试调整查询条件。", "chart": _recommend_chart_heuristic(dims, mets, row_count)}

    # LLM-based insight generation (only when use_llm=True)
    if use_llm:
        try:
            prompt = _build_prompt(query, sql, results, analysis)
            result = _call_analysis_llm(prompt)
            return result
        except Exception as e:
            logger.warning("bare_exception_caught", error=str(e))
            pass  # Fall through to heuristic

    # Heuristic fallback (no LLM)
    chart = _recommend_chart_heuristic(dims, mets, row_count)
    summary = analysis.get("summary", {})
    trends = analysis.get("trends", {})

    parts = []
    for m in mets:
        s = summary.get(m, {})
        if isinstance(s, dict) and "total" in s:
            parts.append(f"{m} 总计 {s['total']}")
        t = trends.get(m, {})
        if t:
            direction = "上升" if t["direction"] == "up" else ("下降" if t["direction"] == "down" else "保持稳定")
            parts.append(f"趋势：{direction} {t.get('change_pct','')}%")

    topn = analysis.get("top_n", {})
    for m in mets:
        tn = topn.get(m, {}).get("top", [])
        if tn:
            parts.append(f"最高：{json.dumps(tn[0], ensure_ascii=False)}")

    insight = "；".join(parts) if parts else f"共返回 {row_count} 条数据。"
    return {"insight": insight, "chart": chart}
