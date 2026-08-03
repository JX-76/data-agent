# -*- coding: utf-8 -*-
"""Contract tests for standardized agent results."""

import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, "src"))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from contracts import normalize_analysis_plan, normalize_result, validate_response_contract
from task_types import COMPARISON, DESCRIPTIVE, FUNNEL, infer_task_type
from analysis_output import standardize_analysis_output


def test_normalize_result_fills_required_fields():
    result = normalize_result({"status": "ok", "results": []}, query="昨天 GMV 是多少？")

    assert result["query"] == "昨天 GMV 是多少？"
    assert result["status"] == "ok"
    assert "intent" in result
    assert "model" in result
    assert "metric" in result
    assert "dimensions" in result
    assert "steps" in result
    assert "errors" in result
    assert "loop_stats" in result
    assert "execution" in result
    assert "plan" in result
    assert "analysis" in result
    assert "diagnostics" in result
    assert result["execution"]["step_count"] == 0
    assert result["execution_mode"] == "plan_act"
    ok, missing = validate_response_contract(result)
    assert ok is True
    assert missing == []


def test_normalize_result_keeps_existing_execution_metadata():
    result = normalize_result(
        {
            "status": "ok",
            "execution": {"used_db": True, "custom": "x"},
            "loop_stats": {"step_count": 3, "tool_call_count": 2},
        },
        query="各渠道 GMV",
        used_db=False,
        used_llm=True,
    )

    assert result["execution"]["used_db"] is True
    assert result["execution"]["used_llm"] is True
    assert result["execution"]["custom"] == "x"
    assert result["execution"]["step_count"] == 3
    assert result["execution"]["tool_calls"] == 2


def test_normalize_analysis_plan_fills_required_fields():
    plan = normalize_analysis_plan(
        {"status": "clarification_needed", "metric": "gmv", "dimensions": ["channel"]},
        query="按渠道看GMV",
        task_id="task-1",
    )

    assert plan["query"] == "按渠道看GMV"
    assert plan["status"] == "need_clarification"
    assert plan["metric"] == "gmv"
    assert plan["dimensions"] == ["channel"]
    assert plan["task_id"] == "task-1"
    assert plan["schema_version"] == "v2"
    assert plan["plan_version"] == "v2"
    assert plan["resume_payload"] == {}
    assert plan["execution_mode"] == "plan_act"
    assert plan["join_strategy"]["mode"] == "semantic"
    assert plan["verification_policy"]["mode"] == "basic"
    assert plan["fallback_policy"]["mode"] == "safe_fail"
    assert plan["diagnostics"]["plan_validation"]["valid"] is True


def test_normalize_analysis_plan_handles_invalid_input():
    plan = normalize_analysis_plan(None)

    assert plan["status"] == "error"
    assert plan["diagnostics"]["reason"] == "missing query"


def test_response_contract_for_all_final_statuses():
    samples = [
        {"status": "ok", "results": [], "analysis": {"summary": "done"}},
        {"status": "blocked", "blocked_reason": "dangerous query", "errors": []},
        {"status": "clarification_needed", "clarification": {"question": "请选择时间范围"}},
        {"status": "fallback", "fallback_reason": "strategy_not_available", "errors": []},
        {"status": "pending_human_review", "approval_status": "pending", "errors": []},
        {"status": "error", "errors": [{"phase": "execute", "error": "db unavailable"}]},
    ]

    for sample in samples:
        result = normalize_result(sample, query="最近7天GMV", session_id="s1", trace_id="tr1")
        ok, missing = validate_response_contract(result)
        assert ok is True
        assert missing == []
        assert result["diagnostics"]["response_contract"] == "v1"
        assert result["query"] == "最近7天GMV"
        assert "plan" in result
        assert "execution" in result
        assert "analysis" in result


def test_pending_human_review_is_not_collapsed_to_clarification():
    result = normalize_result(
        {"status": "pending_human_review", "approval_status": "pending", "requires_human_review": True},
        query="导出明细数据",
    )
    assert result["status"] == "pending_human_review"
    assert result["diagnostics"]["terminal"]["status"] == "pending_human_review"
    assert result["diagnostics"]["terminal"]["reason"] == "pending"


def test_fallback_reason_is_stable_contract_field():
    result = normalize_result(
        {"status": "fallback", "fallback_reason": "compiler_not_available"},
        query="复杂归因分析",
    )
    assert result["status"] == "fallback"
    assert result["fallback_reason"] == "compiler_not_available"
    assert result["diagnostics"]["terminal"]["reason"] == "compiler_not_available"
    ok, missing = validate_response_contract(result)
    assert ok is True
    assert missing == []


def test_response_contract_keeps_report_and_elapsed_ms():
    result = normalize_result(
        {"status": "ok", "report": {"format": "json"}, "elapsed_ms": 123},
        query="昨天GMV",
    )

    assert result["report"] == {"format": "json"}
    assert result["elapsed_ms"] == 123
    ok, missing = validate_response_contract(result)
    assert ok is True
    assert missing == []


def test_infer_task_type_defaults_to_descriptive():
    assert infer_task_type("最近7天GMV") == DESCRIPTIVE
    assert infer_task_type("按渠道对比上周和本周订单量") == COMPARISON
    assert infer_task_type("看用户漏斗转化") == FUNNEL


def test_normalize_plan_carries_task_type():
    plan = normalize_analysis_plan({"intent": "comparison"}, query="对比昨天和上周GMV")
    assert plan["task_type"] == COMPARISON


def test_execution_mode_is_preserved_in_plan_and_result_contracts():
    plan = normalize_analysis_plan({"intent": "anomaly", "execution_mode": "react"}, query="昨天GMV异常下钻分析")
    assert plan["execution_mode"] == "react"

    result = normalize_result({"status": "ok", "plan": plan.to_dict(), "execution_mode": "react"}, query="昨天GMV异常下钻分析")
    assert result["execution_mode"] == "react"
    ok, missing = validate_response_contract(result)
    assert ok is True
    assert missing == []


def test_response_fixtures_match_contract():
    fixture_dir = os.path.join(os.path.dirname(__file__), "fixtures", "responses")
    for name in ["ok.json", "blocked.json", "need_clarification.json", "error.json"]:
        with open(os.path.join(fixture_dir, name), "r", encoding="utf-8") as f:
            payload = json.load(f)
        ok, missing = validate_response_contract(payload)
        assert ok is True
        assert missing == []
        assert "task_type" in payload["plan"]


def test_analysis_plan_to_dict_exports_decomposition_metadata():
    plan = normalize_analysis_plan(
        {
            "metric": "gmv",
            "dimensions": ["channel"],
            "sub_plans": [{"metric": "gmv"}],
            "decompose_strategy": "split_by_metric",
            "decompose_reason": "multi metric",
        },
        query="按渠道看GMV",
    )
    data = plan.to_dict()
    assert data["sub_plans"] == [{"metric": "gmv"}]
    assert data["decompose_strategy"] == "split_by_metric"
    assert data["decompose_reason"] == "multi metric"


def test_standardized_analysis_output_contract_for_success():
    payload = standardize_analysis_output(
        {"metric": "gmv", "dimensions": ["channel"], "time_range": "last_7d"},
        {
            "status": "ok",
            "results_summary": {"row_count": 2, "source": "sqlite"},
            "diagnostics": {"quality": {"empty_result": False}, "confidence": 0.86},
        },
        analysis={"type": "descriptive", "summary_facts": {"row_count": 2}},
        insight={"summary": "分析已完成", "chart": {"type": "bar"}},
    )
    assert payload["contract"] == "analysis_output_v1"
    assert payload["summary"] == "分析已完成"
    assert payload["key_findings"]
    assert payload["evidence"]["metric"] == "gmv"
    assert payload["evidence"]["dimensions"] == ["channel"]
    assert payload["chart"]["type"] == "bar"


def test_standardized_analysis_output_contract_for_terminal_status():
    payload = standardize_analysis_output(
        {},
        {"status": "blocked", "diagnostics": {"failure_type": "dangerous_query"}},
        analysis={},
        insight={},
    )
    assert payload["contract"] == "analysis_output_v1"
    assert payload["summary"]
    assert "失败类型：dangerous_query。" in payload["caveats"]
    assert payload["next_steps"]
