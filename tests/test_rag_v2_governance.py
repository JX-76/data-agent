# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from knowledge_rag import KnowledgeDocumentBuilder
from rag_retriever import RagContextPacker, RagGuardrail
from rag_governance import (ClaimEvidenceAuditor, ClaimScopeBuilder,
                            HistoricalEvidencePolicy, IdempotencyKeyBuilder,
                            PromptContextCompiler, TaskStateDiff, TaskStateLedger)


def _sop_evidence():
    return {
        "citation_id": "R1",
        "chunk_id": "sop:gmv_drop_diagnosis",
        "title": "GMV 下滑诊断 SOP",
        "type": "analysis_sop",
        "knowledge_type": "analysis_sop",
        "source_uri": "semantic/ecommerce_sops.yaml",
        "supporting_extract": "所有归因结论必须由查询结果验证。",
        "metadata": {"authority": "governed_sop", "sop_id": "gmv_drop_diagnosis"},
        "score": 0.8,
    }


def test_governed_sop_dataset_is_ingested():
    docs = KnowledgeDocumentBuilder("semantic").build()
    sop = [doc for doc in docs if doc.id == "sop:gmv_drop_diagnosis"]
    assert len(sop) == 1
    assert sop[0].type == "analysis_sop"
    assert sop[0].metadata["authority"] == "governed_sop"
    assert "所有归因结论必须由查询结果验证" in sop[0].content


def test_context_packer_separates_constraints_from_procedures():
    constraint = {
        "citation_id": "R2",
        "chunk_id": "metric:sandbox_gmv#definition",
        "title": "GMV 定义",
        "type": "metric_method",
        "knowledge_type": "metric_method",
        "supporting_extract": "GMV 不扣退款。",
        "metadata": {"authority": "semantic_config"},
        "score": 0.7,
    }
    packed = RagContextPacker().pack("GMV 下滑原因", [_sop_evidence(), constraint])
    assert packed["blocks"]["analysis_constraints"][0]["chunk_id"] == constraint["chunk_id"]
    assert packed["blocks"]["analysis_procedures"][0]["chunk_id"] == "sop:gmv_drop_diagnosis"
    assert "Data facts, values, rankings" in packed["content"]
    assert packed["usage_policy"]["sop_is_fact"] is False


def test_unknown_query_is_rejected_even_when_nearest_neighbours_exist():
    guardrail = RagGuardrail()
    result = guardrail.assess_retrieval(
        "火星仓库库存规则是什么",
        [_sop_evidence()],
        entities={"metrics": [], "tables": [], "sops": [], "intents": []},
    )
    assert result["decision"] == "no_answer"
    assert result["confidence"] == 0.0
    assert "query_not_grounded_by_retrieved_evidence" in result["reasons"]


def test_metadata_anchor_allows_governed_metric_reference():
    guardrail = RagGuardrail()
    metric = {
        "chunk_id": "metric:sandbox_gmv#definition",
        "title": "GMV 定义",
        "type": "metric_method",
        "knowledge_type": "metric_method",
        "score": 0.01,
        "metadata": {"metric_refs": ["GMV"]},
    }
    result = guardrail.assess_retrieval("GMV 怎么计算", [metric], entities={"metrics": ["GMV"]})
    assert result["decision"] == "ok"
    assert result["confidence"] > 0
    assert result["trace"][0]["support"]["anchored"] is True


def test_answerability_gate_rejects_sensitive_and_out_of_scope_queries_even_with_metric_anchor():
    guardrail = RagGuardrail()
    metric = {
        "chunk_id": "metric:sandbox_gmv#definition",
        "title": "GMV 定义",
        "type": "metric_method",
        "knowledge_type": "metric_method",
        "score": 0.95,
        "metadata": {"metric_refs": ["GMV"]},
    }
    sensitive = guardrail.assess_retrieval("CEO身份证对应的GMV权限口径", [metric], entities={"metrics": ["GMV"]})
    assert sensitive["decision"] == "no_answer"
    assert sensitive["confidence"] == 0.0
    assert "query_requests_sensitive_or_private_data" in sensitive["reasons"]
    assert "query_out_of_governed_corpus_scope" in sensitive["reasons"]

    out_of_scope = guardrail.assess_retrieval("北极区域GMV口径是什么", [metric], entities={"metrics": ["GMV"]})
    assert out_of_scope["decision"] == "no_answer"
    assert "query_out_of_governed_corpus_scope" in out_of_scope["reasons"]


def test_answerability_gate_marks_weak_generic_anchor_partial_not_ok():
    guardrail = RagGuardrail()
    metric = {
        "chunk_id": "metric:sandbox_gmv#definition",
        "title": "GMV 定义",
        "type": "metric_method",
        "knowledge_type": "metric_method",
        "score": 0.10,
        "metadata": {"metric_refs": ["GMV"]},
    }
    result = guardrail.assess_retrieval("GMV 相关的未知业务规则", [metric], entities={"metrics": ["GMV"]})
    assert result["decision"] == "partial_answer"
    assert "only_generic_metric_anchor" in result["reasons"]


def test_rag_only_cannot_support_a_numeric_or_confirmed_conclusion():
    guardrail = RagGuardrail()
    numeric = guardrail.validate_answer_grounding(
        {"answer": "GMV 下降了 20%，已确认由渠道投放导致。", "citations": []},
        rag_evidence=[_sop_evidence()],
    )
    assert numeric["status"] == "blocked"
    assert "data_claim_without_tool_evidence" in numeric["unsupported_claims"]
    assert "confirmed_conclusion_from_rag_only" in numeric["unsupported_claims"]

    supported = guardrail.validate_answer_grounding(
        {"answer": "GMV 下降了 20%。", "citations": []},
        tool_evidence=[{"tool": "sql_query", "result_id": "q1"}],
        rag_evidence=[_sop_evidence()],
    )
    assert supported["status"] == "ok"


def test_prompt_context_keeps_task_state_memory_and_sop_out_of_fact_block():
    compiled = PromptContextCompiler().compile(
        "analyst", "继续分析", task_state={"metric": "gmv", "time_range": "last_7_days"},
        fact_ledger={"verified": True, "ledger_id": "E:q1"},
        rag_context={"blocks": {"analysis_procedures": [_sop_evidence()]}},
        user_preferences=[{"citation_id": "M:format", "supporting_extract": "默认中文表格"}],
    )
    assert "TYPED_TASK_STATE_NOT_FACT" in compiled
    assert "VERIFIED_FACT_LEDGER" in compiled
    assert "SOP_AND_PROCEDURES_NOT_FACTS" in compiled
    assert "USER_PREFERENCES_PRESENTATION_ONLY" in compiled
    assert "只能由当前任务匹配的工具/SQL执行证据支持" in compiled


def test_task_state_diff_rejects_malformed_list_filters_without_crashing():
    diff = TaskStateDiff().diff(
        {"metric": "gmv", "time_range": "last_7_days", "task_type": "trend",
         "filters": [{"field": "tenant_id", "value": "t1"}]},
        {"metric": "gmv", "time_range": "last_7_days", "task_type": "trend",
         "filters": ["2026-08-01", "2026-08-02"]},
    )
    assert "filters_contract_invalid" in diff["changed_fields"]
    assert diff["compatible"] is False
    assert diff["details"]["filters_contract_invalid"]["malformed"] == ["previous", "requested"]


def test_historical_evidence_reexecutes_when_time_range_changes_or_is_stale():
    previous = {
        "status": "ok", "metric": "gmv", "dimensions": ["channel"],
        "time_range": "last_7_days", "task_type": "trend", "results": [{"gmv": 1}],
        "task_id": "old", "completed_at": 10,
    }
    requested = {"metric": "gmv", "dimensions": ["channel"],
                 "time_range": "last_30_days", "task_type": "trend"}
    decision = HistoricalEvidencePolicy().assess(previous, requested, now=20)
    assert decision["decision"] == "reexecute"
    assert "time_range_mismatch" in decision["reasons"]

    stale = HistoricalEvidencePolicy().assess(previous, dict(previous), now=1000)
    assert stale["decision"] == "reexecute"
    assert "previous_result_stale" in stale["reasons"]


def test_claim_scope_forbids_data_claims_without_current_execution_evidence():
    scope = ClaimScopeBuilder().build(
        task_state={"metric": "gmv", "time_range": "last_7_days"},
        rag_evidence=[_sop_evidence()],
        historical_decision={"action": "reuse_context_only"},
    )
    assert scope["can_make_data_claims"] is False
    assert "historical_numeric_reuse" in scope["forbidden_claims"]

    audit = ClaimEvidenceAuditor().audit(
        "GMV下降了20%，主要由投放导致。",
        rag_evidence=[_sop_evidence()], claim_scope=scope,
    )
    assert audit["status"] == "blocked"
    assert "claim_scope_forbids_data_claim" in audit["unsupported_claims"]


def test_idempotency_key_is_stable_and_input_sensitive():
    builder = IdempotencyKeyBuilder()
    one = builder.build(tenant_id="t1", session_id="s1", stage="rag_index",
                        input_value={"doc": "a"}, data_version="v1")
    same = builder.build(tenant_id="t1", session_id="s1", stage="rag_index",
                           input_value={"doc": "a"}, data_version="v1")
    changed = builder.build(tenant_id="t1", session_id="s1", stage="rag_index",
                            input_value={"doc": "b"}, data_version="v1")
    assert one["idempotency_key"] == same["idempotency_key"]
    assert one["idempotency_key"] != changed["idempotency_key"]


def test_failed_or_unverified_tool_payload_cannot_support_numeric_claims():
    auditor = ClaimEvidenceAuditor()
    answer = "GMV 下降了20%，主要由投放导致。"
    failed = auditor.audit(answer, tool_evidence=[{"status": "error", "result_id": "bad"}])
    assert failed["status"] == "blocked"
    assert failed["verified_tool_evidence_count"] == 0
    assert "data_claim_without_current_verified_tool_evidence" in failed["unsupported_claims"]

    unverified = auditor.audit(answer, tool_evidence=[{"authority": "unverified", "dataid": "x"}])
    assert unverified["status"] == "blocked"
    assert unverified["verified_tool_evidence_count"] == 0

    verified = auditor.audit(answer, tool_evidence=[{"authority": "verified_execution", "dataid": "q1"}])
    assert verified["status"] == "ok"
    assert verified["verified_tool_evidence_count"] == 1


def test_verified_ledger_excludes_model_summary_and_claim_gate_abstains():
    result = {"status": "ok", "metric": "gmv", "results": [{"gmv": 100}],
              "task_id": "q1", "summary": "GMV 增长 100% 因为投放"}
    ledger = TaskStateLedger().capture(result, now=1)
    assert ledger["verified"] is True
    assert "summary" not in ledger

    audit = ClaimEvidenceAuditor().audit("GMV 增长 100%，主要由投放导致。")
    assert audit["status"] == "blocked"
    assert audit["safe_answer"]
