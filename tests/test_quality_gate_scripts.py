# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
SRC = os.path.join(ROOT, "src")
for path in (SCRIPTS, SRC):
    if path not in sys.path:
        sys.path.insert(0, path)

from answer_contract_validator import validate_answer_contract_envelope

import export_badcases
import replay_harness_failure
import run_agent_quality_gate
import run_release_100_gate


def test_replay_registry_supports_benchmark_r20_and_explicit_case_path():
    harness = replay_harness_failure.AgentHarness()
    by_suite = replay_harness_failure._load_cases(harness, "benchmark_r20")
    by_path = replay_harness_failure._load_cases(
        harness, os.path.join(ROOT, "harness", "cases", "benchmark_r20.jsonl"))
    assert len(by_suite) == 33
    assert [case["id"] for case in by_path] == [case["id"] for case in by_suite]


def test_quality_gate_thresholds_are_product_grade():
    assert run_agent_quality_gate.QUALITY_THRESHOLDS == {
        "pass_rate": 0.80,
        "status_accuracy": 0.90,
        "task_type_accuracy": 0.85,
        "trace_contract_validity": 1.0,
    }


def test_quality_gate_blocks_trace_contract_regression():
    assert run_agent_quality_gate.QUALITY_THRESHOLDS["trace_contract_validity"] == 1.0


def test_release_100_gate_thresholds_and_case_mix_are_stable():
    assert run_release_100_gate.RELEASE_100_THRESHOLDS == {
        "case_count": 100,
        "pass_rate": 0.80,
        "status_accuracy": 0.90,
        "trace_contract_validity": 1.0,
        "multiturn_completion_rate": 0.70,
    }
    cases = [
        {"category": "single_metric", "expected": {"status": "ok"}, "release_gate": {"must_have_evidence": True}},
        {"category": "breakdown_ranking", "expected": {"status": "ok"}, "release_gate": {"must_have_evidence": True}},
        {"category": "trend_comparison", "expected": {"status": "ok"}, "release_gate": {"must_have_evidence": True}},
        {"category": "diagnosis", "expected": {"status": "ok"}, "release_gate": {"must_have_evidence": True}},
        {"category": "follow_up", "expected": {"status": "need_clarification"}},
        {"category": "security", "expected": {"status": "blocked"}},
        {"category": "failure_path", "expected": {"status": "no_answer"}},
    ]
    cases = cases + [{"category": "single_metric", "expected": {"status": "ok"}, "release_gate": {"must_have_evidence": True}} for _ in range(93)]
    mix = run_release_100_gate.validate_case_mix(cases)
    assert mix["valid"] is True
    assert mix["status_counts"]["ok"] == 97
    assert mix["evidence_required_cases"] == 97


def test_release_100_gate_rejects_bad_case_mix():
    mix = run_release_100_gate.validate_case_mix([{"category": "single_metric", "expected": {"status": "ok"}}])
    assert mix["valid"] is False
    assert "missing_status=blocked" in mix["errors"]
    assert "missing_evidence_required_cases" in mix["errors"]


def test_export_badcases_usage_without_args_returns_error():
    assert export_badcases.main([]) == 1


def test_release_v1_gate_rejects_fact_with_unknown_evidence_id():
    env = {
        "status": "ok",
        "answer_contract": {
            "facts": [{"text": "GMV 为 1000 元", "evidence_ids": ["ev_missing"]}],
            "evidence_ids": ["ev_known"],
            "provenance": {"row_count": 1, "metric": "gmv", "time_range": "recent_7d"},
        },
        "raw": {"evidence_id": "ev_known"},
        "provenance": {"execution": {"row_count": 1}},
    }
    validation = validate_answer_contract_envelope(env)
    assert validation["passed"] is False
    assert "fact_unknown_evidence_ids" in [err["code"] for err in validation["errors"]]



def test_release_v1_gate_rejects_ok_fact_without_required_provenance():
    env = {
        "status": "ok",
        "answer_contract": {
            "facts": [{"text": "GMV 为 1000 元", "evidence_ids": ["ev_1"]}],
            "evidence_ids": ["ev_1"],
            "provenance": {"row_count": 1, "metric": "gmv"},
        },
        "raw": {"evidence_id": "ev_1"},
        "provenance": {"execution": {"row_count": 1}},
    }
    validation = validate_answer_contract_envelope(env)
    assert validation["passed"] is False
    assert "missing_fact_provenance" in [err["code"] for err in validation["errors"]]
    assert "time_range" in repr(validation["errors"])



def test_release_v1_gate_rejects_row_count_mismatch_for_verified_fact():
    env = {
        "status": "ok",
        "answer_contract": {
            "facts": [{"text": "GMV 为 1000 元", "evidence_ids": ["ev_1"]}],
            "evidence_ids": ["ev_1"],
            "provenance": {"row_count": 2, "metric": "gmv", "time_range": "recent_7d"},
        },
        "raw": {"evidence_id": "ev_1"},
        "provenance": {"execution": {"row_count": 1}},
    }
    validation = validate_answer_contract_envelope(env)
    assert validation["passed"] is False
    assert "row_count_mismatch" in [err["code"] for err in validation["errors"]]



def test_release_v1_gate_accepts_consistent_verified_fact_evidence():
    env = {
        "status": "ok",
        "answer_contract": {
            "contract": "final_answer_contract_v2",
            "status": "ok",
            "answer_type": "analysis",
            "answer": "GMV 为 1000 元",
            "facts": [{"text": "GMV 为 1000 元", "evidence_ids": ["ev_1"]}],
            "hypotheses": [],
            "citations": [],
            "limitations": [],
            "next_actions": [],
            "trace_id": "trace_1",
            "task_id": "task_1",
            "evidence_ids": ["ev_1"],
            "provenance": {"row_count": 1, "metric": "gmv", "time_range": "recent_7d", "query_id": "q_1", "data_version": "v1", "dataid": "orders", "evidence_id": "ev_1"},
        },
        "raw": {"evidence_id": "ev_1", "query_id": "q_1", "data_version": "v1", "dataid": "orders"},
        "provenance": {"execution": {"row_count": 1}},
    }
    validation = validate_answer_contract_envelope(env, require_production_fields=True)
    assert validation["passed"] is True



def test_release_v1_gate_rejects_missing_production_fields_when_required():
    env = {
        "status": "ok",
        "answer_contract": {
            "contract": "final_answer_contract_v2",
            "status": "ok",
            "answer_type": "analysis",
            "answer": "GMV 为 1000 元",
            "facts": [{"text": "GMV 为 1000 元", "evidence_ids": ["ev_1"]}],
            "hypotheses": [],
            "citations": [],
            "limitations": [],
            "next_actions": [],
            "trace_id": "trace_1",
            "task_id": "task_1",
            "evidence_ids": ["ev_1"],
            "provenance": {"row_count": 1, "metric": "gmv", "time_range": "recent_7d", "query_id": "q_1"},
        },
        "raw": {"evidence_id": "ev_1", "query_id": "q_1"},
        "provenance": {"execution": {"row_count": 1}},
    }
    validation = validate_answer_contract_envelope(env, require_production_fields=True)
    assert validation["passed"] is False
    codes = [err["code"] for err in validation["errors"]]
    assert "missing_fact_provenance" in codes
    assert "data_version" in repr(validation["errors"])
    assert "dataid" in repr(validation["errors"])
    assert "evidence_id" in repr(validation["errors"])



def test_quality_baseline_diff_and_lowest_category_output_are_stable():
    report = {
        "pass_rate": 0.75,
        "status_accuracy": 0.85,
        "task_type_accuracy": 0.70,
        "category_metrics": {
            "comparison": {"total": 2, "failed": 1, "pass_rate": 0.5, "failure_breakdown": {"planning_error": 1}},
            "anomaly": {"total": 1, "failed": 1, "pass_rate": 0.0, "failure_breakdown": {"routing_error": 1}},
        },
    }
    baseline = {
        "agent_quality_gate": {
            "pass_rate": 0.70,
            "status_accuracy": 0.80,
            "task_type_accuracy": 0.75,
        }
    }
    diff = run_agent_quality_gate._baseline_diff(report, baseline)
    assert diff["pass_rate"] == {"baseline": 0.70, "current": 0.75, "delta": 0.05}
    assert diff["task_type_accuracy"]["delta"] == -0.05
    assert [row["category"] for row in run_agent_quality_gate._lowest_categories(report)] == ["anomaly", "comparison"]
