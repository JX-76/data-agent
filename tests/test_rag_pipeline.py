# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import unittest

from rag_pipeline import RagPipeline, retrieve_context
from evidence_recall import EvidenceRecall
from schema_recall import SchemaRecall
from query_enhance import QueryEnhancer


class DummySchema(object):
    def __init__(self, name, description="", columns=None):
        self.name = name
        self.description = description
        self.columns = columns or []

    def to_dict(self):
        return {"name": self.name, "description": self.description, "columns": list(self.columns)}


class DummySchemaRecall(object):
    def recall(self, query, top_k=3):
        return type("Result", (), {
            "tables": [DummySchema("orders", "订单表", [{"name": "order_id"}])],
            "confidence": 0.8,
            "query": query,
        })()


class DummyEvidenceRecall(object):
    def recall(self, query, top_k=5):
        evidence = type("Evidence", (), {
            "to_dict": lambda self: {"id": "ev1", "type": "document", "content": "GMV evidence", "source": "kb", "relevance_score": 0.7, "metadata": {}}
        })()
        return type("Result", (), {
            "evidence": [evidence],
            "query": query,
            "total_score": 0.7,
        })()


class RagPipelineTest(unittest.TestCase):
    def test_retrieve_context_merges_sources(self):
        pipeline = RagPipeline(
            schema_recall=DummySchemaRecall(),
            evidence_recall=DummyEvidenceRecall(),
            query_enhancer=QueryEnhancer(),
        )
        result = pipeline.retrieve("GMV 最近怎么样")
        payload = result.to_dict()
        self.assertIn("enhanced_query", payload)
        self.assertTrue(payload["schemas"])
        self.assertTrue(payload["evidence"])
        self.assertTrue(payload["citations"])
        self.assertGreaterEqual(payload["confidence"], 0.0)

    def test_retrieve_context_wrapper(self):
        payload = retrieve_context(
            "GMV 查询",
            schema_recall=DummySchemaRecall(),
            evidence_recall=DummyEvidenceRecall(),
            query_enhancer=QueryEnhancer(),
        )
        self.assertEqual(payload["query"], "GMV 查询")
        self.assertTrue(payload["citations"])


class EvidenceRecallTest(unittest.TestCase):
    def test_keyword_scoring_returns_top_hit(self):
        recall = EvidenceRecall([
            {"id": "1", "type": "document", "content": "gmv 增长", "source": "kb"},
            {"id": "2", "type": "document", "content": "other", "source": "kb"},
        ])
        result = recall.recall("gmv", top_k=1)
        self.assertEqual(len(result.evidence), 1)
        self.assertEqual(result.evidence[0].id, "1")


if __name__ == "__main__":
    unittest.main()
