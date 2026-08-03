# -*- coding: utf-8 -*-
"""Result explanation helpers.

This module turns a routed/executed analysis result into a concise insight
bundle without hard-coding presentation logic into the facade.
"""

from schemas import InsightBundle


try:
    unicode
except NameError:  # pragma: no cover - Python 3 compatibility
    unicode = str


def _to_unicode(value):
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


def _to_native_text(value):
    """Return a native string (always unicode in Python 3, bytes in Python 2)."""
    text = _to_unicode(value)
    if isinstance(text, bytes):
        # Already bytes (Python 2 path)
        return text
    # Python 3: keep as unicode str — do not encode to bytes
    return text


def _append_unique_native(container, value):
    native = _to_native_text(value)
    if native and native not in container:
        container.append(native)


def _is_clarification(status):
    return status in ("need_clarification", "clarification_needed")


def _select_chart(exec_dict, plan_dict):
    chart = exec_dict.get("chart") or {}
    if chart:
        return chart

    if plan_dict.get("status") in ("blocked", "need_clarification", "clarification_needed"):
        return {"type": "none", "reason": u"non-final result"}

    if plan_dict.get("dimensions"):
        return {"type": "bar", "reason": u"dimension breakdown"}

    return {"type": "none", "reason": u"no chart generated"}


def _summary_for_status(status, metric=None, dimensions=None, time_range=None):
    if status == "blocked":
        return [u"本次请求被安全规则拦截。"]
    if _is_clarification(status):
        return [u"本次请求需要先澄清口径。"]

    parts = [u"分析已完成。"]
    if metric:
        parts.append(u"核心指标是 %s。" % _to_unicode(metric))
    if dimensions:
        parts.append(u"拆分维度：%s。" % u", ".join(_to_unicode(item) for item in dimensions))
    if time_range:
        parts.append(u"时间范围已确定。")
    return parts


def build_insight_bundle(plan, execution_result):
    plan_dict = plan.to_dict() if hasattr(plan, "to_dict") else dict(plan or {})
    exec_dict = execution_result.to_dict() if hasattr(execution_result, "to_dict") else dict(execution_result or {})

    status = exec_dict.get("status") or plan_dict.get("status") or "ok"
    metric = exec_dict.get("metric") or plan_dict.get("metric")
    dimensions = exec_dict.get("dimensions") or plan_dict.get("dimensions") or []
    time_range = exec_dict.get("time_range") or plan_dict.get("time_range")
    task_type = exec_dict.get("task_type") or plan_dict.get("task_type") or "descriptive"
    analysis = exec_dict.get("analysis") or {}

    summary_parts = _summary_for_status(status, metric=metric, dimensions=dimensions, time_range=time_range)
    facts = analysis.get("summary_facts") if isinstance(analysis, dict) else {}
    if task_type == "comparison":
        summary_parts.append(u"本次按对比分析模板输出，可关注 delta 和 delta_pct。")
        if facts and facts.get("delta") is not None:
            summary_parts.append(u"变化量为 %s。" % _to_unicode(facts.get("delta")))
    elif task_type == "anomaly":
        summary_parts.append(u"本次按异常检测模板输出，可关注 anomalies。")
        if facts and facts.get("anomaly_count") is not None:
            summary_parts.append(u"共识别 %s 个异常点。" % _to_unicode(facts.get("anomaly_count")))
    elif task_type == "attribution":
        summary_parts.append(u"本次按归因分析模板输出，可关注 top_drivers。")
        if facts and facts.get("driver_count") is not None:
            summary_parts.append(u"已识别 %s 个主要驱动因素。" % _to_unicode(facts.get("driver_count")))
    elif task_type in ("funnel", "retention"):
        summary_parts.append(u"该分析类型已识别，当前返回稳定分析结构。")

    caveats = []
    diagnostics = exec_dict.get("diagnostics") or {}
    grain_rewrite = diagnostics.get("grain_rewrite")
    if grain_rewrite is None:
        grain_rewrite = ((diagnostics.get("strategy_metadata") or {}).get("compiled_sql") or {}).get("grain_rewrite")
    quality = diagnostics.get("quality") or {}
    quality_messages = quality.get("messages") or []
    if plan_dict.get("clarification"):
        _append_unique_native(caveats, u"需要用户确认口径后再继续。")
    if exec_dict.get("errors"):
        failure_type = diagnostics.get("failure_type")
        if failure_type:
            _append_unique_native(caveats, u"执行阶段存在错误，失败类型：%s。" % _to_unicode(failure_type))
        else:
            _append_unique_native(caveats, u"执行阶段存在错误，请检查 SQL 或运行环境。")
    if quality.get("empty_result"):
        _append_unique_native(caveats, u"本次查询结果为空，可能是时间范围无数据、过滤条件过严或数据尚未更新。")
    if grain_rewrite and grain_rewrite.get("selected"):
        _append_unique_native(caveats, u"本次结果已按事实表粒度先聚合再关联维表，以降低 join fanout 带来的重复计数风险。")
        if grain_rewrite.get("semijoin_pushdowns"):
            _append_unique_native(caveats, u"维表过滤已通过半连接下推到事实表聚合前执行，避免过滤后再聚合造成口径偏差。")
    elif grain_rewrite and grain_rewrite.get("reason"):
        _append_unique_native(caveats, u"本次未启用粒度预聚合改写，原因：%s。" % _to_unicode(grain_rewrite.get("reason")))
    for msg in quality_messages:
        _append_unique_native(caveats, msg)

    next_steps = []
    if status == "ok" and quality.get("empty_result"):
        next_steps.append(u"可以放宽时间范围后重试。")
        next_steps.append(u"可以减少过滤条件或换一个维度查看。")
    elif status == "ok":
        next_steps.append(u"可以继续按更细维度下钻。")
        next_steps.append(u"可以切换时间窗口做对比。")
    elif _is_clarification(status):
        next_steps.append(u"先补充时间范围或分析口径。")
    elif status == "blocked":
        next_steps.append(u"改写为只读分析问题后重试。")

    if isinstance(analysis, dict):
        if task_type == "comparison" and analysis.get("comparison", {}).get("status") == "ok":
            next_steps.append(u"可以继续查看贡献最大的维度拆解。")
        if task_type == "anomaly" and analysis.get("anomalies"):
            _append_unique_native(caveats, u"检测到异常点，建议核查活动、渠道或数据同步情况。")
        if task_type == "attribution" and analysis.get("top_drivers"):
            next_steps.append(u"优先查看 top_drivers 中贡献最大的维度。")
        for message in (analysis.get("data_quality") or {}).get("messages") or []:
            _append_unique_native(caveats, message)

    raw = {
        "plan": plan_dict,
        "execution": exec_dict,
        "analysis": analysis,
        # Compatibility alias for clients consuming the pre-Phase-6 field.
        "advanced_analysis": analysis,
    }

    chart = _select_chart(exec_dict, plan_dict)
    return InsightBundle(
        summary=_to_native_text(u" ".join(summary_parts).strip()),
        chart=chart,
        caveats=[_to_native_text(item) for item in caveats],
        next_steps=[_to_native_text(item) for item in next_steps],
        raw=raw,
    )


__all__ = ["build_insight_bundle"]
