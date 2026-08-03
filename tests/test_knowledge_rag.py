# -*- coding: utf-8 -*-

from knowledge_rag import KnowledgeDocumentBuilder, KnowledgeRecall
from rag_pipeline import RagPipeline, retrieve_context


def test_build_knowledge_documents_includes_metric_and_scenario():
    builder = KnowledgeDocumentBuilder(semantic_dir="semantic")
    docs = builder.build()
    doc_types = [doc.type for doc in docs]
    assert "database_schema" in doc_types
    assert "metric_method" in doc_types
    assert "business_scenario" in doc_types


def test_knowledge_recall_filters_by_type():
    recall = KnowledgeRecall.from_semantic_dir("semantic")
    results = recall.recall("GMV 下滑怎么诊断", top_k=5, types=["business_scenario"])
    assert results
    assert all(doc.type == "business_scenario" for doc, _ in results)


def test_retrieve_context_returns_knowledge_section():
    result = retrieve_context("GMV 下滑怎么诊断", knowledge_types=["business_scenario", "metric_method"])
    assert "knowledge" in result
    assert result["knowledge"]
    assert any(item["type"] in ("business_scenario", "metric_method") for item in result["knowledge"])
    assert result["citations"]


def test_rag_query_bundle_expands_rewrites_and_decomposes():
    pipeline = RagPipeline()
    result = pipeline.retrieve("GMV 为什么下滑，同时看 ROI 投产比", knowledge_types=["business_scenario", "metric_method"])
    payload = result.to_dict()
    bundle = payload["query_bundle"]
    assert bundle["lexical_expanded"]
    assert bundle["semantic_rewrite"]
    assert len(bundle["sub_queries"]) >= 2
    assert "query_decomposed" in payload["notes"]
    assert any(item["stage"] == "parallel_recall" for item in payload["trace"])


def test_rag_compressed_context_filters_noise_before_llm():
    result = retrieve_context("GMV 为什么下滑", knowledge_types=["business_scenario", "metric_method"])
    compressed = result["compressed_context"]
    assert compressed["knowledge_notes"]
    assert all(len(item["snippet"]) <= 180 for item in compressed["knowledge_notes"])
