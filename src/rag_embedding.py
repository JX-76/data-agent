# -*- coding: utf-8 -*-
"""Embedding provider adapters for RAG.

Production must use a real semantic embedding model.  The deterministic provider
is kept only for explicit tests/offline development and is never selected by
accident.
"""
from __future__ import unicode_literals

import os

from full_rag import DeterministicEmbeddingProvider, SentenceTransformerEmbeddingProvider


PRODUCTION_EMBEDDING_PROVIDERS = ("sentence_transformer", "sentence-transformer", "bge", "m3e")
TEST_EMBEDDING_PROVIDERS = ("deterministic", "local", "mock", "test")


class EmbeddingProviderFactory(object):
    @staticmethod
    def create(provider=None, **kwargs):
        provider = (provider or os.environ.get("DATA_AGENT_RAG_EMBEDDING_PROVIDER") or "bge").lower()
        if provider in TEST_EMBEDDING_PROVIDERS:
            if not EmbeddingProviderFactory._deterministic_allowed():
                raise RuntimeError(
                    "deterministic RAG embedding is disabled outside tests; "
                    "set DATA_AGENT_RAG_ALLOW_DETERMINISTIC=1 only for local tests"
                )
            return DeterministicEmbeddingProvider(**kwargs)
        if provider in PRODUCTION_EMBEDDING_PROVIDERS:
            if provider == "m3e":
                kwargs.setdefault("model_name", "moka-ai/m3e-base")
            else:
                kwargs.setdefault("model_name", os.environ.get("DATA_AGENT_EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5"))
            return SentenceTransformerEmbeddingProvider(**kwargs)
        raise ValueError("unsupported embedding provider: %s" % provider)

    @staticmethod
    def create_test(**kwargs):
        return DeterministicEmbeddingProvider(**kwargs)

    @staticmethod
    def _deterministic_allowed():
        value = os.environ.get("DATA_AGENT_RAG_ALLOW_DETERMINISTIC", "").lower()
        if value in ("1", "true", "yes"):
            return True
        profile = os.environ.get("DATA_AGENT_PROFILE", "dev").lower()
        return profile in ("test", "ci")
