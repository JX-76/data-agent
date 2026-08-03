# -*- coding: utf-8 -*-
"""Reranker adapters for Phase A RAG."""
from __future__ import unicode_literals

from full_rag import CrossEncoderReranker, tokenize


class LexicalReranker(object):
    """Deterministic local reranker used when no model reranker is configured."""

    def rerank(self, query, chunks, top_k=8):
        qtokens = set(tokenize(query))
        scored = []
        for chunk in chunks:
            text_tokens = set(tokenize(chunk.title + " " + chunk.content))
            overlap = len(qtokens.intersection(text_tokens))
            scored.append((chunk, float(overlap)))
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:top_k]


class RerankerFactory(object):
    @staticmethod
    def create(provider="lexical", **kwargs):
        provider = (provider or "lexical").lower()
        if provider in ("none", "disabled"):
            return None
        if provider in ("lexical", "local", "mock"):
            return LexicalReranker()
        if provider in ("cross_encoder", "cross-encoder", "bge-reranker"):
            return CrossEncoderReranker(**kwargs)
        raise ValueError("unsupported reranker provider: %s" % provider)
