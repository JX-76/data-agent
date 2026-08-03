# -*- coding: utf-8 -*-
"""Stable, Python 2.7-compatible contracts for task-scoped agent memory."""
from __future__ import unicode_literals

import time
import uuid


MEMORY_STATE_WORKING = 'working'
MEMORY_STATE_VERIFIED = 'verified'
MEMORY_STATE_QUARANTINED = 'quarantined'
MEMORY_STATE_SUPERSEDED = 'superseded'

AUTHORITY_VERIFIED = 'verified'
AUTHORITY_INFERRED = 'inferred'
AUTHORITY_UNVERIFIED = 'unverified'


def new_memory_id():
    return str(uuid.uuid4())


def _list(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


class EvidenceCard(object):
    """Compact, traceable representation of a tool or execution result.

    The card is safe to inject into an LLM context; full result rows remain in
    the execution result/dataid and are deliberately not copied into memory.
    """
    def __init__(self, task_id, source='execution', summary='', metric=None,
                 dimensions=None, time_range=None, dataid=None, evidence_id=None,
                 authority=AUTHORITY_UNVERIFIED, relevance=0.0, confidence=0.0,
                 key_values=None, state=MEMORY_STATE_WORKING, metadata=None,
                 created_at=None):
        self.evidence_id = evidence_id or new_memory_id()
        self.task_id = task_id
        self.source = source
        self.summary = summary or ''
        self.metric = metric
        self.dimensions = _list(dimensions)
        self.time_range = time_range
        self.dataid = dataid
        self.authority = authority
        self.relevance = float(relevance or 0.0)
        self.confidence = float(confidence or 0.0)
        self.key_values = dict(key_values or {})
        self.state = state
        self.metadata = dict(metadata or {})
        self.created_at = created_at if created_at is not None else time.time()

    def to_dict(self):
        return {
            'evidence_id': self.evidence_id, 'task_id': self.task_id,
            'source': self.source, 'summary': self.summary,
            'metric': self.metric, 'dimensions': list(self.dimensions),
            'time_range': self.time_range, 'dataid': self.dataid,
            'authority': self.authority, 'relevance': self.relevance,
            'confidence': self.confidence, 'key_values': dict(self.key_values),
            'state': self.state, 'metadata': dict(self.metadata),
            'created_at': self.created_at,
        }


class SubtaskSummary(object):
    def __init__(self, task_id, summary, evidence_ids=None, verified_facts=None,
                 inferences=None, open_questions=None, contradictions=None):
        self.task_id = task_id
        self.summary = summary or ''
        self.evidence_ids = _list(evidence_ids)
        self.verified_facts = _list(verified_facts)
        self.inferences = _list(inferences)
        self.open_questions = _list(open_questions)
        self.contradictions = _list(contradictions)

    def to_dict(self):
        return {
            'task_id': self.task_id, 'summary': self.summary,
            'evidence_ids': list(self.evidence_ids),
            'verified_facts': list(self.verified_facts),
            'inferences': list(self.inferences),
            'open_questions': list(self.open_questions),
            'contradictions': list(self.contradictions),
        }


__all__ = ['EvidenceCard', 'SubtaskSummary', 'new_memory_id',
           'MEMORY_STATE_WORKING', 'MEMORY_STATE_VERIFIED',
           'MEMORY_STATE_QUARANTINED', 'MEMORY_STATE_SUPERSEDED',
           'AUTHORITY_VERIFIED', 'AUTHORITY_INFERRED', 'AUTHORITY_UNVERIFIED']
