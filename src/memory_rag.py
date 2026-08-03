# -*- coding: utf-8 -*-
"""Isolated long-term user/conversation memory retrieval for Data-Agent RAG.

This module deliberately stores memory separately from governed metric/schema/SOP
knowledge.  Memory may personalize a plan and recover conversation background,
but is never treated as a source of data facts or analytical conclusions.
"""
from __future__ import unicode_literals

import re

from memory_context_service import (
    ManagedMemoryStore, MEMORY_KIND_LONG_TERM, MEMORY_KIND_SUMMARY,
)

NAMESPACE_USER_PREFERENCE = 'user_memory'
NAMESPACE_CONVERSATION = 'conversation_memory'
NAMESPACE_SESSION = 'session_context'

try:
    text_type = unicode
except NameError:  # pragma: no cover - Python 3
    text_type = str


def _as_text(value):
    if value is None:
        return u''
    if isinstance(value, text_type):
        return value
    try:
        return value.decode('utf-8')
    except Exception:
        try:
            return value.decode('gbk')
        except Exception:
            return text_type(value)


def _tokens(text):
    text = _as_text(text).lower()
    return re.findall(r'[a-z0-9_]+|[\u4e00-\u9fff]', text)


class MemoryExtractor(object):
    """Conservative rule-based promotion. Never promotes model assertions as facts."""
    PREFERENCE_PATTERNS = [
        (u'默认', 'default'), (u'以后', 'default'), (u'偏好', 'preference'),
        (u'请用', 'format'), (u'不要', 'avoid'), (u'习惯', 'preference'),
    ]

    def extract(self, message, role='user'):
        text = _as_text(message).strip()
        if role != 'user' or not text:
            return []
        for marker, category in self.PREFERENCE_PATTERNS:
            if marker in text:
                return [{
                    'namespace': NAMESPACE_USER_PREFERENCE,
                    'key': category,
                    'value': text,
                    'confidence': 0.9,
                    'source': 'explicit_user_preference',
                }]
        return []

    def conversation_summary(self, query, result):
        result = dict(result or {})
        # Persist only a compact task state; no unverified answer text becomes fact.
        state = {
            'metric': result.get('metric'), 'dimensions': result.get('dimensions') or [],
            'filters': result.get('filters') or {}, 'time_range': result.get('time_range'),
            'task_type': result.get('task_type') or result.get('intent'),
            'task_id': result.get('task_id'), 'status': result.get('status'),
        }
        state = dict((key, value) for key, value in state.items() if value not in (None, '', [], {}))
        if not state:
            return None
        return {'namespace': NAMESPACE_CONVERSATION, 'key': 'task_state', 'value': state,
                'topic': state.get('metric') or state.get('task_type') or '',
                'confidence': 0.7, 'source': 'structured_conversation_summary'}


class MemoryRagStore(object):
    """Tenant/user isolated memory store with lightweight lexical retrieval.

    The backing store can be replaced by the persistence adapter while retaining
    this contract. ``disabled`` is checked at retrieval time so deletion and
    opt-out are effective immediately.
    """
    def __init__(self, store=None, extractor=None):
        self.store = store or ManagedMemoryStore()
        self.extractor = extractor or MemoryExtractor()
        self.disabled_users = set()

    def write_preference(self, user_id, session_id, key, value, tenant_id='global',
                         topic='preference', ttl_seconds=None, confidence=1.0):
        return self._write(user_id, session_id, NAMESPACE_USER_PREFERENCE, key, value,
                           tenant_id, topic, ttl_seconds, 'explicit_user_preference', confidence)

    def ingest_message(self, user_id, session_id, message, tenant_id='global', role='user'):
        records = []
        if self._identity_disabled(user_id, tenant_id):
            return records
        for item in self.extractor.extract(message, role=role):
            records.append(self._write(user_id, session_id, item['namespace'], item['key'], item['value'],
                                       tenant_id, 'preference', None, item['source'], item['confidence']))
        return records

    def remember_conversation(self, user_id, session_id, query, result, tenant_id='global', ttl_seconds=2592000):
        if self._identity_disabled(user_id, tenant_id):
            return None
        item = self.extractor.conversation_summary(query, result)
        if item is None:
            return None
        return self._write(user_id, session_id, item['namespace'], item['key'], item['value'], tenant_id,
                           item.get('topic'), ttl_seconds, item['source'], item['confidence'])

    def retrieve(self, query, user_id, tenant_id='global', session_id=None, namespaces=None, top_k=4):
        if self._identity_disabled(user_id, tenant_id):
            return []
        namespaces = set(namespaces or [NAMESPACE_USER_PREFERENCE, NAMESPACE_CONVERSATION])
        records = self.store.recall(user_id=user_id, tenant_id=tenant_id)
        scored = []
        query_tokens = set(_tokens(query))
        for record in records:
            meta = record.metadata or {}
            namespace = meta.get('rag_namespace')
            if namespace not in namespaces:
                continue
            if namespace == NAMESPACE_SESSION and session_id and record.session_id != session_id:
                continue
            text = '%s %s %s' % (record.key, record.value, record.topic)
            overlap = len(query_tokens.intersection(set(_tokens(text))))
            # Preferences are useful even without keyword overlap, but lower ranked.
            score = float(overlap) + (0.25 if namespace == NAMESPACE_USER_PREFERENCE else 0.0)
            scored.append((score, record.created_at, record))
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [self._to_evidence(item[2], item[0]) for item in scored[:top_k]]

    def forget_user(self, user_id, tenant_id=None):
        # Store already removes all user records. Retain disable state only when opted out.
        return self.store.forget_user(user_id)

    def set_enabled(self, user_id, tenant_id='global', enabled=True):
        identity = (tenant_id or 'global', user_id or 'anonymous')
        if enabled:
            self.disabled_users.discard(identity)
        else:
            self.disabled_users.add(identity)
            self.forget_user(user_id, tenant_id=tenant_id)

    def _write(self, user_id, session_id, namespace, key, value, tenant_id, topic, ttl_seconds, source, confidence):
        metadata = {'rag_namespace': namespace, 'confidence': float(confidence or 0.0),
                    'memory_is_fact': False, 'requires_tool_evidence_for_data_claim': True}
        return self.store.remember(user_id, session_id, MEMORY_KIND_LONG_TERM, key, value,
                                   tenant_id=tenant_id, topic=topic, ttl_seconds=ttl_seconds,
                                   source=source, authority='user_asserted', metadata=metadata)

    def _to_evidence(self, record, score):
        namespace = (record.metadata or {}).get('rag_namespace')
        return {
            'citation_id': 'M:%s' % record.record_id,
            'chunk_id': 'memory:%s' % record.record_id,
            'title': _as_text(record.key) or 'memory', 'supporting_extract': _as_text(record.value),
            'knowledge_type': namespace, 'type': namespace, 'source_uri': 'memory://%s/%s' % (record.tenant_id, record.user_id),
            'score': score, 'metadata': dict(record.metadata or {}, user_id=record.user_id,
                                               session_id=record.session_id, topic=record.topic,
                                               source=record.source, created_at=record.created_at),
        }

    def _identity_disabled(self, user_id, tenant_id):
        return (tenant_id or 'global', user_id or 'anonymous') in self.disabled_users


__all__ = ['MemoryRagStore', 'MemoryExtractor', 'NAMESPACE_USER_PREFERENCE',
           'NAMESPACE_CONVERSATION', 'NAMESPACE_SESSION']
