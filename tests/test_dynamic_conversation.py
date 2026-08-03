# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from dynamic_conversation_dataset import load_initial_cases, summarize_initial_cases, validate_initial_case
from dynamic_conversation_runner import DynamicConversationRunner, choose_dynamic_followup, evaluate_dynamic_runs, sanitize_turn_envelope, summarize_runs
from agent_facade import AgentFacade

CASES = os.path.join(ROOT, "evaluation", "conversation_cases", "ecommerce_dynamic_initial_questions_100.jsonl")


def _case(sample_type="positive", category="metric_overview"):
    return {"case_id": "fixture", "initial_question": "最近 7 天 GMV 怎么样？", "max_turns": 10,
            "category": category, "metadata": {"sample_type": sample_type}}


def test_seed_dataset_has_100_initial_only_cases_and_required_mix():
    cases = load_initial_cases(CASES)
    summary = summarize_initial_cases(cases)
    assert summary["valid"] is True
    assert summary["total"] == 100
    assert summary["by_scenario"]["ecommerce"] == 90
    assert summary["by_sample_type"] == {"positive": 70, "negative": 20, "generic_control": 10}
    assert all("followups" not in row and "turns" not in row for row in cases)


def test_seed_validator_rejects_pre_authored_followup():
    row = {"contract": "dynamic_conversation_initial_case_v1", "case_id": "x", "initial_question": "x",
           "scenario": "ecommerce", "max_turns": 10, "expected_safe_outcomes": ["ok"],
           "metadata": {"sample_type": "positive"}, "followups": ["not allowed"]}
    assert "seed_dataset_must_not_contain_pre_authored_followups" in validate_initial_case(row)


def test_dynamic_policy_uses_actual_clarification_response():
    question, reason, terminal = choose_dynamic_followup(_case(),
        {"status": "need_clarification", "clarification": {"prompt": "请补充时间范围"}}, 1)
    assert "最近 7 天" in question
    assert reason == "clarification_time_scope"
    assert terminal is None


def test_sanitized_error_keeps_structured_safe_error_and_excludes_internal_message():
    envelope = {"status": "error", "terminal": "error", "audit_id": "audit_123",
                "error": {"message": "Traceback: internal/path.py"},
                "safe_error": {"contract": "safe_error_v1", "stage": "execution",
                               "code": "schema_error", "retryable": False,
                               "remediation": "correct_request_or_supported_schema",
                               "trace_ref": "audit_123"}}
    sanitized = sanitize_turn_envelope(envelope)
    assert sanitized["safe_error"]["code"] == "schema_error"
    assert "error" not in sanitized
    assert "Traceback" not in repr(sanitized)


def test_run_summary_surfaces_safe_error_for_runtime_case_without_raw_details():
    def failing(question, **kwargs):
        return {"status": "error", "terminal": "error", "answer": {"summary": "safe"},
                "safe_error": {"contract": "safe_error_v1", "stage": "planning",
                               "code": "metric_not_supported", "retryable": False,
                               "remediation": "correct_request_or_supported_schema",
                               "trace_ref": "audit_x"}, "llm_assist": {"enabled": False}}
    result = DynamicConversationRunner(failing, failing, use_llm=False).run_case(_case())
    report = summarize_runs([result])
    assert report["runtime_error_count"] == 1
    assert report["runtime_errors"] == [{"case_id": "fixture", "safe_error": result["transcript"][0]["response"]["safe_error"]}]
    assert "summary" not in repr(report["runtime_errors"])


def test_io_quality_evaluator_scores_declared_outcome_evidence_and_audit_trace():
    case = _case()
    case["expected_safe_outcomes"] = ["ok", "evidence_limited"]
    run = {"case_id": "fixture", "completion_reason": "analysis_complete", "transcript": [{"response": {
        "status": "ok", "audit_id": "audit_1", "evidence_ids": ["ev_1"], "analysis_evidence_ids": []}}]}
    report = evaluate_dynamic_runs([case], [run])
    assert report["contract"] == "dynamic_conversation_quality_report_v1"
    assert report["passed"] is True
    assert report["metrics"]["declared_safe_outcome_pass_rate"] == 1.0
    assert report["metrics"]["ok_output_evidence_reference_rate"] == 1.0
    assert report["metrics"]["audit_trace_coverage"] == 1.0


def test_io_quality_evaluator_rejects_untraced_ok_without_evidence_and_classifies_badcase():
    case = _case()
    case["expected_safe_outcomes"] = ["ok"]
    run = {"case_id": "fixture", "completion_reason": "analysis_complete", "transcript": [{"response": {"status": "ok"}}]}
    report = evaluate_dynamic_runs([case], [run])
    assert report["passed"] is False
    assert report["badcases"][0]["failure_category"] == "unsupported_claim"
    assert "ok_without_evidence_reference" in report["badcases"][0]["errors"]


def test_io_quality_evaluator_prioritizes_runtime_failure_over_unexpected_status():
    case = _case()
    case["expected_safe_outcomes"] = ["ok"]
    run = {"case_id": "fixture", "completion_reason": "runtime_error", "transcript": [{"response": {
        "status": "error", "safe_error": {"contract": "safe_error_v1"}}}]}
    report = evaluate_dynamic_runs([case], [run])
    assert report["badcases"][0]["failure_category"] == "runtime_failure"




def test_provider_failure_is_not_business_turn_and_is_preserved_in_transcript():
    calls = []
    def ask(question, **kwargs):
        calls.append(question)
        return {"status": "ok", "terminal": "ok", "answer": {"summary": "已完成"},
                "answer_contract": {"evidence_ids": ["ev1"]},
                "llm_assist": {"enabled": True, "status": "provider_runtime_error"}}
    runner = DynamicConversationRunner(ask, ask, use_llm=True, max_provider_retries=1)
    result = runner.run_case(_case())
    assert result["business_turn_count"] == 0
    assert result["provider_failure_count"] == 2
    assert result["completion_reason"] == "provider_unresolved"
    assert len(result["transcript"]) == 2
    assert all(not row["valid_business_turn"] for row in result["transcript"])


def test_positive_conversation_generates_followups_from_actual_response_until_evidence_complete():
    calls = []
    def fake(question, **kwargs):
        calls.append(question)
        if len(calls) < 3:
            return {"status": "ok", "terminal": "ok", "answer": {"summary": "intermediate"},
                    "answer_contract": {"evidence_ids": []}, "llm_assist": {"enabled": False}}
        return {"status": "ok", "terminal": "ok", "answer": {"summary": "final"},
                "answer_contract": {"evidence_ids": ["ev-final"]}, "llm_assist": {"enabled": False}}
    result = DynamicConversationRunner(fake, fake, use_llm=False).run_case(_case())
    assert result["business_turn_count"] == 3
    assert result["completion_reason"] == "analysis_complete"
    assert result["transcript"][1]["user_question"] != result["transcript"][0]["user_question"]
    assert result["transcript"][1]["followup_policy"]["reason"] == "drilldown_diagnosis"


def test_negative_safe_response_does_not_continue_into_bypass_dialogue():
    def blocked(question, **kwargs):
        return {"status": "blocked", "terminal": "blocked", "answer": {"summary": "denied"},
                "llm_assist": {"enabled": False}}
    result = DynamicConversationRunner(blocked, blocked, use_llm=False).run_case(_case("negative", "policy_bypass"))
    assert result["business_turn_count"] == 1
    assert result["completion_reason"] == "safe_terminal:blocked"
    assert len(result["transcript"]) == 1


def test_long_dimension_drilldown_inherits_typed_metric_scope():
    facade = AgentFacade(session_id="followup-scope",
                         access_context={"tenant_id": "tenant-a", "user_id": "user-a", "role": "analyst"})
    context = {"task_state": {"metric": "order_count", "task_type": "descriptive",
                                "intent": "metric_query", "time_range": "recent"}}
    resolved = facade._resolve_multiturn_query(
        "请按最相关的渠道、地区或商品维度拆解，并说明贡献最大的项目。", context)
    assert "order_count" in resolved
    assert "metric_query" in resolved


def test_unrelated_long_turn_does_not_inherit_prior_typed_scope():
    facade = AgentFacade(session_id="followup-scope-unrelated",
                         access_context={"tenant_id": "tenant-a", "user_id": "user-a", "role": "analyst"})
    question = "请解释本季度留存率的计算口径和适用人群。"
    resolved = facade._resolve_multiturn_query(question, {"task_state": {"metric": "order_count"}})
    assert resolved == question
