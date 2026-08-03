# -*- coding: utf-8 -*-
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))

from dag_routing import infer_execution_mode, route_and_plan
from router_core import build_clarification, detect_blocked_query, ensure_plan_defaults, parse_time_range_label


def test_router_core_clarification_policy():
    clarification = build_clarification(metric="gmv", reason="need scope")
    assert clarification["metric"] == "gmv"
    assert clarification["question"] == "请选择分析口径"
    assert clarification["options"]
    assert clarification["expected_next_step"]


def test_router_core_time_range_and_blocking():
    start, end = parse_time_range_label("last7d")
    assert start <= end

    blocked, reason = detect_blocked_query("delete from orders")
    assert blocked is True
    assert reason

    blocked, reason = detect_blocked_query("show gmv by channel")
    assert blocked is False
    assert reason is None


def test_router_core_plan_defaults():
    plan = ensure_plan_defaults({"metric": "gmv"}, "show gmv")
    assert plan["status"] == "ok"
    assert plan["metrics"] == ["gmv"]
    assert "time_range" in plan

    clarification = build_clarification()
    assert "question" in clarification
    assert "expected_next_step" in clarification


def test_conversion_rate_regex_route_beats_template_fallback():
    plan = route_and_plan("本周转化率是多少？", use_llm=False)
    assert plan["status"] == "ok"
    assert plan["metric"] == "conversion_rate"
    assert plan["diagnostics"]["route_path"] == "router_node"


def test_execution_mode_policy_defaults_and_react_cases():
    assert infer_execution_mode("最近7天GMV", "descriptive", 0.95, "ok") == "plan_act"
    assert infer_execution_mode("最近7天GMV按渠道", "descriptive", 0.95, "ok") == "plan_act"
    assert infer_execution_mode("先看一下有哪些表和字段", "descriptive", 0.5, "need_clarification") == "react"
    assert infer_execution_mode("昨天GMV异常下钻分析", "anomaly", 0.9, "ok") == "react"


def test_route_and_plan_carries_execution_mode():
    metric_plan = route_and_plan("最近7天GMV", use_llm=False)
    assert metric_plan["execution_mode"] == "plan_act"

    react_plan = route_and_plan("昨天GMV异常下钻分析", use_llm=False)
    assert react_plan["execution_mode"] == "react"
    assert react_plan["diagnostics"]["execution_mode_reason"] == "business_intent_policy"
