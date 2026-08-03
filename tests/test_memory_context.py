# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'src')
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from memory_contracts import EvidenceCard, AUTHORITY_VERIFIED, MEMORY_STATE_QUARANTINED
from memory_policy import MemoryPolicy
from task_anchor import DECISION_ALLOW, DECISION_QUARANTINE, TaskAnchor


def _anchor():
    return TaskAnchor(task_id='task-gmv', intent='breakdown', task_type='descriptive',
                      metric='gmv', dimensions=['channel'], time_range='last7d', confidence=0.9)


def test_anchor_allows_matching_evidence():
    card = EvidenceCard('task-gmv', metric='gmv', dimensions=['channel'], time_range='last7d',
                        authority=AUTHORITY_VERIFIED, summary='channel gmv')
    card, decision = MemoryPolicy().apply(_anchor(), card)
    assert decision.action == DECISION_ALLOW
    assert card.state == 'verified'


def test_anchor_quarantines_metric_mismatch():
    card = EvidenceCard('task-gmv', metric='order_count', dimensions=['channel'], time_range='last7d',
                        authority=AUTHORITY_VERIFIED, summary='orders by channel')
    card, decision = MemoryPolicy().apply(_anchor(), card)
    assert decision.action == DECISION_QUARANTINE
    assert card.state == MEMORY_STATE_QUARANTINED
    assert card.metadata['quarantine_reason'] == 'metric_mismatch'


def test_compacted_context_excludes_quarantine_and_deduplicates():
    policy = MemoryPolicy(max_cards=3, max_summary_chars=20)
    good = EvidenceCard('task-gmv', metric='gmv', dimensions=['channel'], time_range='last7d',
                        dataid='d1', authority=AUTHORITY_VERIFIED, summary='x' * 40)
    duplicate = EvidenceCard('task-gmv', metric='gmv', dimensions=['channel'], time_range='last7d',
                             dataid='d1', authority=AUTHORITY_VERIFIED, summary='x' * 40)
    bad = EvidenceCard('task-gmv', metric='order_count', dimensions=['channel'], time_range='last7d',
                       dataid='d2', authority=AUTHORITY_VERIFIED, summary='bad')
    good, unused = policy.apply(_anchor(), good)
    duplicate, unused = policy.apply(_anchor(), duplicate)
    bad, unused = policy.apply(_anchor(), bad)
    compact = policy.compact_context([good, duplicate, bad])
    assert len(compact) == 1
    assert compact[0]['dataid'] == 'd1'
    assert compact[0]['summary'].endswith('...')


def test_facade_records_anchor_and_evidence_trace():
    from agent_facade import AgentFacade
    facade = AgentFacade(session_id='memory-context-test')
    result = facade.ask(u'最近7天GMV')
    trace_names = [event['name'] for event in facade.get_trace(result['trace_id'])]
    assert 'task_anchor' in trace_names
    assert 'memory_retrieved' in trace_names
    diagnostics = result.get('diagnostics') or {}
    assert diagnostics.get('task_anchor', {}).get('metric') == 'gmv'
    assert isinstance(diagnostics.get('evidence_cards'), list)
