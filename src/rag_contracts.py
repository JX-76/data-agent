# -*- coding: utf-8 -*-
"""RAG production contracts.

These contracts intentionally stay dependency-light so they can be reused by
retrievers, evaluators, API adapters and harness scripts.
"""
from __future__ import unicode_literals

import time


class RagDocument(object):
    def __init__(self, doc_id, title, source_uri, content_type="text", tenant_id="global", acl=None, metadata=None, updated_at=None, checksum=""):
        self.doc_id = doc_id
        self.title = title or ""
        self.source_uri = source_uri or ""
        self.content_type = content_type or "text"
        self.tenant_id = tenant_id or "global"
        self.acl = list(acl or ["role:analyst", "role:admin"])
        self.metadata = dict(metadata or {})
        self.updated_at = updated_at or int(time.time())
        self.checksum = checksum or ""

    def to_dict(self):
        return {
            "doc_id": self.doc_id,
            "title": self.title,
            "source_uri": self.source_uri,
            "content_type": self.content_type,
            "tenant_id": self.tenant_id,
            "acl": list(self.acl),
            "metadata": dict(self.metadata),
            "updated_at": self.updated_at,
            "checksum": self.checksum,
        }


class RagChunkRecord(object):
    def __init__(self, chunk_id, doc_id, text, parent_id=None, chunk_type="text", title="", source_uri="", start=0, end=0, metadata=None):
        self.chunk_id = chunk_id
        self.doc_id = doc_id
        self.parent_id = parent_id or chunk_id
        self.text = text or ""
        self.chunk_type = chunk_type or "text"
        self.title = title or ""
        self.source_uri = source_uri or ""
        self.start = int(start or 0)
        self.end = int(end or len(self.text))
        self.metadata = dict(metadata or {})

    def to_dict(self):
        return {
            "chunk_id": self.chunk_id,
            "doc_id": self.doc_id,
            "parent_id": self.parent_id,
            "text": self.text,
            "chunk_type": self.chunk_type,
            "title": self.title,
            "source_uri": self.source_uri,
            "start": self.start,
            "end": self.end,
            "metadata": dict(self.metadata),
        }


class RagRetrievalHit(object):
    def __init__(self, chunk_id, doc_id, score, snippet, title="", source_uri="", parent_id=None, dense_score=0.0, sparse_score=0.0, rrf_score=0.0, metadata=None):
        self.chunk_id = chunk_id
        self.doc_id = doc_id
        self.parent_id = parent_id or chunk_id
        self.score = float(score or 0.0)
        self.dense_score = float(dense_score or 0.0)
        self.sparse_score = float(sparse_score or 0.0)
        self.rrf_score = float(rrf_score or 0.0)
        self.snippet = snippet or ""
        self.title = title or ""
        self.source_uri = source_uri or ""
        self.metadata = dict(metadata or {})

    def to_dict(self):
        return {
            "chunk_id": self.chunk_id,
            "doc_id": self.doc_id,
            "parent_id": self.parent_id,
            "score": self.score,
            "dense_score": self.dense_score,
            "sparse_score": self.sparse_score,
            "rrf_score": self.rrf_score,
            "snippet": self.snippet,
            "title": self.title,
            "source_uri": self.source_uri,
            "metadata": dict(self.metadata),
        }


class RagIndexManifest(object):
    def __init__(self, index_version="v1", active=True, tombstones=None, document_checksums=None, created_at=None):
        self.index_version = index_version or "v1"
        self.active = bool(active)
        self.tombstones = set(tombstones or [])
        self.document_checksums = dict(document_checksums or {})
        self.created_at = created_at or int(time.time())

    def mark_deleted(self, doc_or_chunk_id):
        self.tombstones.add(doc_or_chunk_id)

    def is_deleted(self, doc_or_chunk_id):
        return doc_or_chunk_id in self.tombstones

    def to_dict(self):
        return {
            "index_version": self.index_version,
            "active": self.active,
            "tombstones": sorted(self.tombstones),
            "document_checksums": dict(self.document_checksums),
            "created_at": self.created_at,
        }
