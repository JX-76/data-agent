# -*- coding: utf-8 -*-
"""Bounded, evidence-first runtime for ecommerce diagnostic RAG.

This module deliberately does not generate answers.  It owns the durable task
state around retrieval/tool calls so SOP and historical cases remain hypotheses,
while only current successful tool output becomes evidence.
"""
from __future__ import unicode_literals

import copy
import time
import uuid


IDLE = 'IDLE'
COMPARING_OVERALL = 'COMPARING_OVERALL'
DRILLING_CHANNEL = 'DRILLING_CHANNEL'
DRILLING_PRODUCT = 'DRILLING_PRODUCT'
CHECKING_REVIEWS_COMPETITORS = 'CHECKING_REVIEWS_COMPETITORS'
GENERATING_REPORT = 'GENERATING_REPORT'
VERIFYING = 'VERIFYING'
COMPLETE = 'COMPLETE'
NEED_CLARIFICATION = 'NEED_CLARIFICATION'
DEGRADED = 'DEGRADED'
BLOCKED = 'BLOCKED'

ACTIVE_STATES = (COMPARING_OVERALL, DRILLING_CHANNEL, DRILLING_PRODUCT,
                 CHECKING_REVIEWS_COMPETITORS, GENERATING_REPORT, VERIFYING)
TERMINAL_STATES = (COMPLETE, NEED_CLARIFICATION, DEGRADED, BLOCKED)
DEFAULT_PATH = (COMPARING_OVERALL, DRILLING_CHANNEL, DRILLING_PRODUCT,
                CHECKING_REVIEWS_COMPETITORS, GENERATING_REPORT, VERIFYING,
                COMPLETE)


def _text(value):
    if value is None:
        return ''
    return value if isinstance(value, str) else str(value)


def _compact(value, limit=480):
    text = _text(value).replace('\n', ' ').strip()
    return text if len(text) <= limit else text[:limit - 1] + u'…'


class InMemoryDiagnosisStateStore(object):
    """Checkpoint store interface; can later be replaced by Redis/Postgres."""
    def __init__(self):
        self._items = {}

    def save(self, state):
        value = copy.deepcopy(state)
        value['checkpoint_version'] = int(value.get('checkpoint_version', 0)) + 1
        value['updated_at'] = time.time()
        self._items[value['task_id']] = value
        return copy.deepcopy(value)

    def load(self, task_id):
        value = self._items.get(task_id)
        return copy.deepcopy(value) if value else None


class ToolDiscoveryService(object):
    """Policy-first candidate selection. Never exposes the whole tool registry."""
    STATE_CAPABILITIES = {
        COMPARING_OVERALL: ('ecommerce.overview', 'warehouse.query_sql', 'semantic.catalog_read'),
        DRILLING_CHANNEL: ('ecommerce.channel_performance', 'ecommerce.funnel', 'warehouse.query_sql'),
        DRILLING_PRODUCT: ('ecommerce.product_performance', 'ecommerce.inventory', 'warehouse.query_sql'),
        CHECKING_REVIEWS_COMPETITORS: ('ecommerce.review_sentiment', 'ecommerce.competitor_price'),
    }

    def discover(self, state, registry=None, access_context=None, limit=8):
        state = state or {}
        candidates = list(self.STATE_CAPABILITIES.get(state.get('status'), ()))
        available = set()
        if registry is not None:
            available = set([x.get('tool_id') for x in registry.list_tools()])
            candidates = [x for x in candidates if x in available]
        budget = (state.get('tool_budget') or {})
        remaining = max(0, int(budget.get('max_calls', 8)) - int(budget.get('used', 0)))
        candidates = candidates[:min(int(limit), remaining)]
        return {
            'candidate_tool_ids': candidates,
            'selection_reason': 'state_capability_allowlist:%s' % state.get('status'),
            'excluded_by_policy': [] if remaining else ['tool_budget_exhausted'],
            'tool_budget_remaining': remaining,
        }


class DiagnosisVerifier(object):
    """Deterministic last gate for evidence-backed findings and recommendations."""
    def verify(self, state, report):
        state = state or {}
        report = report or {}
        evidence_ids = set([x.get('evidence_id') for x in state.get('evidence', [])])
        failures = []
        for finding in report.get('findings', []) or []:
            refs = set(finding.get('evidence_refs') or [])
            if finding.get('kind', 'fact') == 'fact' and (not refs or not refs.issubset(evidence_ids)):
                failures.append('unsupported_finding:%s' % finding.get('id', 'unknown'))
        for action in report.get('recommendations', []) or []:
            if action.get('requires_approval') and not action.get('approval_id'):
                failures.append('unapproved_action:%s' % action.get('id', 'unknown'))
            if action.get('forbidden_by_policy'):
                failures.append('policy_forbidden_action:%s' % action.get('id', 'unknown'))
        return {'decision': 'pass' if not failures else 'rewrite_report', 'failures': failures,
                'evidence_count': len(evidence_ids)}


class DiagnosisRuntime(object):
    """Explicit diagnostic state machine with bounded calls and resumable evidence."""
    def __init__(self, store=None, discovery=None, verifier=None, max_tool_calls=8):
        self.store = store or InMemoryDiagnosisStateStore()
        self.discovery = discovery or ToolDiscoveryService()
        self.verifier = verifier or DiagnosisVerifier()
        self.max_tool_calls = int(max_tool_calls)

    def start(self, shop_id, trigger, task_id=None):
        if not shop_id:
            raise ValueError('shop_id is required')
        state = {'task_id': task_id or str(uuid.uuid4()), 'shop_id': shop_id,
                 'trigger': dict(trigger or {}), 'status': COMPARING_OVERALL,
                 'hypotheses': [], 'evidence': [], 'findings': [], 'transitions': [],
                 'tool_budget': {'max_calls': self.max_tool_calls, 'used': 0},
                 'checkpoint_version': 0, 'created_at': time.time()}
        return self.store.save(state)

    def resume(self, task_id):
        state = self.store.load(task_id)
        if state is None:
            raise KeyError('diagnosis task not found: %s' % task_id)
        return state

    def candidates(self, task_id, registry=None, access_context=None):
        return self.discovery.discover(self.resume(task_id), registry, access_context)

    def record_tool_result(self, task_id, tool_id, result, purpose=None):
        state = self.resume(task_id)
        if state['status'] not in ACTIVE_STATES:
            raise ValueError('cannot call tool in terminal state')
        budget = state['tool_budget']
        if budget['used'] >= budget['max_calls']:
            return self._transition(state, DEGRADED, 'tool_budget_exhausted')
        budget['used'] += 1
        result = result or {}
        if result.get('status') == 'ok':
            data = result.get('data') or {}
            evidence = {'evidence_id': 'ev_%03d' % len(state['evidence']), 'tool_id': tool_id,
                        'purpose': purpose or '', 'captured_at': time.time(),
                        'authority': 'current_tool_execution', 'summary': _compact(data),
                        'schema': sorted(data.keys()) if isinstance(data, dict) else []}
            state['evidence'].append(evidence)
        else:
            state.setdefault('tool_errors', []).append({'tool_id': tool_id,
                'failure_type': (result.get('diagnostics') or {}).get('failure_type', 'tool_error')})
        return self.store.save(state)

    def advance(self, task_id, reason='sop_complete'):
        state = self.resume(task_id)
        if state['status'] in TERMINAL_STATES:
            return state
        index = DEFAULT_PATH.index(state['status'])
        return self._transition(state, DEFAULT_PATH[index + 1], reason)

    def verify_report(self, task_id, report):
        state = self.resume(task_id)
        if state['status'] != VERIFYING:
            raise ValueError('report verification requires VERIFYING state')
        outcome = self.verifier.verify(state, report)
        state['verification'] = outcome
        if outcome['decision'] == 'pass':
            state['report'] = report
            return self._transition(state, COMPLETE, 'verification_passed')
        return self._transition(state, GENERATING_REPORT, 'verification_rewrite_required')

    def _transition(self, state, target, reason):
        state['transitions'].append({'from': state['status'], 'to': target,
                                     'reason': reason, 'at': time.time()})
        state['status'] = target
        return self.store.save(state)


__all__ = ['DiagnosisRuntime', 'DiagnosisVerifier', 'ToolDiscoveryService',
           'InMemoryDiagnosisStateStore', 'COMPARING_OVERALL', 'DRILLING_CHANNEL',
           'DRILLING_PRODUCT', 'CHECKING_REVIEWS_COMPETITORS', 'GENERATING_REPORT',
           'VERIFYING', 'COMPLETE', 'DEGRADED']
