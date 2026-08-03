# -*- coding: utf-8 -*-
"""EvidenceRecall: Evidence-based recall for query answering.

Recalls relevant evidence (past queries, documents, knowledge base)
to support the current query.
"""

from __future__ import unicode_literals

import structlog

logger = structlog.get_logger("evidence_recall")


class Evidence(object):
    """A piece of evidence."""

    def __init__(self, id, type, content, source, relevance_score=0.0, metadata=None):
        self.id = id
        self.type = type
        self.content = content
        self.source = source
        self.relevance_score = relevance_score
        self.metadata = dict(metadata or {})

    def to_dict(self):
        return {
            "id": self.id,
            "type": self.type,
            "content": self.content,
            "source": self.source,
            "relevance_score": self.relevance_score,
            "metadata": dict(self.metadata or {}),
        }


class EvidenceRecallResult(object):
    """Result of evidence recall."""

    def __init__(self, evidence, query, total_score=0.0):
        self.evidence = list(evidence or [])
        self.query = query
        self.total_score = total_score


class EvidenceRecall(object):
    """Recalls relevant evidence for a query."""

    def __init__(self, evidence_store=None):
        self.evidence_store = list(evidence_store or [])
        self._embedding_cache = {}

    def recall(self, query, top_k=5):
        scored = []
        for item in self.evidence_store:
            score = self._score_relevance(query, item)
            evidence = Evidence(
                id=item.get("id", ""),
                type=item.get("type", "unknown"),
                content=item.get("content", ""),
                source=item.get("source", ""),
                relevance_score=score,
                metadata=item.get("metadata", {}),
            )
            scored.append((evidence, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        top_evidence = [e for e, _ in scored[:top_k]]
        total_score = sum(s for _, s in scored[:top_k])

        return EvidenceRecallResult(evidence=top_evidence, query=query, total_score=total_score)

    def add_evidence(self, evidence):
        self.evidence_store.append(evidence)
        logger.info("evidence_added", evidence_id=evidence.get("id"))

    def _score_relevance(self, query, evidence):
        query_lower = query.lower()
        content = evidence.get("content", "").lower()
        if query_lower in content:
            return 1.0
        query_words = set(query_lower.split())
        content_words = set(content.split())
        if not query_words:
            return 0.0
        overlap = query_words & content_words
        score = len(overlap) / len(query_words)
        title = evidence.get("title", "").lower()
        if any(word in title for word in query_words):
            score += 0.2
        return min(score, 1.0)


def recall_evidence(query, evidence_store=None, top_k=5):
    recall = EvidenceRecall(evidence_store)
    return recall.recall(query, top_k)
