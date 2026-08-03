# -*- coding: utf-8 -*-
"""Citation helpers for grounded RAG answers."""
from __future__ import unicode_literals


class CitationFormatter(object):
    def format_evidence(self, evidence):
        out = []
        for idx, item in enumerate(evidence or [], start=1):
            citation_id = item.get("citation_id") or "R%s" % idx
            out.append({
                "id": citation_id,
                "chunk_id": item.get("chunk_id"),
                "doc_id": item.get("doc_id") or item.get("parent_id"),
                "title": item.get("title"),
                "source_uri": item.get("source_uri") or item.get("metadata", {}).get("source_uri") or item.get("metadata", {}).get("source"),
                "score": item.get("score", 0.0),
                "snippet": item.get("supporting_extract") or item.get("snippet") or "",
            })
        return out

    def validate_answer_citations(self, answer):
        citations = answer.get("citations") or []
        evidence = answer.get("evidence") or []
        evidence_ids = set([e.get("chunk_id") for e in evidence])
        for c in citations:
            if c.get("chunk_id") not in evidence_ids:
                return False
        return True
