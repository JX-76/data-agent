# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'src')
if SRC not in sys.path: sys.path.insert(0, SRC)

from rag_control_plane import VersionedKnowledgeStore, HybridRetrievalService


def _store():
    store = VersionedKnowledgeStore()
    store.ingest('doc-gmv', 'gmv metric definition\ngmv uses paid order amount\nrefund is excluded only for net gmv', tenant_id='t1', acl_roles=['analyst'], now=1)
    store.mark_indexed('doc-gmv', now=2)
    store.ingest('doc-roi', 'roi metric definition\nroi uses ad cost and paid gmv', tenant_id='t1', acl_roles=['admin'], now=1)
    store.mark_indexed('doc-roi', now=2)
    return store


def test_document_version_chunk_diff_and_dedup_receipts():
    store = VersionedKnowledgeStore()
    doc, changed, receipt = store.ingest('doc-a', 'line one\nline two', tenant_id='t1', now=10)
    assert doc.to_dict()['contract'] == 'document_version_v1'
    assert receipt['status'] == 'pending' and len(changed) == 2
    same, changed2, receipt2 = store.ingest('doc-a', 'line one\nline two', tenant_id='t1', now=11)
    assert same.version == doc.version and changed2 == [] and receipt2['status'] == 'deduplicated'
    new_doc, changed3, ignored = store.ingest('doc-a', 'line one\nline three', tenant_id='t1', now=12)
    assert new_doc.version != doc.version
    assert len(changed3) == 1


def test_pending_documents_are_not_served_until_index_receipt():
    store = VersionedKnowledgeStore()
    store.ingest('doc-pending', 'fresh policy gmv', tenant_id='t1', now=1)
    svc = HybridRetrievalService(store)
    pending = svc.retrieve('gmv', {'tenant_id': 't1', 'role': 'analyst'}, now=2)
    assert pending['fallback'] == 'evidence_limited' and pending['evidence'] == []
    store.mark_indexed('doc-pending', now=3)
    ready = svc.retrieve('gmv', {'tenant_id': 't1', 'role': 'analyst'}, now=4)
    assert ready['evidence'][0]['document_version']
    assert ready['citations'][0]['indexed_at'] == 3


def test_hybrid_retrieval_rrf_trace_contains_lineage_ranks_versions_and_rerank():
    svc = HybridRetrievalService(_store(), config={'rerank_top_n': 3})
    pack = svc.retrieve('gmv paid amount', {'tenant_id': 't1', 'role': 'analyst'}, now=9)
    assert pack['trace']['contract'] == 'hybrid_retrieval_trace_v1'
    assert pack['trace']['retriever'] == 'sparse_dense_rrf'
    assert pack['trace']['reranker'] == 'deterministic_lexical_fallback'
    first = pack['evidence'][0]
    assert first['document_id'] == 'doc-gmv'
    assert first['document_version'] and first['indexed_at'] == 2
    assert 'sparse' in first['raw_ranks'] and 'dense' in first['raw_ranks']
    assert first['rrf_score'] > 0 and first['selection_reason'] == 'rerank_then_token_budget'


def test_acl_scope_and_revocation_block_serving_immediately():
    store = _store(); svc = HybridRetrievalService(store)
    analyst = svc.retrieve('roi ad cost', {'tenant_id': 't1', 'role': 'analyst'}, now=5)
    assert analyst['fallback'] == 'evidence_limited'
    admin = svc.retrieve('roi ad cost', {'tenant_id': 't1', 'role': 'admin'}, now=5)
    assert admin['evidence'][0]['document_id'] == 'doc-roi'
    store.revoke('doc-roi', now=6)
    revoked = svc.retrieve('roi ad cost', {'tenant_id': 't1', 'role': 'admin'}, now=7)
    assert revoked['evidence'] == [] and revoked['fallback'] == 'evidence_limited'
    assert store.events[-1]['physical_cleanup'] == 'pending'


def test_benchmark_style_metrics_are_computable_without_llm_judge():
    svc = HybridRetrievalService(_store(), config={'rerank_top_n': 2})
    cases = [
        {'query': 'gmv paid order amount', 'expected': 'doc-gmv', 'access_context': {'tenant_id': 't1', 'role': 'analyst'}},
        {'query': 'roi ad cost', 'expected': 'doc-roi', 'access_context': {'tenant_id': 't1', 'role': 'admin'}},
    ]
    hits = 0; reciprocal = []
    for case in cases:
        got = [e['document_id'] for e in svc.retrieve(case['query'], case['access_context'])['evidence']]
        hits += 1 if case['expected'] in got[:2] else 0
        reciprocal.append(1.0 / float(got.index(case['expected']) + 1) if case['expected'] in got else 0.0)
    assert hits == 2
    assert sum(reciprocal) / len(reciprocal) > 0
