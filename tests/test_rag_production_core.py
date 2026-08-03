# -*- coding: utf-8 -*-
from rag_chunker import ParentChildChunker, RecursiveTextChunker, TableAwareChunker
from rag_citation import CitationFormatter
from rag_contracts import RagDocument, RagIndexManifest
from rag_eval import RagQualityEvaluator
from rag_retriever import RagService


def test_recursive_and_parent_child_chunker():
    doc = RagDocument("doc1", "测试文档", "memory://doc1", acl=["role:analyst"])
    chunks = ParentChildChunker(RecursiveTextChunker(chunk_size=10, overlap=2)).chunk_document(doc, "abcdefghijklmnopqrstuvwxyz")
    assert chunks[0].chunk_type == "parent"
    assert len(chunks) > 2
    assert chunks[1].parent_id == "doc1"


def test_table_aware_chunker_keeps_headers():
    doc = RagDocument("table1", "表格", "memory://table1")
    chunks = TableAwareChunker(rows_per_chunk=2).chunk_table(doc, ["日期", "GMV"], [["d1", 1], ["d2", 2], ["d3", 3]])
    assert len(chunks) == 2
    assert "日期 | GMV" in chunks[0].text


def test_manifest_tombstone():
    manifest = RagIndexManifest()
    manifest.mark_deleted("doc1")
    assert manifest.is_deleted("doc1")
    assert "doc1" in manifest.to_dict()["tombstones"]


def test_rag_service_grounded_answer_and_citations():
    service = RagService.local()
    answer = service.answer_grounded("GMV 怎么计算", access_context={"role": "analyst", "tenant_id": "global"})
    assert answer["status"] == "ok"
    assert answer["citations"]
    assert CitationFormatter().validate_answer_citations(answer)


def test_rag_quality_evaluator_thresholds():
    service = RagService.local()
    cases = [
        {"query": "GMV 怎么计算", "expected_chunk_ids": ["metric:sandbox_gmv#definition"], "access_context": {"role": "analyst", "tenant_id": "global"}},
        {"query": "ROI 投产比怎么算", "expected_chunk_ids": ["metric:sandbox_roi#definition"], "access_context": {"role": "analyst", "tenant_id": "global"}},
        {"query": "GMV 口径", "expected_chunk_ids": [], "expect_no_answer": True, "access_context": {"role": "guest", "tenant_id": "global"}},
    ]
    evaluator = RagQualityEvaluator(service)
    metrics = evaluator.evaluate(cases)
    assert metrics["recall@5"] >= 0.8
    assert metrics["citation_accuracy"] >= 0.9
    assert not evaluator.pass_thresholds(metrics)
