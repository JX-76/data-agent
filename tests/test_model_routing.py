# -*- coding: utf-8 -*-
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))

from dag_routing import route_and_plan
from model_routing import (
    TIER_LARGE,
    TIER_NO_LLM,
    TIER_SMALL,
    choose_model_for_stage,
    should_escalate_to_large,
)


def test_model_routing_policy_off_is_no_llm():
    decision = choose_model_for_stage("最近7天GMV", use_llm=False)
    assert decision.tier == TIER_NO_LLM
    assert decision.model is None
    assert "policy_off" in decision.reason_codes


def test_model_routing_auto_splits_small_and_large():
    small = choose_model_for_stage("最近7天GMV", use_llm=True)
    assert small.tier == TIER_SMALL
    assert small.model

    large = choose_model_for_stage("昨天GMV异常下钻归因分析", use_llm=True)
    assert large.tier == TIER_LARGE
    assert large.model
    assert large.token_budget > 0


def test_model_routing_shadow_records_but_does_not_call_llm():
    plan = route_and_plan("最近7天GMV", llm_policy="shadow")
    routing = plan["diagnostics"]["model_routing"]
    assert routing["contract"] == "model_route_decision_v1"
    assert routing["llm_policy"] == "shadow"
    assert routing["shadow"] is True
    assert plan["source"] in ("intent_engine", "rule")


def test_route_and_plan_attaches_no_llm_decision_without_breaking_route_path():
    plan = route_and_plan("本周转化率是多少？", use_llm=False)
    assert plan["diagnostics"]["route_path"] == "router_node"
    routing = plan["model_routing"]
    assert routing["tier"] == TIER_NO_LLM
    assert routing["contract"] == "model_route_decision_v1"


def test_escalation_policy_blocks_execution_and_permission_failures():
    assert should_escalate_to_large(error_code="schema_invalid")[0] is True
    assert should_escalate_to_large(error_code="permission_denied")[0] is False
    assert should_escalate_to_large(error_code="sql_execution_error")[0] is False
    assert should_escalate_to_large(error_code="empty_result")[0] is False
