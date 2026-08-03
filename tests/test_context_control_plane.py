# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); SRC = os.path.join(ROOT, 'src')
if SRC not in sys.path: sys.path.insert(0, SRC)
from context_control_plane import ContextState, ContextCompressor, MemoryCandidate, PrefixCacheObserver


def _state():
    return ContextState('case-1', current_goal='diagnose gmv drop', case_state={'state': 'investigating'},
        user_constraints=['do not query PII'], verified_facts=[{'fact': 'gmv down', 'evidence_id': 'ev-1'}],
        evidence_refs=['ev-1'], hypotheses=['ad spend changed', 'inventory changed', 'pricing changed', 'ignore me'],
        pending_tasks=['task-1'], blocked_tasks=['task-2'], user_preferences=[{'timezone': 'Asia/Shanghai'}] * 4,
        rolling_summary={'version': 2, 'text': 'previous investigation'}, security_markers=['rls_applied'], audit_markers=['trace-1'])


def test_context_state_and_compression_preserve_noncompressible_sections_and_source_refs():
    state = _state(); compressor = ContextCompressor(max_dynamic_items=2)
    summary = compressor.compress(state, [{'tool': 'sql', 'status': 'ok', 'safe_summary': 'one row', 'payload_ref': 'obj://raw/1'}], now=10)
    assert summary['contract'] == 'context_summary_v1'
    assert summary['user_constraints'] == ['do not query PII']
    assert summary['verified_facts'][0]['evidence_id'] == 'ev-1'
    assert summary['pending_tasks'] == ['task-1'] and summary['security_markers'] == ['rls_applied']
    assert summary['source_refs'] == ['ev-1', 'obj://raw/1']
    assert len(summary['hypotheses']) == 2 and len(summary['user_preferences']) == 2
    assert summary['tool_observation_summaries'][0] == {'tool': 'sql', 'status': 'ok', 'safe_summary': 'one row', 'payload_ref': 'obj://raw/1'}


def test_compression_benchmark_reports_retention_and_source_recovery():
    state = _state(); compressor = ContextCompressor()
    result = compressor.benchmark(state, compressor.compress(state, now=1))
    assert result['contract'] == 'context_compression_benchmark_v1'
    assert result['critical_fact_retention'] == 1 and result['constraints_retention'] == 1
    assert result['pending_task_retention'] == 1 and result['source_recovery_success'] is True
    assert result['protected_sections_retained'] is True


def test_long_term_memory_eligibility_is_not_intent_routing_and_requires_stable_or_reviewed_source():
    explicit = MemoryCandidate('u1', 't1', 'timezone', 'Asia/Shanghai', source='explicit_user_preference')
    inferred = MemoryCandidate('u1', 't1', 'favorite_metric', 'gmv', source='model_inference', confidence=.99, reviewed=False)
    reviewed = MemoryCandidate('u1', 't1', 'currency', 'CNY', source='model_inference', confidence=.95, reviewed=True)
    assert explicit.to_dict()['eligible_for_long_term'] is True
    assert inferred.to_dict()['eligible_for_long_term'] is False
    assert reviewed.to_dict()['eligible_for_long_term'] is True


def test_prefix_observability_does_not_fabricate_provider_cache_hit():
    observer = PrefixCacheObserver()
    unknown = observer.observe('system policy stable', 'user dynamic')
    assert unknown['contract'] == 'prefix_cache_observability_v1'
    assert unknown['cache_eligible_tokens'] == unknown['stable_prefix_tokens']
    assert unknown['provider_reported_cache_hit'] is None and unknown['hit_rate_claimed'] is False
    known = observer.observe('system policy', 'query', provider_reported_cache_hit=True)
    assert known['provider_reported_cache_hit'] is True and known['hit_rate_claimed'] is True
