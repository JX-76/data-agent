# -*- coding: utf-8 -*-
from rag_sandbox import HybridSandboxRetriever, KnowledgeChunk, SandboxKnowledgeBuilder, build_evidence_pack
from sandbox_data_factory import build_sandbox_connection


def test_virtual_database_has_retrievable_schema_and_facts():
    conn = build_sandbox_connection()
    rows = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    chunks = SandboxKnowledgeBuilder().build()
    assert rows == 5
    assert any(item.chunk_id == "table:orders#profile" for item in chunks)
    assert any(item.chunk_id == "metric:sandbox_gmv#formula" for item in chunks)


def test_hybrid_retrieval_returns_formula_and_parent_context():
    results = HybridSandboxRetriever().retrieve("GMV 怎么计算，口径是什么", top_k=8)
    ids = [chunk.chunk_id for chunk, _, _ in results]
    assert "metric:sandbox_gmv#formula" in ids
    assert "metric:sandbox_gmv#definition" in ids
    assert "metric:sandbox_gmv#caveats" in ids


def test_scenario_query_returns_diagnosis_evidence_pack():
    pack = build_evidence_pack("GMV 为什么下滑，同时看 ROI 投产比")
    ids = [item["chunk_id"] for item in pack["evidence"]]
    assert "scenario:gmv_drop#steps" in ids
    assert any("sandbox_roi" in item["parent_id"] for item in pack["evidence"])
    assert pack["citations"]


def test_acl_is_filtered_before_retrieval():
    private = KnowledgeChunk("private#formula", "private", "metric_method", "formula", "私有指标", "仅 admin 可见", {"metric_refs": ["私有指标"], "acl_roles": ["admin"], "tenant_id": "global"})
    results = HybridSandboxRetriever(chunks=[private]).retrieve("私有指标怎么计算", roles=["analyst"])
    assert results == []
