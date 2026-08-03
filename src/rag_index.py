# -*- coding: utf-8 -*-
"""Index abstractions for Phase A RAG.

The production path can later swap MemoryVectorIndex with Qdrant/Milvus. The
local implementation is deterministic and fully testable.
"""
from __future__ import unicode_literals

from full_rag import BM25Retriever, FaissVectorRetriever, RagChunk
from rag_contracts import RagIndexManifest


class ChunkAdapter(object):
    @staticmethod
    def to_full_rag_chunk(record):
        metadata = dict(getattr(record, "metadata", {}) or {})
        metadata.setdefault("tenant_id", metadata.get("tenant_id", "global"))
        if "acl_roles" not in metadata:
            acl = metadata.get("acl") or ["role:analyst", "role:admin"]
            roles = []
            for item in acl:
                text = str(item)
                roles.append(text.split(":", 1)[1] if text.startswith("role:") else text)
            metadata["acl_roles"] = roles or ["analyst", "admin"]
        return RagChunk(
            id=record.chunk_id,
            parent_id=record.parent_id,
            title=record.title,
            content=record.text,
            source=record.source_uri,
            type=record.chunk_type,
            section_type=record.chunk_type,
            metadata=metadata,
        )


class HybridLocalIndex(object):
    def __init__(self, chunk_records, embedding_provider, manifest=None):
        self.records = list(chunk_records or [])
        self.chunks = [ChunkAdapter.to_full_rag_chunk(r) for r in self.records]
        self.embedding_provider = embedding_provider
        self.manifest = manifest or RagIndexManifest()
        self.bm25 = BM25Retriever(self.chunks) if self.chunks else None
        self.vector = FaissVectorRetriever(self.chunks, embedding_provider) if self.chunks else None

    def search_sparse(self, query, top_k=20, allowed_indices=None):
        if not self.bm25:
            return []
        allowed = set(allowed_indices) if allowed_indices is not None else None
        return [(i, s) for i, s in self.bm25.search(query, top_k) if allowed is None or i in allowed]

    def search_dense(self, query, top_k=20, allowed_indices=None):
        if not self.vector:
            return []
        allowed = set(allowed_indices) if allowed_indices is not None else None
        return [(i, s) for i, s in self.vector.search(query, top_k) if allowed is None or i in allowed]

    def allowed_indices(self, access_context=None):
        ctx = access_context or {}
        role = ctx.get("role", "analyst")
        tenant = ctx.get("tenant_id", "global")
        allowed = set()
        for idx, chunk in enumerate(self.chunks):
            if self.manifest.is_deleted(chunk.id) or self.manifest.is_deleted(chunk.parent_id):
                continue
            roles = set(chunk.metadata.get("acl_roles") or ["analyst", "admin"])
            ctenant = chunk.metadata.get("tenant_id", "global")
            if role in roles and ctenant in ("global", tenant):
                allowed.add(idx)
        return allowed

    def mark_deleted(self, doc_or_chunk_id):
        self.manifest.mark_deleted(doc_or_chunk_id)
