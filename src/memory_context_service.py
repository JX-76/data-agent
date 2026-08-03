# -*- coding: utf-8 -*-
"""Production-oriented memory/context service for Phase D.

This module is intentionally dependency-light and Python 2.7 compatible.  It
builds on the existing MemoryStore, follow-up policy, TaskAnchor, MemoryPolicy
and context budget primitives without replacing them.
"""
from __future__ import unicode_literals

import time
import uuid

from followup_policy import classify_followup_intent, apply_context_policy, INHERITANCE_POLICY
from memory_contracts import EvidenceCard, AUTHORITY_VERIFIED
from memory_policy import MemoryPolicy
from task_anchor import TaskAnchor

try:
    from context.token_budget import estimate_tokens
except Exception:  # pragma: no cover
    def estimate_tokens(text):
        return max(1, int(len(text or '') / 4))


MEMORY_KIND_SHORT_TERM = 'short_term'
MEMORY_KIND_LONG_TERM = 'long_term'
MEMORY_KIND_EPISODIC = 'episodic'
MEMORY_KIND_SUMMARY = 'summary'

ROUTE_NEW_TOPIC = 'new_topic'
ROUTE_FOLLOW_UP = 'follow_up'
ROUTE_CLARIFY = 'clarify'


class MemoryRecord(object):
    def __init__(self, user_id, session_id, kind, key, value, record_id=None,
                 tenant_id='global', topic=None, source='agent', authority='unverified',
                 ttl_seconds=None, visibility='private', metadata=None, created_at=None):
        self.record_id = record_id or str(uuid.uuid4())
        self.user_id = user_id or 'anonymous'
        self.session_id = session_id or 'default'
        self.tenant_id = tenant_id or 'global'
        self.kind = kind or MEMORY_KIND_SHORT_TERM
        self.key = key or ''
        self.value = value
        self.topic = topic or ''
        self.source = source or 'agent'
        self.authority = authority or 'unverified'
        self.ttl_seconds = ttl_seconds
        self.visibility = visibility or 'private'
        self.metadata = dict(metadata or {})
        self.created_at = created_at if created_at is not None else time.time()

    @property
    def expires_at(self):
        if self.ttl_seconds is None:
            return None
        return self.created_at + self.ttl_seconds

    def expired(self, now=None):
        if self.ttl_seconds is None:
            return False
        return (now or time.time()) > self.expires_at

    def accessible(self, user_id=None, tenant_id=None):
        if tenant_id is not None and self.tenant_id not in ('global', tenant_id):
            return False
        if self.visibility == 'shared':
            return True
        return user_id is None or self.user_id == user_id

    def to_dict(self):
        return {
            'record_id': self.record_id,
            'user_id': self.user_id,
            'session_id': self.session_id,
            'tenant_id': self.tenant_id,
            'kind': self.kind,
            'key': self.key,
            'value': self.value,
            'topic': self.topic,
            'source': self.source,
            'authority': self.authority,
            'ttl_seconds': self.ttl_seconds,
            'expires_at': self.expires_at,
            'visibility': self.visibility,
            'metadata': dict(self.metadata),
            'created_at': self.created_at,
        }


class ManagedMemoryStore(object):
    """Governed in-memory records with TTL, tenant/user isolation and erasure."""
    def __init__(self, now=None):
        self.records = []
        self.now = now or time.time

    def remember_record(self, record):
        self.records.append(record)
        return record

    def remember(self, user_id, session_id, kind, key, value, **kwargs):
        kwargs.setdefault('created_at', self.now())
        return self.remember_record(MemoryRecord(user_id, session_id, kind, key, value, **kwargs))

    def recall(self, user_id=None, session_id=None, kind=None, key=None,
               tenant_id=None, topic=None, include_expired=False):
        out = []
        for record in self.records:
            if not include_expired and record.expired(self.now()):
                continue
            if user_id is not None and not record.accessible(user_id=user_id, tenant_id=tenant_id):
                continue
            if tenant_id is not None and not record.accessible(user_id=user_id, tenant_id=tenant_id):
                continue
            if session_id is not None and record.session_id != session_id:
                continue
            if kind is not None and record.kind != kind:
                continue
            if key is not None and record.key != key:
                continue
            if topic is not None and record.topic != topic:
                continue
            out.append(record)
        out.sort(key=lambda item: item.created_at)
        return out

    def forget_user(self, user_id):
        active_deleted = len([r for r in self.records if r.user_id == user_id and not r.expired(self.now())])
        self.records = [r for r in self.records if r.user_id != user_id]
        return active_deleted

    def purge_expired(self):
        before = len(self.records)
        self.records = [r for r in self.records if not r.expired(self.now())]
        return before - len(self.records)


class TopicRouter(object):
    """Route user turns into follow-up/new-topic/clarification branches."""
    def __init__(self, clarification_state=None):
        self.clarification_state = clarification_state

    def route(self, query, previous_context=None, session_id=None):
        if self.clarification_state is not None and session_id is not None:
            if self.clarification_state.has_pending(session_id):
                return {'route': ROUTE_CLARIFY, 'followup_intent': 'new_topic', 'reason': 'pending_clarification'}
        previous_context = dict(previous_context or {})
        if not previous_context:
            return {'route': ROUTE_NEW_TOPIC, 'followup_intent': 'new_topic', 'resolved_context': {}, 'reason': 'no_previous_context'}
        intent = classify_followup_intent(query)
        if intent == 'new_topic':
            return {'route': ROUTE_NEW_TOPIC, 'followup_intent': intent, 'resolved_context': {}, 'reason': 'classifier_new_topic'}
        patch = self._patch(query, intent)
        resolved = apply_context_policy(previous_context, patch, intent)
        inherited = [k for k in ['metric', 'dimensions', 'filters', 'time_range', 'task_type']
                     if INHERITANCE_POLICY[intent].get(k) and previous_context.get(k) is not None]
        return {'route': ROUTE_FOLLOW_UP, 'followup_intent': intent, 'resolved_context': resolved,
                'inherited_fields': inherited, 'overrides': patch, 'reason': 'policy_inheritance'}

    def _patch(self, query, intent):
        text = query or ''
        patch = {}
        if '淘宝' in text:
            patch.setdefault('filters', {})['channel'] = '淘宝'
        if '华东' in text:
            patch.setdefault('filters', {})['region'] = '华东'
        if '品类' in text:
            patch['dimensions'] = ['category']
        if '渠道' in text:
            patch['dimensions'] = ['channel']
        if '最近30天' in text:
            patch['time_range'] = 'last_30_days'
        if intent == 'comparison_request':
            patch['task_type'] = 'comparison'
            patch['compare_to'] = 'previous_week' if '上周' in text else 'previous_month'
        return patch


class MemoryContextAssembler(object):
    """Layered context assembler with token budget and traceable trimming."""
    def __init__(self, max_tokens=1600, reserve_for_response=256,
                 memory_policy=None, short_term_turns=4, max_long_term=4):
        self.max_tokens = int(max_tokens or 1600)
        self.reserve_for_response = int(reserve_for_response or 256)
        self.memory_policy = memory_policy or MemoryPolicy(max_cards=6)
        self.short_term_turns = int(short_term_turns or 4)
        self.max_long_term = int(max_long_term or 4)

    def assemble(self, query, system_prompt='', current_plan=None, recent_messages=None,
                 rolling_summary='', evidence_cards=None, long_term_records=None,
                 rag_evidence=None, access_context=None):
        budget = max(0, self.max_tokens - self.reserve_for_response)
        trace = []
        blocks = []
        self._add(blocks, trace, 'system', system_prompt, 100, budget)
        if current_plan:
            self._add(blocks, trace, 'task_state', self._format_dict(current_plan), 95, budget)
        if rolling_summary:
            self._add(blocks, trace, 'rolling_summary', rolling_summary, 90, budget)
        for idx, msg in enumerate((recent_messages or [])[-self.short_term_turns:]):
            self._add(blocks, trace, 'short_term_%s' % idx, msg.get('content') or '', 70, budget)
        long_term = self._filter_long_term(long_term_records or [], access_context or {})[:self.max_long_term]
        for idx, record in enumerate(long_term):
            self._add(blocks, trace, 'long_term_%s' % idx, self._record_text(record), 65, budget)
        compact_cards = self.memory_policy.compact_context(evidence_cards or [])
        for idx, card in enumerate(compact_cards):
            self._add(blocks, trace, 'evidence_card_%s' % idx, self._format_dict(card), 60, budget)
        for idx, ev in enumerate(rag_evidence or []):
            text = ev.get('supporting_extract') or ev.get('snippet') or ev.get('content') or ''
            self._add(blocks, trace, 'rag_evidence_%s' % idx, text, 55, budget)
        self._add(blocks, trace, 'current_query', query or '', 100, budget)
        blocks.sort(key=lambda item: item['priority'], reverse=True)
        selected = []
        used = 0
        dropped = []
        for block in blocks:
            if used + block['tokens'] <= budget:
                selected.append(block)
                used += block['tokens']
            else:
                dropped.append(block['name'])
        content = '\n\n'.join(['[%s]\n%s' % (b['name'], b['content']) for b in selected if b.get('content')])
        return {'content': content, 'blocks': selected, 'tokens_used': used, 'token_budget': budget,
                'dropped_blocks': dropped, 'trace': trace + [{'event': 'context_assembled', 'tokens_used': used, 'dropped_blocks': dropped}]}

    def _add(self, blocks, trace, name, content, priority, budget):
        content = content or ''
        tokens = estimate_tokens(content)
        if tokens > budget and budget > 0:
            keep_chars = max(20, budget * 3)
            content = content[:keep_chars] + '...[truncated]'
            tokens = estimate_tokens(content)
            trace.append({'event': 'context_block_trimmed', 'block': name, 'reason': 'single_block_over_budget'})
        blocks.append({'name': name, 'content': content, 'priority': priority, 'tokens': tokens})

    def _filter_long_term(self, records, access_context):
        user_id = access_context.get('user_id')
        tenant_id = access_context.get('tenant_id')
        out = []
        for record in records:
            if hasattr(record, 'expired') and record.expired():
                continue
            if hasattr(record, 'accessible') and not record.accessible(user_id=user_id, tenant_id=tenant_id):
                continue
            out.append(record)
        out.sort(key=lambda r: getattr(r, 'created_at', 0), reverse=True)
        return out

    def _record_text(self, record):
        data = record.to_dict() if hasattr(record, 'to_dict') else dict(record)
        return '%s:%s topic=%s source=%s' % (data.get('key'), data.get('value'), data.get('topic'), data.get('source'))

    def _format_dict(self, value):
        if hasattr(value, 'to_dict'):
            value = value.to_dict()
        if not isinstance(value, dict):
            return str(value)
        pairs = []
        for key in sorted(value.keys()):
            if key in ('raw_rows', 'rows'):
                continue
            pairs.append('%s=%s' % (key, value[key]))
        return '; '.join(pairs)


class MemoryContextService(object):
    def __init__(self, store=None, topic_router=None, assembler=None, memory_policy=None):
        self.store = store or ManagedMemoryStore()
        self.topic_router = topic_router or TopicRouter()
        self.memory_policy = memory_policy or MemoryPolicy()
        self.assembler = assembler or MemoryContextAssembler(memory_policy=self.memory_policy)

    def record_turn(self, user_id, session_id, query, result, tenant_id='global'):
        result = dict(result or {})
        topic = result.get('metric') or result.get('model') or result.get('intent') or ''
        self.store.remember(user_id, session_id, MEMORY_KIND_SHORT_TERM, 'turn',
                            {'query': query, 'result': result}, tenant_id=tenant_id,
                            topic=topic, ttl_seconds=86400, source='conversation')
        if result.get('status') == 'ok':
            self.store.remember(user_id, session_id, MEMORY_KIND_EPISODIC, 'task_result',
                                {'task_id': result.get('task_id'), 'metric': result.get('metric'), 'summary': result.get('summary') or result.get('answer')},
                                tenant_id=tenant_id, topic=topic, source='agent_result')
        return topic

    def remember_preference(self, user_id, session_id, key, value, tenant_id='global', topic='preference'):
        return self.store.remember(user_id, session_id, MEMORY_KIND_LONG_TERM, key, value,
                                   tenant_id=tenant_id, topic=topic, authority='verified', source='user_preference')

    def build_context(self, user_id, session_id, query, previous_context=None,
                      system_prompt='', current_plan=None, recent_messages=None,
                      rolling_summary='', evidence_cards=None, rag_evidence=None,
                      access_context=None):
        access = dict(access_context or {})
        access.setdefault('user_id', user_id)
        access.setdefault('tenant_id', access.get('tenant_id') or 'global')
        route = self.topic_router.route(query, previous_context=previous_context, session_id=session_id)
        long_term = self.store.recall(user_id=user_id, tenant_id=access.get('tenant_id'), kind=MEMORY_KIND_LONG_TERM)
        assembled = self.assembler.assemble(query, system_prompt=system_prompt, current_plan=current_plan,
                                            recent_messages=recent_messages, rolling_summary=rolling_summary,
                                            evidence_cards=evidence_cards, long_term_records=long_term,
                                            rag_evidence=rag_evidence, access_context=access)
        assembled['route'] = route
        return assembled

    def evidence_card_from_result(self, task_id, result, authority=AUTHORITY_VERIFIED):
        result = dict(result or {})
        return EvidenceCard(task_id=task_id, source='agent_result', summary=result.get('summary') or result.get('answer') or '',
                            metric=result.get('metric'), dimensions=result.get('dimensions') or [],
                            time_range=result.get('time_range'), dataid=result.get('dataid'),
                            authority=authority, confidence=1.0 if result.get('status') == 'ok' else 0.0,
                            key_values={'status': result.get('status')})

    def anchor_from_plan(self, plan, task_id=None):
        return TaskAnchor.from_plan(plan, task_id=task_id)


__all__ = ['MemoryRecord', 'ManagedMemoryStore', 'TopicRouter', 'MemoryContextAssembler',
           'MemoryContextService', 'MEMORY_KIND_SHORT_TERM', 'MEMORY_KIND_LONG_TERM',
           'MEMORY_KIND_EPISODIC', 'MEMORY_KIND_SUMMARY', 'ROUTE_NEW_TOPIC',
           'ROUTE_FOLLOW_UP', 'ROUTE_CLARIFY']
