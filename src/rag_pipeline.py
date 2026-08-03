# -*- coding: utf-8 -*-
"""Minimal but structured RAG pipeline for the Data Agent.

This module keeps retrieval explicit and testable:
- query understanding / expansion / decomposition
- schema recall
- evidence recall
- semantic knowledge recall
- fusion + context compression + citation-friendly output

It is intentionally not a full vector-db stack. The goal is to preserve the
current retrieval path while making it easy to evolve toward hybrid recall.
"""

from __future__ import unicode_literals

import re

from evidence_recall import EvidenceRecall
from knowledge_rag import KnowledgeRecall
from query_enhance import QueryEnhancer
from schema_recall import SchemaRecall


class RagCitation(object):
    """A citation-like reference produced by the retrieval scaffold."""

    def __init__(self, source_type, source_id, title, snippet, score=0.0, metadata=None):
        self.source_type = source_type
        self.source_id = source_id
        self.title = title
        self.snippet = snippet
        self.score = score
        self.metadata = dict(metadata or {})

    def to_dict(self):
        return {
            "source_type": self.source_type,
            "source_id": self.source_id,
            "title": self.title,
            "snippet": self.snippet,
            "score": self.score,
            "metadata": dict(self.metadata or {}),
        }


class RagRetrievalResult(object):
    """Combined retrieval output for downstream answer generation."""

    def __init__(self, query, enhanced_query, query_bundle=None, ambiguities=None, schemas=None, evidence=None, knowledge=None, citations=None, compressed_context=None, confidence=0.0, notes=None, trace=None):
        self.query = query
        self.enhanced_query = enhanced_query
        self.query_bundle = dict(query_bundle or {})
        self.ambiguities = list(ambiguities or [])
        self.schemas = list(schemas or [])
        self.evidence = list(evidence or [])
        self.knowledge = list(knowledge or [])
        self.citations = list(citations or [])
        self.compressed_context = dict(compressed_context or {})
        self.confidence = confidence
        self.notes = list(notes or [])
        self.trace = list(trace or [])

    def to_dict(self):
        return {
            "query": self.query,
            "enhanced_query": self.enhanced_query,
            "query_bundle": dict(self.query_bundle),
            "ambiguities": list(self.ambiguities),
            "schemas": list(self.schemas),
            "evidence": list(self.evidence),
            "knowledge": list(self.knowledge),
            "citations": [item.to_dict() for item in self.citations],
            "compressed_context": dict(self.compressed_context),
            "confidence": self.confidence,
            "notes": list(self.notes),
            "trace": list(self.trace),
        }


class RagPipeline(object):
    """Structured retrieval scaffold composed from query/schema/evidence modules."""

    def __init__(self, schema_recall=None, evidence_recall=None, query_enhancer=None, knowledge_recall=None):
        self.schema_recall = schema_recall or SchemaRecall()
        self.evidence_recall = evidence_recall or EvidenceRecall()
        self.query_enhancer = query_enhancer or QueryEnhancer()
        self.knowledge_recall = knowledge_recall or KnowledgeRecall.from_semantic_dir()

    def retrieve(self, query, top_k=3, evidence_top_k=5, knowledge_top_k=6, knowledge_types=None):
        enhanced = self.query_enhancer.enhance(query)
        bundle = self._build_query_bundle(query, enhanced)
        trace = []

        schema_query = bundle["normalized_query"]
        schema_result = self.schema_recall.recall(schema_query, top_k=top_k)
        trace.append({"stage": "schema_recall", "query": schema_query, "hit_count": len(schema_result.tables)})

        evidence_queries = bundle["recall_queries"] or [schema_query]
        evidence_hits = []
        knowledge_hits = []
        for sub_query in evidence_queries:
            evidence_result = self.evidence_recall.recall(sub_query, top_k=evidence_top_k)
            knowledge_result = self.knowledge_recall.recall(sub_query, top_k=knowledge_top_k, types=knowledge_types)
            evidence_hits.extend([(sub_query, item) for item in evidence_result.evidence])
            knowledge_hits.extend([(sub_query, doc, score) for doc, score in knowledge_result])
            trace.append({
                "stage": "parallel_recall",
                "query": sub_query,
                "evidence_hits": len(evidence_result.evidence),
                "knowledge_hits": len(knowledge_result),
            })

        schemas = [item.to_dict() for item in schema_result.tables]
        evidence = self._dedupe_evidence([self._evidence_to_dict(item) for _, item in evidence_hits])
        knowledge = self._dedupe_knowledge(knowledge_hits)
        citations = self._build_citations(schemas, evidence, knowledge)
        compressed_context = self._build_compressed_context(query, schemas, evidence, knowledge)
        confidence = self._estimate_confidence(schema_result.confidence, evidence, knowledge, len(enhanced.ambiguities))
        notes = []
        if enhanced.ambiguities:
            notes.append("query_has_ambiguities")
        if len(bundle.get("sub_queries") or []) > 1:
            notes.append("query_decomposed")
        if not schemas and not evidence and not knowledge:
            notes.append("no_retrieval_hits")

        return RagRetrievalResult(
            query=query,
            enhanced_query=bundle["rewrite_query"],
            query_bundle=bundle,
            ambiguities=list(enhanced.ambiguities),
            schemas=schemas,
            evidence=evidence,
            knowledge=knowledge,
            citations=citations,
            compressed_context=compressed_context,
            confidence=confidence,
            notes=notes,
            trace=trace,
        )

    def _build_query_bundle(self, query, enhanced):
        normalized = self._normalize_query(query, enhanced)
        lexical_expanded = self._expand_query_terms(query, enhanced)
        semantic_rewrite = self._rewrite_query(query, enhanced)
        sub_queries = self._decompose_query(query, enhanced, semantic_rewrite)
        recall_queries = []
        for item in [normalized] + lexical_expanded + semantic_rewrite:
            if item and item not in recall_queries:
                recall_queries.append(item)
        for sub in sub_queries:
            goal = sub.get("goal")
            if goal and goal not in recall_queries:
                recall_queries.append(goal)
        return {
            "original": query,
            "normalized_query": normalized,
            "lexical_expanded": lexical_expanded,
            "semantic_rewrite": semantic_rewrite,
            "sub_queries": sub_queries,
            "recall_queries": recall_queries,
            "rewrite_query": semantic_rewrite[0] if semantic_rewrite else normalized,
        }

    def _normalize_query(self, query, enhanced):
        text = enhanced.enhanced if enhanced and enhanced.enhanced else query
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def _expand_query_terms(self, query, enhanced):
        expansions = []
        if enhanced:
            for item in enhanced.expansions or []:
                original = item.get("original")
                expanded = item.get("expanded")
                if original and expanded:
                    expansions.append("%s %s" % (original, expanded))
        for item in self._synonym_expansions(query):
            if item not in expansions:
                expansions.append(item)
        return expansions[:5]

    def _synonym_expansions(self, query):
        synonyms = {
            "GMV": ["销售额", "成交额", "业绩"],
            "AOV": ["客单价", "平均订单金额"],
            "ROI": ["投产比", "投入产出比"],
            "CPA": ["获客成本", "拉新成本"],
            "CTR": ["点击率"],
            "CVR": ["转化率"],
        }
        items = []
        upper = query.upper()
        for key, values in synonyms.items():
            if key in upper or key in query:
                items.append("%s %s" % (key, " ".join(values)))
        return items

    def _rewrite_query(self, query, enhanced):
        # Deterministic lightweight rewrite; LLM rewrite can be added behind the same contract.
        rewrites = []
        base = self._normalize_query(query, enhanced)
        if any(term in base for term in ["下滑", "下降", "减少", "为什么", "原因"]):
            rewrites.append("%s 原因分析 维度拆解 指标诊断" % base)
        if any(term in base for term in ["对比", "比较", "环比", "同比"]):
            rewrites.append("%s 对比分析 时间趋势 维度差异" % base)
        if any(term in base for term in ["怎么", "如何", "方案", "SOP"]):
            rewrites.append("%s 方法 口径 步骤" % base)
        if not rewrites:
            rewrites.append(base)
        return rewrites[:2]

    def _decompose_query(self, query, enhanced, semantic_rewrite):
        text = query
        markers = ["并且", "同时", "顺便", "以及", "和", "再", "还要", "另外"]
        if not any(marker in text for marker in markers):
            return [{"id": "q1", "goal": semantic_rewrite[0] if semantic_rewrite else query, "type": "single"}]
        parts = [p.strip() for p in re.split(r"并且|同时|顺便|以及|另外|再|还要", text) if p.strip()]
        sub_queries = []
        for idx, part in enumerate(parts[:3], start=1):
            sub_queries.append({
                "id": "q%s" % idx,
                "goal": part,
                "type": "sub_query",
            })
        return sub_queries or [{"id": "q1", "goal": query, "type": "single"}]

    def _evidence_to_dict(self, item):
        if isinstance(item, dict):
            return dict(item)
        if hasattr(item, "to_dict"):
            return item.to_dict()
        return {
            "id": getattr(item, "id", ""),
            "type": getattr(item, "type", "evidence"),
            "content": getattr(item, "content", ""),
            "source": getattr(item, "source", ""),
            "relevance_score": getattr(item, "relevance_score", 0.0),
            "metadata": dict(getattr(item, "metadata", {}) or {}),
        }

    def _dedupe_evidence(self, evidence_items):
        seen = set()
        deduped = []
        for item in evidence_items:
            key = (item.get("id"), item.get("content"))
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped

    def _dedupe_knowledge(self, knowledge_hits):
        seen = set()
        deduped = []
        for _, doc, score in knowledge_hits:
            key = doc.id
            if key in seen:
                continue
            seen.add(key)
            payload = doc.to_dict()
            payload["relevance_score"] = float(score or 0.0)
            deduped.append(payload)
        deduped.sort(key=lambda item: item.get("relevance_score", 0.0), reverse=True)
        return deduped

    def _compress_text(self, text, max_len=180):
        text = re.sub(r"\s+", " ", text or "").strip()
        if len(text) <= max_len:
            return text
        return text[: max_len - 1] + "…"

    def _build_compressed_context(self, query, schemas, evidence, knowledge):
        compressed = {
            "schema_notes": [],
            "evidence_notes": [],
            "knowledge_notes": [],
        }
        for schema in schemas[:3]:
            compressed["schema_notes"].append({
                "name": schema.get("name"),
                "description": self._compress_text(schema.get("description", ""), 120),
                "columns": [col.get("name") for col in (schema.get("columns") or [])[:8]],
            })
        for item in evidence[:3]:
            compressed["evidence_notes"].append({
                "id": item.get("id"),
                "title": item.get("title") or item.get("source"),
                "snippet": self._compress_text(item.get("content", ""), 160),
            })
        for item in knowledge[:5]:
            compressed["knowledge_notes"].append({
                "id": item.get("id"),
                "title": item.get("title"),
                "type": item.get("type"),
                "snippet": self._compress_text(item.get("content", ""), 180),
                "score": item.get("relevance_score", 0.0),
            })
        return compressed

    def _build_citations(self, schemas, evidence, knowledge=None):
        citations = []
        for schema in schemas or []:
            citations.append(RagCitation(
                source_type="schema",
                source_id=schema.get("name", ""),
                title=schema.get("name", ""),
                snippet=schema.get("description", "") or "",
                score=1.0,
                metadata={"columns": list(schema.get("columns") or [])},
            ))
        for item in evidence or []:
            citations.append(RagCitation(
                source_type=item.get("type", "evidence"),
                source_id=item.get("id", ""),
                title=item.get("source", "") or item.get("title", ""),
                snippet=item.get("content", "")[:200],
                score=float(item.get("relevance_score") or 0.0),
                metadata=dict(item.get("metadata") or {}),
            ))
        for item in knowledge or []:
            citations.append(RagCitation(
                source_type=item.get("type", item.get("source_type", "knowledge")),
                source_id=item.get("id", ""),
                title=item.get("title", ""),
                snippet=item.get("content", "")[:240],
                score=float(item.get("relevance_score") or 0.0),
                metadata=dict(item.get("metadata") or {}),
            ))
        return citations

    def _estimate_confidence(self, schema_confidence, evidence_items, knowledge_items, ambiguity_count):
        base = float(schema_confidence or 0.0)
        if evidence_items:
            base = max(base, min(1.0, 0.1 * len(evidence_items) + 0.2))
        if knowledge_items:
            base = max(base, min(1.0, 0.08 * len(knowledge_items) + 0.15))
        penalty = min(0.4, 0.1 * max(0, ambiguity_count))
        return max(0.0, min(1.0, base - penalty))


def retrieve_context(query, schema_recall=None, evidence_recall=None, query_enhancer=None, knowledge_recall=None, top_k=3, evidence_top_k=5, knowledge_top_k=6, knowledge_types=None):
    """Convenience wrapper returning a plain dict for downstream callers."""
    pipeline = RagPipeline(schema_recall=schema_recall, evidence_recall=evidence_recall, query_enhancer=query_enhancer, knowledge_recall=knowledge_recall)
    result = pipeline.retrieve(query, top_k=top_k, evidence_top_k=evidence_top_k, knowledge_top_k=knowledge_top_k, knowledge_types=knowledge_types)
    return result.to_dict()


__all__ = ["RagPipeline", "RagRetrievalResult", "RagCitation", "retrieve_context"]
