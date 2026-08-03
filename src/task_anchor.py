# -*- coding: utf-8 -*-
"""Task-anchor guard that prevents irrelevant evidence from polluting context."""
from __future__ import unicode_literals

import uuid


DECISION_ALLOW = 'allow'
DECISION_QUARANTINE = 'quarantine'
DECISION_CLARIFICATION = 'clarification'
DECISION_PIVOT = 'pivot'


def _as_dict(value):
    if isinstance(value, dict):
        return value
    if hasattr(value, 'to_dict'):
        return value.to_dict()
    try:
        return dict(value)
    except Exception:
        return {}


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _as_filter_dict(value):
    """Return a stable filter mapping without raising on list-style filters.

    Router outputs may represent filters as a list of clauses.  TaskAnchor is a
    compact semantic key and must not turn a harmless list into a runtime error.
    Keep dict filters as-is; wrap list/scalar forms under a non-factual clause
    key so downstream comparisons remain deterministic and bounded.
    """
    if not value:
        return {}
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, (list, tuple)):
        return {"clauses": list(value)} if value else {}
    return {"value": value}


class AnchorDecision(object):
    def __init__(self, action, reason, relevance=0.0, conflicts=None):
        self.action = action
        self.reason = reason
        self.relevance = float(relevance)
        self.conflicts = list(conflicts or [])

    def to_dict(self):
        return {'action': self.action, 'reason': self.reason,
                'relevance': self.relevance, 'conflicts': list(self.conflicts)}


class TaskAnchor(object):
    """Immutable semantic target derived from a normalized analysis plan."""
    def __init__(self, task_id=None, parent_task_id=None, intent=None,
                 task_type=None, metric=None, metrics=None, dimensions=None,
                 time_range=None, filters=None, confidence=0.0, query=None,
                 version=1, state='active'):
        self.task_id = task_id or str(uuid.uuid4())
        self.parent_task_id = parent_task_id
        self.intent = intent
        self.task_type = task_type
        self.metric = metric
        self.metrics = _as_list(metrics or metric)
        self.dimensions = _as_list(dimensions)
        self.time_range = time_range
        self.filters = _as_filter_dict(filters)
        self.confidence = float(confidence or 0.0)
        self.query = query or ''
        self.version = int(version or 1)
        self.state = state

    @classmethod
    def from_plan(cls, plan, task_id=None):
        data = _as_dict(plan)
        return cls(task_id=task_id or data.get('task_id'),
                   parent_task_id=data.get('parent_task_id'),
                   intent=data.get('intent'), task_type=data.get('task_type'),
                   metric=data.get('metric'), metrics=data.get('metrics'),
                   dimensions=data.get('dimensions'),
                   time_range=data.get('time_range') or data.get('time_range_label'),
                   filters=data.get('filters'), confidence=data.get('confidence', 0.0),
                   query=data.get('query'))

    def to_dict(self):
        return {'task_id': self.task_id, 'parent_task_id': self.parent_task_id,
                'intent': self.intent, 'task_type': self.task_type,
                'metric': self.metric, 'metrics': list(self.metrics),
                'dimensions': list(self.dimensions), 'time_range': self.time_range,
                'filters': dict(self.filters), 'confidence': self.confidence,
                'query': self.query, 'version': self.version, 'state': self.state}

    def requires_clarification(self, minimum_confidence=0.55):
        return (not self.metric or self.confidence < minimum_confidence)

    def assess(self, evidence):
        """Return an allow/quarantine decision without mutating evidence."""
        item = _as_dict(evidence)
        conflicts = []
        score = 1.0
        evidence_metric = item.get('metric')
        if evidence_metric and self.metrics and evidence_metric not in self.metrics:
            conflicts.append('metric_mismatch')
            score -= 0.65
        evidence_dims = set(_as_list(item.get('dimensions')))
        anchor_dims = set(self.dimensions)
        if evidence_dims and anchor_dims and not evidence_dims.intersection(anchor_dims):
            conflicts.append('dimension_mismatch')
            score -= 0.25
        evidence_range = item.get('time_range')
        if evidence_range and self.time_range and evidence_range != self.time_range:
            conflicts.append('time_range_mismatch')
            score -= 0.15
        if item.get('task_type') and self.task_type and item.get('task_type') != self.task_type:
            conflicts.append('task_type_mismatch')
            score -= 0.20
        score = max(0.0, score)
        if conflicts:
            return AnchorDecision(DECISION_QUARANTINE, conflicts[0], score, conflicts)
        return AnchorDecision(DECISION_ALLOW, 'anchor_compatible', score, [])

    def pivot(self, plan):
        next_anchor = TaskAnchor.from_plan(plan)
        next_anchor.parent_task_id = self.task_id
        next_anchor.version = self.version + 1
        return next_anchor


__all__ = ['TaskAnchor', 'AnchorDecision', 'DECISION_ALLOW',
           'DECISION_QUARANTINE', 'DECISION_CLARIFICATION', 'DECISION_PIVOT']
