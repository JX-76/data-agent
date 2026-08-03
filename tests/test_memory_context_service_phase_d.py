# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from memory_context_service import (
    ManagedMemoryStore, MemoryContextAssembler, MemoryContextService,
    MemoryRecord, TopicRouter, ROUTE_FOLLOW_UP, ROUTE_NEW_TOPIC,
    MEMORY_KIND_LONG_TERM,
)
from memory_contracts import EvidenceCard, AUTHORITY_VERIFIED
from memory_eval import MemoryQualityEvaluator


def test_memory_record_ttl_and_erasure():
    now = [1000.0]
    store = ManagedMemoryStore(now=lambda: now[0])
    store.remember('u1', 's1', MEMORY_KIND_LONG_TERM, 'pref', '中文', ttl_seconds=10, tenant_id='global')
    assert len(store.recall(user_id='u1', tenant_id='global')) == 1
    now[0] = 1011.0
    assert store.recall(user_id='u1', tenant_id='global') == []
    store.remember('u1', 's1', MEMORY_KIND_LONG_TERM, 'pref', 'x')
    assert store.forget_user('u1') == 1


def test_memory_isolation_by_user_and_tenant():
    store = ManagedMemoryStore()
    store.remember('u1', 's1', MEMORY_KIND_LONG_TERM, 'pref', 'visible', tenant_id='t1')
    store.remember('u2', 's2', MEMORY_KIND_LONG_TERM, 'pref', 'hidden', tenant_id='t1')
    store.remember('u1', 's3', MEMORY_KIND_LONG_TERM, 'pref', 'other_tenant', tenant_id='t2')
    values = [r.value for r in store.recall(user_id='u1', tenant_id='t1', kind=MEMORY_KIND_LONG_TERM)]
    assert values == ['visible']


def test_topic_router_followup_and_new_topic():
    router = TopicRouter()
    previous = {'metric': 'gmv', 'dimensions': ['channel'], 'filters': {'region': '华东'}, 'time_range': 'last7d', 'task_type': 'descriptive'}
    follow = router.route('按品类看一下', previous_context=previous)
    assert follow['route'] == ROUTE_FOLLOW_UP
    assert follow['resolved_context']['metric'] == 'gmv'
    assert follow['resolved_context']['dimensions'] == ['category']
    pivot = router.route('用户留存怎么样', previous_context=previous)
    assert pivot['route'] == ROUTE_NEW_TOPIC


def test_context_assembler_budget_and_evidence_compaction():
    assembler = MemoryContextAssembler(max_tokens=180, reserve_for_response=40, short_term_turns=2)
    cards = [EvidenceCard('t1', metric='gmv', dimensions=['channel'], summary='GMV by channel', dataid='d1', authority=AUTHORITY_VERIFIED)]
    records = [MemoryRecord('u1', 's1', MEMORY_KIND_LONG_TERM, 'pref', '默认看GMV', tenant_id='global')]
    result = assembler.assemble('继续', system_prompt='规则', current_plan={'metric': 'gmv'}, recent_messages=[{'content': 'old'}, {'content': 'new'}], evidence_cards=cards, long_term_records=records, access_context={'user_id': 'u1', 'tenant_id': 'global'})
    assert result['tokens_used'] <= result['token_budget']
    assert 'current_query' in [b['name'] for b in result['blocks']]
    assert '默认看GMV' in result['content']


def test_memory_quality_evaluator_passes_core_cases():
    service = MemoryContextService()
    cases = [
        {'query': '按渠道拆一下', 'previous_context': {'metric': 'gmv', 'dimensions': ['date'], 'filters': {}, 'time_range': 'last7d', 'task_type': 'descriptive'}, 'expected_route': 'follow_up', 'expected_inherited_fields': ['metric', 'filters', 'time_range', 'task_type'], 'user_id': 'u1', 'session_id': 's1'},
        {'query': '用户留存怎么样', 'previous_context': {'metric': 'gmv'}, 'expected_route': 'new_topic', 'expected_inherited_fields': [], 'user_id': 'u1', 'session_id': 's1'},
        {'query': '继续解释', 'previous_context': {'metric': 'gmv', 'dimensions': ['channel'], 'filters': {}, 'time_range': 'last7d', 'task_type': 'descriptive'}, 'expected_route': 'follow_up', 'expected_inherited_fields': ['metric', 'dimensions', 'filters', 'time_range', 'task_type'], 'user_id': 'u1', 'session_id': 's1', 'preferences': [{'user_id': 'u2', 'session_id': 's2', 'key': 'secret', 'value': 'DO_NOT_LEAK'}], 'forbidden_text': 'DO_NOT_LEAK'},
    ]
    evaluator = MemoryQualityEvaluator(service)
    metrics = evaluator.evaluate(cases)
    assert not evaluator.pass_thresholds(metrics, thresholds={'route_accuracy': 1.0, 'isolation_accuracy': 1.0, 'budget_pass_rate': 1.0})
