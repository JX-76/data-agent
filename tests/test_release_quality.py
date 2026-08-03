# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from release_api import ask_release, release_history
from release_quality import evaluate_release_envelope


def test_release_quality_passes_for_ok_envelope():
    env = ask_release("最近7天GMV", session_id="quality_test_ok", use_llm=False)
    quality = env["quality"]
    assert quality["contract"] == "release_v1_quality"
    assert quality["passed"] is True
    assert quality["score"] >= quality["threshold"]
    assert env["answer"]["summary"]


def test_release_quality_blocks_sensitive_query():
    env = ask_release("查看用户密码", session_id="quality_test_block", use_llm=False)
    assert env["status"] == "blocked"
    assert env["quality"]["passed"] is True
    assert env["raw"]["blocked_reason"] == "sensitive_field"
    assert env["answer_contract"]["contract"] == "final_answer_contract_v2"
    assert env["answer_contract"]["status"] == "blocked"
    assert env["answer_contract"]["facts"] == []


def test_release_quality_detects_bad_envelope():
    quality = evaluate_release_envelope({"status": "ok", "answer": {"summary": ""}})
    assert quality["passed"] is False
    assert quality["score"] < quality["threshold"]


def test_release_quality_blocks_missing_answer_contract():
    env = ask_release("最近7天GMV", session_id="quality_missing_answer_contract", use_llm=False)
    broken = dict(env)
    broken.pop("answer_contract", None)
    quality = evaluate_release_envelope(broken)
    assert quality["passed"] is False
    failed = [c["name"] for c in quality["checks"] if not c["passed"]]
    assert "stable_envelope_keys" in failed
    assert "stable_answer_contract_keys" in failed
    assert "answer_contract_status" in failed


def test_release_quality_blocks_malformed_answer_contract():
    env = ask_release("最近7天GMV", session_id="quality_bad_answer_contract", use_llm=False)
    broken = dict(env)
    broken["answer_contract"] = {"contract": "legacy", "status": "ok"}
    quality = evaluate_release_envelope(broken)
    assert quality["passed"] is False
    failed = [c["name"] for c in quality["checks"] if not c["passed"]]
    assert "stable_answer_contract_keys" in failed
    assert "answer_contract_status" in failed


def test_release_quality_blocks_unknown_fact_evidence_id():
    env = ask_release("最近7天GMV", session_id="quality_unknown_fact_evidence", use_llm=False)
    broken = dict(env)
    answer_contract = dict(env["answer_contract"])
    answer_contract["status"] = "ok"
    answer_contract["facts"] = [{"text": "GMV 为 1000 元", "evidence_ids": ["ev_missing"]}]
    answer_contract["evidence_ids"] = ["ev_known"]
    answer_contract["provenance"] = {
        "row_count": 1, "metric": "gmv", "time_range": "recent_7d",
        "query_id": "q_1", "data_version": "v1", "dataid": "orders", "evidence_id": "ev_known",
    }
    answer_contract["trace_id"] = "trace_1"
    answer_contract["task_id"] = "task_1"
    broken["status"] = "ok"
    broken["answer_contract"] = answer_contract
    broken["raw"] = {"evidence_id": "ev_known"}
    broken["provenance"] = {"execution": {"row_count": 1}}
    quality = evaluate_release_envelope(broken)
    assert quality["passed"] is False
    failed = [c["name"] for c in quality["checks"] if not c["passed"]]
    assert "answer_contract_evidence" in failed
    assert "answer_contract:fact_unknown_evidence_ids" in quality["warnings"]


def test_release_quality_blocks_non_ok_verified_facts():
    env = ask_release("查看用户密码", session_id="quality_non_ok_fact", use_llm=False)
    broken = dict(env)
    answer_contract = dict(env["answer_contract"])
    answer_contract["facts"] = [{"text": "已确认结论", "evidence_ids": ["ev_1"]}]
    answer_contract["evidence_ids"] = ["ev_1"]
    broken["answer_contract"] = answer_contract
    quality = evaluate_release_envelope(broken)
    assert quality["passed"] is False
    assert "answer_contract:non_ok_facts" in quality["warnings"]


def test_release_quality_requires_production_lineage_for_ok_facts():
    env = ask_release("最近7天GMV", session_id="quality_missing_prod_lineage", use_llm=False)
    broken = dict(env)
    answer_contract = dict(env["answer_contract"])
    answer_contract["status"] = "ok"
    answer_contract["facts"] = [{"text": "GMV 为 1000 元", "evidence_ids": ["ev_1"]}]
    answer_contract["evidence_ids"] = ["ev_1"]
    answer_contract["trace_id"] = "trace_1"
    answer_contract["task_id"] = "task_1"
    answer_contract["provenance"] = {"row_count": 1, "metric": "gmv", "time_range": "recent_7d", "query_id": "q_1"}
    broken["status"] = "ok"
    broken["answer_contract"] = answer_contract
    broken["raw"] = {"evidence_id": "ev_1", "query_id": "q_1"}
    broken["provenance"] = {"execution": {"row_count": 1}}
    quality = evaluate_release_envelope(broken)
    assert quality["passed"] is False
    assert "answer_contract:missing_fact_provenance" in quality["warnings"]
    assert "data_version" in repr(quality["answer_contract_validation"]["errors"])
    assert "dataid" in repr(quality["answer_contract_validation"]["errors"])
    assert "evidence_id" in repr(quality["answer_contract_validation"]["errors"])


def test_release_history_contains_quality_score():
    ask_release("昨天订单量是多少", session_id="quality_test_history", use_llm=False)
    hist = release_history(limit=1)
    assert hist["items"]
    assert "quality_score" in hist["items"][0]
