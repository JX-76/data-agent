# -*- coding: utf-8 -*-
"""Stable product-facing analysis output contract.

This module is the single adapter from internal analysis/insight objects to the
payload shape that UI/API clients can safely consume. It intentionally avoids
business-specific hard-coding in AgentFacade.
"""

try:
    unicode
except NameError:  # pragma: no cover
    unicode = str


def _as_dict(value):
    if value is None:
        return {}
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if isinstance(value, dict):
        return value
    return {}


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _text(value):
    if value is None:
        return u""
    if isinstance(value, unicode):
        return value
    try:
        return value.decode("utf-8")
    except Exception:
        try:
            return unicode(value)
        except Exception:
            return u"%s" % value


def _append_unique(items, value):
    value = _text(value)
    if value and value not in items:
        items.append(value)


def _extract_insight(insight):
    data = _as_dict(insight)
    return {
        "summary": data.get("summary") or data.get("headline"),
        "chart": data.get("chart") or {},
        "caveats": _as_list(data.get("caveats")),
        "next_steps": _as_list(data.get("next_steps")),
        "raw": dict(data.get("raw") or {}),
    }


def _build_evidence(plan, execution_result, analysis):
    plan = _as_dict(plan)
    execution_result = _as_dict(execution_result)
    diagnostics = execution_result.get("diagnostics") or {}
    results_summary = execution_result.get("results_summary") or {}
    definition = analysis.get("definition") if isinstance(analysis, dict) else {}
    if not isinstance(definition, dict):
        definition = {}
    return {
        "metric": execution_result.get("metric") or plan.get("metric") or definition.get("metric"),
        "dimensions": execution_result.get("dimensions") or plan.get("dimensions") or definition.get("dimensions") or [],
        "time_range": execution_result.get("time_range") or plan.get("time_range") or definition.get("time_range"),
        "filters": plan.get("filters") or [],
        "row_count": results_summary.get("row_count"),
        "source": results_summary.get("source"),
        # Retention definition metadata is additive and contains no entity rows.
        "cohort_definition": definition if execution_result.get("task_type") == "retention" or plan.get("task_type") == "retention" else None,
        "sql_available": bool(execution_result.get("sql")),
        "quality": diagnostics.get("quality") or {},
        "confidence": execution_result.get("confidence") or diagnostics.get("confidence"),
    }


def standardize_analysis_output(plan, execution_result, analysis=None, insight=None):
    """Return stable analysis payload for all final statuses.

    Shape:
      summary, key_findings, evidence, caveats, next_steps, chart, raw
    """
    plan_dict = _as_dict(plan)
    exec_dict = _as_dict(execution_result)
    analysis_dict = _as_dict(analysis if analysis is not None else exec_dict.get("analysis"))
    insight_dict = _extract_insight(insight if insight is not None else exec_dict.get("insight"))
    status = exec_dict.get("status") or plan_dict.get("status") or "ok"
    task_type = exec_dict.get("task_type") or plan_dict.get("task_type") or analysis_dict.get("type") or "descriptive"

    summary = insight_dict.get("summary")
    if not summary:
        if status == "blocked":
            summary = u"本次请求被安全或治理策略拦截。"
        elif status == "need_clarification":
            summary = u"本次请求需要补充口径后继续。"
        elif status == "pending_human_review":
            summary = u"本次请求需要人工审核后继续。"
        elif status == "error":
            summary = u"本次分析执行失败，请查看诊断信息。"
        elif status == "fallback":
            summary = u"本次分析已进入降级路径。"
        else:
            summary = u"分析已完成。"

    key_findings = []
    facts = analysis_dict.get("summary_facts") or {}
    if facts.get("row_count") is not None:
        _append_unique(key_findings, u"返回 %s 行结果。" % _text(facts.get("row_count")))
    if task_type == "comparison" and facts.get("delta") is not None:
        direction = facts.get("direction") or ("increase" if facts.get("delta") >= 0 else "decrease")
        direction_text = u"增长" if direction == "increase" else (u"下降" if direction == "decrease" else u"持平")
        pct = facts.get("delta_pct")
        pct_text = u"，变化率 %.1f%%" % (pct * 100) if pct is not None else u""
        _append_unique(key_findings, u"核心指标%s %s%s。" % (direction_text, _text(facts.get("delta")), pct_text))
        top_inc = facts.get("top_increase") or {}
        top_dec = facts.get("top_decrease") or {}
        if isinstance(top_inc, dict) and top_inc.get("dimension"):
            _append_unique(key_findings, u"增长贡献最大的维度值是 %s。" % _text(top_inc.get("dimension")))
        if isinstance(top_dec, dict) and top_dec.get("dimension") and top_dec.get("delta", 0) < 0:
            _append_unique(key_findings, u"下降拖累最大的维度值是 %s。" % _text(top_dec.get("dimension")))
    if task_type == "anomaly" and facts.get("anomaly_count") is not None:
        _append_unique(key_findings, u"识别到 %s 个异常点。" % _text(facts.get("anomaly_count")))
        if facts.get("max_severity"):
            _append_unique(key_findings, u"最高异常等级为 %s。" % _text(facts.get("max_severity")))
        if facts.get("latest_anomaly_time"):
            _append_unique(key_findings, u"最近异常发生在 %s。" % _text(facts.get("latest_anomaly_time")))
    if task_type == "attribution" and facts.get("driver_count") is not None:
        _append_unique(key_findings, u"识别到 %s 个主要驱动因素。" % _text(facts.get("driver_count")))
        if facts.get("primary_driver"):
            pct = facts.get("primary_driver_pct")
            pct_text = u"，贡献占比 %.1f%%" % (pct * 100) if pct is not None else u""
            _append_unique(key_findings, u"首要驱动因素是 %s%s。" % (_text(facts.get("primary_driver")), pct_text))
    if task_type == "retention":
        if facts.get("cohort_count") is not None:
            _append_unique(key_findings, u"生成 %s 个 cohort 的聚合留存矩阵。" % _text(facts.get("cohort_count")))
        horizons = facts.get("horizons") or []
        if horizons:
            _append_unique(key_findings, u"观察周期包括 %s。" % u"、".join([_text(item) for item in horizons]))
    if task_type == "forecast":
        if facts.get("forecast_point_count") is not None:
            _append_unique(key_findings, u"已生成 %s 个预测点。" % _text(facts.get("forecast_point_count")))
        if facts.get("method"):
            _append_unique(key_findings, u"预测算法为 %s。" % _text(facts.get("method")))
        if facts.get("backtest_available"):
            mape = facts.get("backtest_mape")
            if mape is not None:
                _append_unique(key_findings, u"回测 MAPE 为 %.2f%%。" % (mape * 100))
            _append_unique(key_findings, u"回测%s阈值检验。" % (u"通过" if facts.get("backtest_passed") else u"未通过"))
    if not key_findings and status == "ok":
        _append_unique(key_findings, u"结果已按当前计划生成。")

    caveats = []
    for item in insight_dict.get("caveats") or []:
        _append_unique(caveats, item)
    diagnostics = exec_dict.get("diagnostics") or {}
    if diagnostics.get("failure_type"):
        _append_unique(caveats, u"失败类型：%s。" % _text(diagnostics.get("failure_type")))
    quality = diagnostics.get("quality") or {}
    if quality.get("empty_result"):
        _append_unique(caveats, u"查询结果为空，可能是时间范围无数据或过滤条件过严。")

    next_steps = []
    for item in insight_dict.get("next_steps") or []:
        _append_unique(next_steps, item)
    if not next_steps:
        if status == "ok" and task_type == "anomaly":
            for item in analysis_dict.get("drill_down_suggestions") or []:
                _append_unique(next_steps, item)
            if not next_steps:
                _append_unique(next_steps, u"建议按渠道、品类或地区下钻异常点。")
        elif status == "ok" and task_type == "attribution":
            _append_unique(next_steps, u"建议继续查看 top drivers 的明细样本与变化来源。")
        elif status == "ok" and task_type == "forecast":
            _append_unique(next_steps, u"建议结合回测误差与业务事件复核预测结果，并避免将预测作为因果结论。")
        elif status == "ok":
            _append_unique(next_steps, u"可以继续按维度下钻或切换时间窗口对比。")
        elif status == "need_clarification":
            _append_unique(next_steps, u"请先选择系统给出的澄清选项。")
        elif status == "blocked":
            _append_unique(next_steps, u"请改写为只读分析问题。")

    chart = insight_dict.get("chart") or exec_dict.get("chart") or {"type": "none"}
    if "type" not in chart:
        chart = dict(chart)
        chart.setdefault("type", "none")
    try:
        from chart_spec import recommend_chart_for_task_type
        empty_result = bool((exec_dict.get("diagnostics") or {}).get("quality", {}).get("empty_result"))
        if status != "ok" or empty_result or chart.get("type") in (None, "", "none"):
            chart = recommend_chart_for_task_type(
                task_type,
                status=None if status == "ok" and not empty_result else status,
                empty_result=empty_result,
            )
    except Exception:
        pass

    return {
        "contract": "analysis_output_v1",
        "type": task_type,
        "status": analysis_dict.get("status") or status,
        "summary": _text(summary),
        "key_findings": key_findings,
        "evidence": _build_evidence(plan_dict, exec_dict, analysis_dict),
        "caveats": caveats,
        "next_steps": next_steps,
        "chart": chart,
        "raw": {
            "source_analysis": analysis_dict,
            "source_insight": insight_dict,
        },
    }


__all__ = ["standardize_analysis_output"]
