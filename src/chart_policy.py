# -*- coding: utf-8 -*-
"""Chart selection policy for analysis results."""

from chart_spec import make_chart_spec, normalize_chart_spec, recommend_chart_for_task_type

SUPPORTED_NONE_STATUSES = {
    "blocked", "need_clarification", "clarification_needed", "error",
    "fallback", "pending_human_review",
}


def _metric(plan_dict, exec_dict):
    return plan_dict.get("metric") or exec_dict.get("metric") or "value"


def _dimension(plan_dict, exec_dict):
    dimensions = plan_dict.get("dimensions") or exec_dict.get("dimensions") or []
    return dimensions[0] if dimensions else None


def _dimensions(plan_dict, exec_dict):
    return plan_dict.get("dimensions") or exec_dict.get("dimensions") or []


def _result_data(exec_dict):
    return exec_dict.get("results") or exec_dict.get("rows") or []


def _quality(exec_dict):
    diagnostics = exec_dict.get("diagnostics") or {}
    return diagnostics.get("quality") or {}


def select_chart(plan_dict=None, exec_dict=None):
    plan_dict = plan_dict or {}
    exec_dict = exec_dict or {}

    chart = exec_dict.get("chart") or {}
    if chart:
        return normalize_chart_spec(chart)

    status = plan_dict.get("status") or exec_dict.get("status")
    if status in SUPPORTED_NONE_STATUSES:
        return recommend_chart_for_task_type(
            plan_dict.get("task_type") or exec_dict.get("task_type") or "descriptive",
            status=status,
        )

    quality = _quality(exec_dict)
    data = _result_data(exec_dict)
    results_summary = exec_dict.get("results_summary") or {}
    empty_result = bool(quality.get("empty_result"))
    if not empty_result and results_summary.get("row_count") == 0:
        empty_result = True
    if not empty_result and "results" in exec_dict and not data:
        empty_result = True
    if empty_result:
        return recommend_chart_for_task_type(
            plan_dict.get("task_type") or exec_dict.get("task_type") or "descriptive",
            empty_result=True,
        )

    task_type = plan_dict.get("task_type") or exec_dict.get("task_type") or "descriptive"
    metric = _metric(plan_dict, exec_dict)
    dim = _dimension(plan_dict, exec_dict)
    dims = _dimensions(plan_dict, exec_dict)
    analysis = exec_dict.get("analysis") or {}
    intent = plan_dict.get("intent") or exec_dict.get("intent") or ""

    if task_type == "experiment":
        facts = (analysis.get("summary_facts") or (data[0] if data else {}))
        return make_chart_spec(type="grouped_bar", title="A/B 实验对比", x="group", y="value", data=[{"group": "control", "value": facts.get("control_value")}, {"group": "treatment", "value": facts.get("treatment_value")}], annotations=[{"confidence_interval": facts.get("confidence_interval"), "p_value": facts.get("p_value")}], reason="comparison")
    if task_type == "comparison" or plan_dict.get("compare_periods") or plan_dict.get("time_compare"):
        return make_chart_spec(type="grouped_bar" if dim else "line", title="对比分析", x=dim or "date", y=metric, series="period", data=analysis.get("items") or data, reason="comparison")
    if task_type == "anomaly":
        return make_chart_spec(type="line_with_anomaly", title="异常检测", x=analysis.get("definition", {}).get("time_dimension", "date"), y=metric, data=data, annotations=analysis.get("anomalies") or exec_dict.get("anomalies") or [], reason="anomaly timeline")
    if task_type == "attribution":
        return make_chart_spec(type="waterfall", title="归因贡献", x=dim or "driver", y="delta", data=analysis.get("top_drivers") or data, reason="driver contribution")
    if task_type == "funnel":
        return make_chart_spec(type="funnel", title="漏斗分析", x="stage", y=metric, data=data, reason="funnel conversion")
    if task_type == "retention":
        return make_chart_spec(type="heatmap", title="留存分析", x="cohort", y="period", data=data, reason="cohort retention")

    if intent in ("trend", "compare_periods", "time_compare", "yoy", "mom") or dim == "date":
        return make_chart_spec(type="line", title="趋势分析", x="date", y=metric, data=data, reason="trend analysis")
    if dim:
        if len(dims) > 1:
            return make_chart_spec(type="grouped_bar", title="多维交叉分析", x=dim, y=metric, series=dims[1], data=data, reason="multi-dimension breakdown")
        return make_chart_spec(type="bar", title="维度拆解", x=dim, y=metric, data=data, reason="dimension breakdown")
    tr = plan_dict.get("time_range") or exec_dict.get("time_range")
    if tr and isinstance(tr, (list, tuple)) and len(tr) == 2:
        try:
            from datetime import datetime
            if isinstance(tr[0], datetime) and isinstance(tr[1], datetime):
                days = (tr[1] - tr[0]).days
                if days >= 2:
                    return make_chart_spec(type="line", title="趋势分析", x="date", y=metric, data=data, reason="trend analysis")
        except Exception:
            pass
    return recommend_chart_for_task_type(task_type)


__all__ = ["select_chart"]
