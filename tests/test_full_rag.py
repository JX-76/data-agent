# -*- coding: utf-8 -*-
from full_rag import DeterministicEmbeddingProvider, FullRagRetriever, KnowledgeIngestor, RagEvaluator, rrf_fuse


def test_ingestor_builds_chunks():
    chunks = KnowledgeIngestor(include_sandbox=True).build_chunks()
    assert any(chunk.id == "table:fct_orders" for chunk in chunks)
    assert any(chunk.id.startswith("metric:sandbox_gmv") for chunk in chunks)


def test_rrf_fuse_combines_sparse_and_dense():
    fused = rrf_fuse({"bm25": [(0, 1.0), (2, 0.8)], "dense_hnsw": [(2, 0.9), (1, 0.7)]})
    assert fused[0][0] in {2, 0}
    assert any(item[0] == 2 for item in fused)


def test_retrieve_returns_evidence_and_citations():
    retriever = FullRagRetriever(embedding_provider=DeterministicEmbeddingProvider())
    pack = retriever.retrieve("GMV 为什么下滑，同时看 ROI 投产比", top_k=6, candidate_k=12, access_context={"role": "analyst", "tenant_id": "global"})
    ids = [item["chunk_id"] for item in pack["evidence"]]
    assert any(cid.startswith("scenario:gmv_diagnosis") for cid in ids)
    assert any(cid.startswith("metric:sandbox_gmv") for cid in ids)
    assert any(cid.startswith("metric:sandbox_roi") for cid in ids)
    assert pack["citations"]


def test_acl_filter_blocks_non_matching_role():
    retriever = FullRagRetriever(embedding_provider=DeterministicEmbeddingProvider())
    pack = retriever.retrieve("GMV 口径", access_context={"role": "guest", "tenant_id": "global"})
    assert pack["evidence"] == []


def test_evaluator_returns_metrics():
    retriever = FullRagRetriever(embedding_provider=DeterministicEmbeddingProvider())
    evaluator = RagEvaluator(retriever)
    metrics = evaluator.evaluate([
        {"query": "GMV 怎么计算", "expected_chunk_ids": ["metric:sandbox_gmv"]},
        {"query": "ROI 怎么算", "expected_chunk_ids": ["metric:sandbox_roi"]},
    ])
    assert "recall@1" in metrics
    assert "mrr@5" in metrics
