# -*- coding: utf-8 -*-
"""P2 versioned hybrid RAG control plane.

Offline/in-memory reference implementation. It models document/chunk/index state,
ACL revocation and deterministic sparse+dense RRF retrieval without claiming a
production vector database or model reranker.
"""
from __future__ import unicode_literals
import hashlib
import time

try:
    text_type = unicode
except NameError:
    text_type = str

DOCUMENT_VERSION_CONTRACT = 'document_version_v1'
CHUNK_VERSION_CONTRACT = 'chunk_version_v1'
EMBEDDING_JOB_CONTRACT = 'embedding_job_v1'
INDEX_RECEIPT_CONTRACT = 'index_receipt_v1'
RETRIEVAL_TRACE_CONTRACT = 'hybrid_retrieval_trace_v1'


def _now(value=None): return float(time.time() if value is None else value)
def _hash(value): return hashlib.sha256((value or '').encode('utf-8')).hexdigest()
def _tokens(value): return set([item.lower() for item in str(value or '').replace('_', ' ').split() if item])
def _copy(value): return dict(value or {})


class DocumentVersion(object):
    def __init__(self, document_id, content, version=None, tenant_id='global', acl_roles=None,
                 active=True, effective_at=None, indexed_at=None, status='pending', metadata=None):
        self.document_id = document_id; self.content = content or ''; self.content_hash = _hash(self.content)
        self.version = version or self.content_hash[:12]; self.tenant_id = tenant_id or 'global'
        self.acl_roles = list(acl_roles or ['analyst']); self.active = bool(active)
        self.effective_at = _now(effective_at); self.indexed_at = indexed_at; self.status = status
        self.metadata = _copy(metadata)
    def to_dict(self):
        return {'contract': DOCUMENT_VERSION_CONTRACT, 'document_id': self.document_id, 'version': self.version,
                'content_hash': self.content_hash, 'tenant_id': self.tenant_id, 'acl_roles': list(self.acl_roles),
                'active': self.active, 'effective_at': self.effective_at, 'indexed_at': self.indexed_at,
                'status': self.status, 'metadata': _copy(self.metadata)}


class ChunkVersion(object):
    def __init__(self, chunk_id, document_id, document_version, content, ordinal, active=True):
        self.chunk_id = chunk_id; self.document_id = document_id; self.document_version = document_version
        self.content = content; self.content_hash = _hash(content); self.ordinal = ordinal; self.active = bool(active)
    def to_dict(self):
        return {'contract': CHUNK_VERSION_CONTRACT, 'chunk_id': self.chunk_id, 'document_id': self.document_id,
                'document_version': self.document_version, 'content_hash': self.content_hash, 'ordinal': self.ordinal,
                'active': self.active}


class VersionedKnowledgeStore(object):
    """Metadata serving filter is authoritative; physical deletion is asynchronous."""
    def __init__(self):
        self.documents = {}; self.chunks = {}; self.aliases = {'active': set()}; self.events = []

    def ingest(self, document_id, content, tenant_id='global', acl_roles=None, metadata=None, now=None):
        now = _now(now); doc = DocumentVersion(document_id, content, tenant_id=tenant_id, acl_roles=acl_roles, metadata=metadata, effective_at=now)
        old = self.documents.get(document_id)
        if old and old.content_hash == doc.content_hash and old.active:
            return old, [], {'contract': INDEX_RECEIPT_CONTRACT, 'status': 'deduplicated', 'document_id': document_id, 'version': old.version}
        if old: old.active = False
        parts = [line.strip() for line in doc.content.splitlines() if line.strip()] or [doc.content]
        previous = dict((chunk.content_hash, chunk) for chunk in self.chunks.get(document_id, []) if chunk.active)
        changed = []
        for ordinal, part in enumerate(parts):
            content_hash = _hash(part); existing = previous.get(content_hash)
            if existing:
                chunk = ChunkVersion(existing.chunk_id, document_id, doc.version, part, ordinal)
            else:
                chunk = ChunkVersion('%s:%s:%s' % (document_id, ordinal, content_hash[:8]), document_id, doc.version, part, ordinal)
                changed.append(chunk)
            self.chunks.setdefault(document_id, []).append(chunk)
        self.documents[document_id] = doc; self.aliases.setdefault('active', set()).add(document_id)
        self.events.append({'event': 'document_ingested', 'document_id': document_id, 'version': doc.version, 'changed_chunks': len(changed), 'at': now})
        return doc, changed, {'contract': INDEX_RECEIPT_CONTRACT, 'status': 'pending', 'document_id': document_id, 'version': doc.version, 'changed_chunks': len(changed)}

    def mark_indexed(self, document_id, now=None):
        doc = self.documents[document_id]; doc.status = 'indexed'; doc.indexed_at = _now(now)
        self.events.append({'event': 'index_completed', 'document_id': document_id, 'version': doc.version, 'at': doc.indexed_at})
        return {'contract': INDEX_RECEIPT_CONTRACT, 'status': 'indexed', 'document_id': document_id, 'version': doc.version, 'indexed_at': doc.indexed_at}

    def revoke(self, document_id, now=None):
        doc = self.documents[document_id]; doc.active = False; self.aliases.setdefault('active', set()).discard(document_id)
        for chunk in self.chunks.get(document_id, []): chunk.active = False
        self.events.append({'event': 'access_revoked', 'document_id': document_id, 'at': _now(now), 'physical_cleanup': 'pending'})

    def serving_chunks(self, access_context=None, require_indexed=True):
        ctx = access_context or {}; role = ctx.get('role', 'analyst'); tenant = ctx.get('tenant_id', 'global'); out = []
        for doc_id in self.aliases.get('active', set()):
            doc = self.documents[doc_id]
            if not doc.active or (require_indexed and doc.status != 'indexed'): continue
            if doc.tenant_id not in ('global', tenant) or role not in doc.acl_roles: continue
            out.extend([chunk for chunk in self.chunks.get(doc_id, []) if chunk.active and chunk.document_version == doc.version])
        return out


class DeterministicFallbackReranker(object):
    """Explicit lexical fallback; not a real model reranker."""
    name = 'deterministic_lexical_fallback'
    def rerank(self, query, candidates, top_n):
        qt = _tokens(query)
        ranked = sorted(candidates, key=lambda item: (len(qt & _tokens(item['content'])), item['rrf_score']), reverse=True)
        for item in ranked: item['rerank_score'] = float(len(qt & _tokens(item['content'])))
        return ranked[:top_n]


class HybridRetrievalService(object):
    def __init__(self, store, config=None, reranker=None):
        self.store = store; self.config = dict({'sparse_top_k': 20, 'dense_top_k': 20, 'rrf_k': 60, 'weights': {'sparse': 1.0, 'dense': 1.0}, 'rerank_top_n': 8, 'token_budget': 1200}.items())
        self.config.update(config or {}); self.reranker = reranker or DeterministicFallbackReranker()

    def retrieve(self, query, access_context=None, now=None):
        now = _now(now); chunks = self.store.serving_chunks(access_context); qt = _tokens(query)
        sparse = sorted([(c, len(qt & _tokens(c.content))) for c in chunks], key=lambda x: x[1], reverse=True)
        dense = sorted([(c, len(qt & _tokens(c.content + ' ' + c.document_id)) / float(len(qt) or 1)) for c in chunks], key=lambda x: x[1], reverse=True)
        sparse = [item for item in sparse if item[1] > 0][:self.config['sparse_top_k']]
        dense = [item for item in dense if item[1] > 0][:self.config['dense_top_k']]
        ranks = {}; k = float(self.config['rrf_k']); weights = self.config['weights']
        for channel, values in [('sparse', sparse), ('dense', dense)]:
            for rank, (chunk, score) in enumerate(values, 1):
                item = ranks.setdefault(chunk.chunk_id, {'chunk': chunk, 'raw_ranks': {}, 'raw_scores': {}, 'rrf_score': 0.0, 'channels': []})
                item['raw_ranks'][channel] = rank; item['raw_scores'][channel] = score; item['rrf_score'] += float(weights.get(channel, 1.0)) / (k + rank); item['channels'].append(channel)
        candidates = []
        for item in sorted(ranks.values(), key=lambda x: x['rrf_score'], reverse=True):
            doc = self.store.documents[item['chunk'].document_id]
            candidates.append({'chunk_id': item['chunk'].chunk_id, 'document_id': doc.document_id, 'document_version': doc.version, 'indexed_at': doc.indexed_at, 'content': item['chunk'].content, 'raw_ranks': item['raw_ranks'], 'raw_scores': item['raw_scores'], 'rrf_score': item['rrf_score'], 'channels': item['channels'], 'selection_reason': 'rrf_fusion'})
        selected = self.reranker.rerank(query, candidates, self.config['rerank_top_n'])
        used = 0; evidence = []
        for item in selected:
            tokens = len(_tokens(item['content']))
            if used + tokens > self.config['token_budget']: continue
            used += tokens; item['selection_reason'] = 'rerank_then_token_budget'; evidence.append(item)
        trace = {'contract': RETRIEVAL_TRACE_CONTRACT, 'query': query, 'retrieved_at': now, 'retriever': 'sparse_dense_rrf', 'reranker': self.reranker.name, 'candidate_count': len(candidates), 'selected_count': len(evidence), 'acl_scope': _copy(access_context), 'freshness': {'indexed_only': True}, 'evidence': evidence}
        return {'query': query, 'evidence': evidence, 'citations': [{'id': 'R%s' % (i + 1), 'chunk_id': x['chunk_id'], 'document_version': x['document_version'], 'indexed_at': x['indexed_at']} for i, x in enumerate(evidence)], 'trace': trace, 'fallback': 'evidence_limited' if not evidence else None}


__all__ = ['DocumentVersion', 'ChunkVersion', 'VersionedKnowledgeStore', 'HybridRetrievalService', 'DeterministicFallbackReranker', 'DOCUMENT_VERSION_CONTRACT', 'CHUNK_VERSION_CONTRACT', 'EMBEDDING_JOB_CONTRACT', 'INDEX_RECEIPT_CONTRACT', 'RETRIEVAL_TRACE_CONTRACT']
