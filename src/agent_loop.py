"""ReAct Agent Loop for the Data Agent.

Replaces the hardcoded execute_plan pipeline with a loop where the LLM
autonomously decides which tool to call next, observes the result,
and iterates until it declares the analysis complete.

Uses the same DeepSeek V4 Flash API for agent reasoning.
"""

import json
import sys
import urllib.request
import urllib.error
from pathlib import Path

import structlog

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import DEEPSEEK_BASE, DEEPSEEK_KEY, ANALYSIS_MODEL

logger = structlog.get_logger("agent_loop")
MAX_STEPS = 10


def _build_agent_prompt(tools_description, semantic_summary, current_dataids):
    """Build system prompt with prefix-cache-friendly layout.

    Layout: [immutable rules + tools + schema] → cacheable prefix
            [...] → minimal gap
            [variable: current dataids] → appended last
    """
    from context_manager import PrefixCacheManager
    from config import SEMANTIC_SUMMARY as SS

    pm = PrefixCacheManager()
    pm.set_tools(tools_description)
    return pm.build_system_prompt(tools_description, None, current_dataids)


def _call_agent_llm_raw(messages, max_tokens=512):
    """Call DeepSeek and return raw response text (NOT parsed JSON).

    JSON parsing moves to ParseRetryManager for proper error handling.
    """
    if not DEEPSEEK_KEY:
        return '{"action": "done", "summary": "API key not configured"}'

    url = f"{DEEPSEEK_BASE}/chat/completions"
    body = json.dumps({
        "model": ANALYSIS_MODEL,
        "messages": messages,
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
    except Exception as e:
        return f'{{"action": "done", "summary": "Agent error: {e}"}}'


def _format_observation(tool_result: dict, dataid: str, sample_rows: dict = None) -> str:
    """Format tool execution result for the agent with budget-aware trimming.

    Uses ResultTrimmer to cap rows/chars before injecting into context.
    Only the LLM needs: row count, sample rows, column headers.
    """
    from context_manager import ResultTrimmer

    if not sample_rows:
        return f"dataid: {dataid}\nstatus: ok"

    parts = [f"dataid: {dataid}"]

    if isinstance(sample_rows, dict):
        if "row_count" in sample_rows:
            parts.append(f"row_count: {sample_rows['row_count']}")

        rows = sample_rows.get("rows")
        if isinstance(rows, list) and rows:
            trimmed = ResultTrimmer.trim_rows(rows)
            parts.append(f"sample ({trimmed['row_count']} total, showing {len(trimmed.get('sample', []))}): {json.dumps(trimmed['sample'], ensure_ascii=False)}")
            if trimmed.get("hidden_columns"):
                parts.append(f"columns ({len(trimmed.get('columns', []))} visible, {trimmed['hidden_columns']} hidden)")
            elif trimmed.get("columns"):
                parts.append(f"columns: {trimmed['columns']}")

            # Extract key stats for large results
            if trimmed['row_count'] > 10:
                stats = ResultTrimmer.extract_key_stats(rows)
                if stats:
                    parts.append(f"stats: {json.dumps(stats, ensure_ascii=False)}")
        elif "columns" in sample_rows:
            parts.append(f"columns: {sample_rows['columns']}")
        if "visible_columns" in sample_rows:
            parts.append(f"visible: {sample_rows['visible_columns']}")

    if tool_result.get("error"):
        parts.append(f"error: {tool_result['error']}")

    return "\n".join(parts)


def react_loop(query: str, use_db: bool = True, max_steps: int = MAX_STEPS,
               max_tool_calls: int = 12, max_replans: int = 4, tracer=None) -> dict:
    """Run the ReAct agent loop.

    Args:
        query: User's natural language question
        use_db: Whether to use the SQLite executor for real data
        max_steps: Maximum agent steps before forced termination
        tracer: Optional TracingObserver (e.g., LangfuseTracer) for observability

    Returns:
        {
            "query": str,
            "steps": [{"step": int, "action": dict, "observation": str}, ...],
            "insight": str,
            "chart": dict,
            "sql": str | None,
            "results": list[dict] | None,
        }
    """
    # Use lazy imports to avoid circular dependency
    # These imports are inside the function to break the cycle: agent_loop -> mvp_agent -> agent_loop
    from mvp_agent import AgentRuntime, SEM
    from db_executor import get_db as _get_db
    from analysis import analyze as _analyze
    from nlu import generate_insight as _nl_insight
    from tool_dispatcher import ToolDispatcher

    rt = AgentRuntime()
    db = _get_db() if use_db else None
    dispatcher = ToolDispatcher(rt, db=db)

    import uuid
    trace_id = uuid.uuid4().hex
    if tracer:
        tracer.on_trace_start(trace_id, "agent_loop", {"query": query})

    tools_desc = """switch(model_id) - Switch to semantic model
preview(dataid, n=5) - View sample rows
filter(dataid, metric_id, start_iso, end_iso) - Filter time range + metric defaults
aggregate(dataid, metric_id, dimensions=[]) - Aggregate by dimensions
sort(dataid, by, order="DESC") - Sort results
top(dataid, by, n=5, order="DESC") - Get top/bottom N rows (same as sort+LIMIT)
filter_value(dataid, dimension, value) - Filter to a specific dimension value (ex: channel='online')
merge(dataid_a, dataid_b, on) - Join two datasets on shared dimension
compare_periods(dataid, metric_id, p1_start, p1_end, p2_start, p2_end, dimensions) - Compare two time periods
catalog() - List available models, metrics, dimensions
done() - Finish analysis"""

    # Build initial observation with the query
    current_dataids = []
    system_prompt = _build_agent_prompt(tools_desc, None, current_dataids)
    from context_manager import estimate_tokens, MessageCompactor
    system_tokens = estimate_tokens(system_prompt)
    compactor = MessageCompactor(max_tokens=7000)  # DeepSeek V4 has 128k, budget is for cost

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"Query: {query}"},
    ]

    steps = []
    final_sql = None
    final_results = None
    tool_call_count = 0
    replan_count = 0
    visited_signatures = []
    termination_reason = None

    # Parse retry manager for each step
    from tool_enforcer import ParseRetryManager

    for step_idx in range(1, max_steps + 1):
        if tool_call_count >= max_tool_calls:
            termination_reason = f"max_tool_calls_exceeded({max_tool_calls})"
            logger.warning("loop_terminated", reason=termination_reason, step=step_idx)
            break
        if replan_count >= max_replans:
            termination_reason = f"max_replans_exceeded({max_replans})"
            logger.warning("loop_terminated", reason=termination_reason, step=step_idx)
            break
        if tracer:
            tracer.on_node_start(trace_id, f"react_step_{step_idx}", {}, step_idx)

        # ── Parse + retry loop ──
        retry_mgr = ParseRetryManager()
        action = None
        parse_ok = False

        while retry_mgr.should_retry():
            raw_output = _call_agent_llm_raw(messages)
            parse_result = retry_mgr.parse_and_validate(raw_output)

            if not parse_result.success:
                replan_count += 1

            if parse_result.success:
                action = parse_result.action
                parse_ok = True
                if parse_result.auto_fixed:
                    logger.info("json_auto_fixed", step=step_idx)
                break

            # Structural or schema error → feed back to model for self-correction
            if retry_mgr.should_retry():
                messages.append({"role": "assistant", "content": raw_output[:500]})
                messages.append({"role": "user", "content": parse_result.error})
                logger.info("parse_retry", step=step_idx,
                           retry=retry_mgr.retry_count,
                           error=parse_result.error[:100])

        if not parse_ok:
            # All retries exhausted → force-skip this step
            messages.append({"role": "assistant", "content": json.dumps({"action": "done", "summary": "输出格式错误，分析中止"}, ensure_ascii=False)})
            break

        step_record = {"step": step_idx, "action": action, "observation": None}
        steps.append(step_record)
        signature = f"{action.get('action')}|{action.get('tool', '')}|{json.dumps(action.get('args', {}), ensure_ascii=False, sort_keys=True)}"
        if signature in visited_signatures:
            replan_count += 1
        visited_signatures.append(signature)

        if action.get("action") == "done":
            termination_reason = "done"
            step_record["observation"] = "done"
            messages.append({"role": "assistant", "content": json.dumps(action, ensure_ascii=False)})
            break

        if action.get("action") != "tool":
            messages.append({"role": "assistant", "content": json.dumps(action, ensure_ascii=False)})
            messages.append({"role": "user", "content": "Invalid action. Use 'tool' or 'done'."})
            continue

        # Execute the tool through unified dispatcher
        tool = action.get("tool", "")
        args = action.get("args", {})
        dispatch = dispatcher.dispatch(tool, args, current_dataids)
        if not dispatch.ok:
            observation = _format_observation({"error": dispatch.error or f"Unknown tool: {tool}"}, dispatch.dataid or "?")
            messages.append({"role": "assistant", "content": json.dumps(action, ensure_ascii=False)})
            messages.append({"role": "user", "content": f"Tool error: {dispatch.error}. Try different arguments or another tool."})
            continue

        new_dataid = dispatch.dataid
        sample_rows = dispatch.sample_rows
        observation = dispatch.observation or _format_observation({"status": "ok"}, new_dataid or "?", sample_rows)

        if new_dataid and db is not None and tool in ("aggregate", "sort", "merge", "top", "filter_value", "compare_periods"):
            tool_call_count += 1
            if tool_call_count > max_tool_calls:
                termination_reason = f"max_tool_calls_exceeded({max_tool_calls})"
                observation = _format_observation({"error": termination_reason}, new_dataid)
                break
            try:
                chain_sql = rt.compile_sql(new_dataid, limit=20)
                rows = db.execute_cte(chain_sql)
                if isinstance(sample_rows, dict):
                    sample_rows["rows"] = rows
                    sample_rows["row_count"] = len(rows)
                final_sql = chain_sql
                final_results = rows
            except Exception as exec_err:
                observation = _format_observation({"error": str(exec_err)}, new_dataid, sample_rows)
                messages.append({"role": "assistant", "content": json.dumps(action, ensure_ascii=False)})
                messages.append({"role": "user", "content": f"SQL execution failed: {exec_err}. Try a different dimension, metric, or time range."})
                continue

        if termination_reason:
            break

        if action.get("action") == "done":
            break

        steps[-1]["observation"] = observation
        if tracer:
            tracer.on_node_end(trace_id, f"react_step_{step_idx}", {"action": action, "observation": observation[:200]}, step_idx, "ok")
        messages.append({"role": "assistant", "content": json.dumps(action, ensure_ascii=False)})
        if tool == "catalog":
            messages.append({"role": "user", "content": observation})
        else:
            messages.append({"role": "user", "content": f"OBSERVATION:\n{observation}\n\nAvailable dataids: {current_dataids}"})

        # Compact messages if approaching token budget (every 3 steps)
        if step_idx % 3 == 0:
            messages = compactor.compact(messages, system_tokens)

    # Generate final insight and chart
    insight_output = {"insight": action.get("summary", "分析完成。"),
                      "chart": {"type": "none", "reason": "no chart generated"}}

    if final_results and final_sql:
        try:
            analysis = _analyze(final_results,
                              dimensions=list(final_results[0].keys()) if final_results else [])
            insight_output = _nl_insight(query, final_sql, final_results, analysis)
        except ImportError:
            logger.warning("nlu_import_failed", hint="nlu module not available")
        except Exception as e:
            logger.warning("insight_generation_failed", error=str(e)[:200])

    if termination_reason is None and len(steps) >= max_steps:
        termination_reason = f"max_steps_exceeded({max_steps})"

    result = {
        "query": query,
        "steps": steps,
        "insight": insight_output.get("insight", "分析完成。"),
        "chart": insight_output.get("chart", {"type": "none"}),
        "sql": final_sql,
        "results": final_results,
        "trace": rt.trace,
        "termination_reason": termination_reason,
        "loop_stats": {
            "max_steps": max_steps,
            "max_tool_calls": max_tool_calls,
            "max_replans": max_replans,
            "step_count": len(steps),
            "tool_call_count": tool_call_count,
            "replan_count": replan_count,
            "visited_signatures": len(visited_signatures),
        },
    }

    if tracer:
        tracer.on_trace_end(trace_id, result)

    return result


# --- standardized react_loop response wrapper (added for regression contract) ---
_react_loop_impl = react_loop


def _standardize_react_result(result: dict, query: str, use_db: bool) -> dict:
    termination_reason = result.get("termination_reason")
    errors = result.get("errors") or []
    loop_stats = result.get("loop_stats") or {}
    status = "error" if errors or (termination_reason and "exceeded" in str(termination_reason)) else "ok"
    result.setdefault("status", status)
    result.setdefault("intent", None)
    result.setdefault("model", None)
    result.setdefault("metric", None)
    result.setdefault("dimensions", [])
    result.setdefault("time_range", None)
    result.setdefault("results_summary", None)
    result.setdefault("errors", errors)
    result.setdefault("execution", {
        "used_db": bool(use_db),
        "used_llm": bool(DEEPSEEK_KEY),
        "tool_calls": loop_stats.get("tool_call_count", 0),
        "step_count": loop_stats.get("step_count", len(result.get("steps") or [])),
    })
    result.setdefault("query", query)
    return result


def react_loop(query: str, use_db: bool = True, max_steps: int = MAX_STEPS,
               max_tool_calls: int = 12, max_replans: int = 4, tracer=None) -> dict:
    result = _react_loop_impl(
        query,
        use_db=use_db,
        max_steps=max_steps,
        max_tool_calls=max_tool_calls,
        max_replans=max_replans,
        tracer=tracer,
    )
    return _standardize_react_result(result, query, use_db)
