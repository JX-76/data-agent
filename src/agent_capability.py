# -*- coding: utf-8 -*-
"""Capability and ecommerce-scenario contracts for the Data Agent.

This module is deliberately declarative.  It does not decide routing or run
SQL; it is the shared vocabulary used by API responses, operational reviews,
and evaluation fixtures to make the supported business boundary explicit.
"""
from __future__ import unicode_literals


CAPABILITY_CONTRACT = "agent_capability_v1"
BUSINESS_COVERAGE_CONTRACT = "business_coverage_v1"

# `level` means the production boundary, not a marketing claim:
# supported = has a governed executable mainline; partial = bounded output or
# controlled fallback; planned = recognised but intentionally not executed.
CAPABILITIES = [
    {"id": "intent_recognition", "title": u"意图识别", "level": "supported",
     "components": ["intent_engine", "router_core", "semantic_registry"],
     "evidence": ["intent_rules", "intent_extreme_harness"]},
    {"id": "task_planning", "title": u"任务规划", "level": "supported",
     "components": ["AnalysisPlan", "plan_validator", "task_decomposer"],
     "evidence": ["analysis_plan_v2", "task_anchor"]},
    {"id": "tool_selection", "title": u"工具选择", "level": "partial",
     "components": ["execution_strategies", "external_tool_registry", "mcp_adapter"],
     "evidence": ["tool_policy", "tool_trace"]},
    {"id": "state_tracking", "title": u"状态追踪", "level": "supported",
     "components": ["task_anchor", "memory_policy", "clarification_state"],
     "evidence": ["session_id", "task_id", "trace_id"]},
    {"id": "failure_recovery", "title": u"失败重试", "level": "supported",
     "components": ["execution_engine", "timeout_guard", "circuit_breaker"],
     "evidence": ["retry_count", "retry_exhausted", "failure_type"]},
    {"id": "result_verification", "title": u"结果校验", "level": "supported",
     "components": ["sql_preflight", "semantic_registry", "data_quality", "credibility"],
     "evidence": ["sql_preflight", "quality", "credibility_v1"]},
    {"id": "observability", "title": u"可观测性", "level": "partial",
     "components": ["observability", "audit_logger", "apm"],
     "evidence": ["trace_summary", "failure_stage", "monitoring_v1"]},
    {"id": "evaluation_flywheel", "title": u"批量评测与反馈闭环", "level": "partial",
     "components": ["agent_harness", "harness_snapshot", "flywheel", "feedback_loop"],
     "evidence": ["harness_gate", "diagnosis", "feedback_proposal"]},
    {"id": "human_collaboration", "title": u"人机协同", "level": "supported",
     "components": ["clarification_state", "human_gate", "human_review_state"],
     "evidence": ["clarification_resume_v1", "human_review_v1"]},
]

# This is the ecommerce product boundary. It must be kept independent from
# intent rules: an intent can be recognised while a scenario remains planned.
ECOMMERCE_SCENARIOS = [
    {"id": "metric_overview", "title": u"经营指标总览", "task_types": ["descriptive"],
     "examples": [u"最近7天GMV", u"昨天订单量", u"本月客单价"], "level": "supported",
     "required_capabilities": ["intent_recognition", "task_planning", "result_verification"]},
    {"id": "dimension_breakdown", "title": u"渠道/品类/区域拆分", "task_types": ["breakdown", "descriptive"],
     "examples": [u"按渠道看GMV", u"各品类订单量"], "level": "supported",
     "required_capabilities": ["intent_recognition", "task_planning", "tool_selection"]},
    {"id": "period_comparison", "title": u"同比环比与周期对比", "task_types": ["comparison"],
     "examples": [u"昨天和上周订单量对比", u"本月和上月GMV对比"], "level": "partial",
     "required_capabilities": ["task_planning", "result_verification"]},
    {"id": "anomaly_diagnosis", "title": u"经营异常诊断", "task_types": ["anomaly"],
     "examples": [u"昨天GMV为什么下降", u"渠道订单异常"], "level": "partial",
     "required_capabilities": ["task_planning", "tool_selection", "result_verification"]},
    {"id": "contribution_analysis", "title": u"贡献/归因分析", "task_types": ["attribution"],
     "examples": [u"哪个渠道导致GMV下滑", u"品类贡献占比"], "level": "partial",
     "required_capabilities": ["task_planning", "tool_selection", "result_verification"]},
    {"id": "recommendation", "title": u"经营建议", "task_types": ["recommendation"],
     "examples": [u"如何提升淘宝GMV"], "level": "partial",
     "required_capabilities": ["result_verification", "human_collaboration"]},
    {"id": "funnel_retention_forecast", "title": u"漏斗、留存与预测", "task_types": ["funnel", "retention", "forecast"],
     "examples": [u"新客7日留存", u"下周GMV预测"], "level": "planned",
     "required_capabilities": ["task_planning", "tool_selection", "result_verification"]},
]


def capability_catalog():
    return {"contract": CAPABILITY_CONTRACT, "capabilities": [dict(x) for x in CAPABILITIES]}


def _as_dict(value):
    return value.to_dict() if hasattr(value, "to_dict") else dict(value or {})


def business_coverage(plan_or_result):
    """Return the explicit supported-business boundary for a request."""
    data = _as_dict(plan_or_result)
    task_type = data.get("task_type") or "descriptive"
    intent = data.get("intent")
    matched = []
    for scenario in ECOMMERCE_SCENARIOS:
        if task_type in scenario.get("task_types", []):
            matched.append(dict(scenario))
    if not matched and intent in ("metric_query", "breakdown", "comparison", "anomaly", "attribution", "recommendation"):
        for scenario in ECOMMERCE_SCENARIOS:
            if scenario["id"] == "metric_overview":
                matched.append(dict(scenario))
                break
    levels = [item.get("level") for item in matched]
    if "supported" in levels:
        overall = "supported"
    elif "partial" in levels:
        overall = "partial"
    elif matched:
        overall = "planned"
    else:
        overall = "unclassified"
    return {
        "contract": BUSINESS_COVERAGE_CONTRACT,
        "task_type": task_type,
        "intent": intent,
        "coverage_level": overall,
        "scenarios": matched,
        "requires_human_confirmation": bool(data.get("clarification") or data.get("requires_human_review")),
    }


__all__ = ["capability_catalog", "business_coverage", "CAPABILITY_CONTRACT", "BUSINESS_COVERAGE_CONTRACT"]
