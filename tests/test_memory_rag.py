# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from memory_rag import MemoryRagStore, NAMESPACE_CONVERSATION, NAMESPACE_USER_PREFERENCE


def test_explicit_preference_is_saved_in_isolated_namespace():
    store = MemoryRagStore()
    records = store.ingest_message('u1', 's1', '以后默认用中文表格展示 GMV', tenant_id='t1')
    assert len(records) == 1
    hits = store.retrieve('GMV 怎么样', user_id='u1', tenant_id='t1')
    assert len(hits) == 1
    assert hits[0]['knowledge_type'] == NAMESPACE_USER_PREFERENCE
    assert hits[0]['metadata']['memory_is_fact'] is False
    assert '中文表格' in hits[0]['supporting_extract']


def test_conversation_memory_persists_structured_task_state_not_answer_claims():
    store = MemoryRagStore()
    record = store.remember_conversation(
        'u1', 's1', 'GMV 下降原因',
        {'status': 'ok', 'metric': 'gmv', 'dimensions': ['channel'],
         'time_range': 'last_7_days', 'summary': 'GMV 下降 20% 因为投放'},
        tenant_id='t1',
    )
    assert record is not None
    hits = store.retrieve('继续按渠道分析 GMV', user_id='u1', tenant_id='t1')
    assert hits[0]['knowledge_type'] == NAMESPACE_CONVERSATION
    assert 'GMV 下降 20%' not in hits[0]['supporting_extract']
    assert hits[0]['metadata']['memory_is_fact'] is False


def test_memory_has_user_tenant_isolation_and_opt_out_erasure():
    store = MemoryRagStore()
    store.write_preference('u1', 's1', 'format', '中文报告', tenant_id='t1')
    store.write_preference('u2', 's2', 'format', 'English report', tenant_id='t1')
    assert len(store.retrieve('报告', user_id='u1', tenant_id='t1')) == 1
    assert store.retrieve('报告', user_id='u1', tenant_id='t2') == []
    store.set_enabled('u1', tenant_id='t1', enabled=False)
    assert store.retrieve('报告', user_id='u1', tenant_id='t1') == []
    assert store.ingest_message('u1', 's3', '以后用 markdown', tenant_id='t1') == []
