# -*- coding: utf-8 -*-
"""Deterministic BusinessCase state machine driven by domain events."""
from __future__ import unicode_literals

import time

from case_contracts import (
    BusinessCase, CaseEvent,
    CASE_NEW, CASE_SCOPING, CASE_SIGNAL_CONFIRMED, CASE_INVESTIGATING,
    CASE_HYPOTHESIS_PENDING, CASE_EVIDENCE_INSUFFICIENT, CASE_ROOT_CAUSE_CONFIRMED,
    CASE_ACTION_DRAFTED, CASE_PENDING_APPROVAL, CASE_ACTION_IN_PROGRESS,
    CASE_OUTCOME_MEASURING, CASE_RESOLVED, CASE_CLOSED,
    EVENT_CASE_CREATED, EVENT_SCOPE_RESOLVED, EVENT_SIGNAL_DETECTED,
    EVENT_DRIVER_DECOMPOSED, EVENT_HYPOTHESIS_PROPOSED, EVENT_HYPOTHESIS_CHALLENGED,
    EVENT_EVIDENCE_INSUFFICIENT, EVENT_ROOT_CAUSE_CONFIRMED, EVENT_ACTION_DRAFTED,
    EVENT_APPROVAL_REQUESTED, EVENT_APPROVAL_APPROVED, EVENT_ACTION_EXECUTED,
    EVENT_OUTCOME_MEASURED, EVENT_CASE_CLOSED,
)


_ALLOWED = {
    CASE_NEW: {
        EVENT_CASE_CREATED: CASE_SCOPING,
        EVENT_SCOPE_RESOLVED: CASE_SCOPING,
    },
    CASE_SCOPING: {
        EVENT_SCOPE_RESOLVED: CASE_SCOPING,
        EVENT_SIGNAL_DETECTED: CASE_SIGNAL_CONFIRMED,
        EVENT_EVIDENCE_INSUFFICIENT: CASE_EVIDENCE_INSUFFICIENT,
    },
    CASE_SIGNAL_CONFIRMED: {
        EVENT_DRIVER_DECOMPOSED: CASE_INVESTIGATING,
        EVENT_HYPOTHESIS_PROPOSED: CASE_HYPOTHESIS_PENDING,
        EVENT_EVIDENCE_INSUFFICIENT: CASE_EVIDENCE_INSUFFICIENT,
    },
    CASE_INVESTIGATING: {
        EVENT_HYPOTHESIS_PROPOSED: CASE_HYPOTHESIS_PENDING,
        EVENT_HYPOTHESIS_CHALLENGED: CASE_HYPOTHESIS_PENDING,
        EVENT_EVIDENCE_INSUFFICIENT: CASE_EVIDENCE_INSUFFICIENT,
        EVENT_ROOT_CAUSE_CONFIRMED: CASE_ROOT_CAUSE_CONFIRMED,
    },
    CASE_HYPOTHESIS_PENDING: {
        EVENT_HYPOTHESIS_CHALLENGED: CASE_HYPOTHESIS_PENDING,
        EVENT_EVIDENCE_INSUFFICIENT: CASE_EVIDENCE_INSUFFICIENT,
        EVENT_ROOT_CAUSE_CONFIRMED: CASE_ROOT_CAUSE_CONFIRMED,
        EVENT_ACTION_DRAFTED: CASE_ACTION_DRAFTED,
    },
    CASE_EVIDENCE_INSUFFICIENT: {
        EVENT_DRIVER_DECOMPOSED: CASE_INVESTIGATING,
        EVENT_HYPOTHESIS_PROPOSED: CASE_HYPOTHESIS_PENDING,
        EVENT_ROOT_CAUSE_CONFIRMED: CASE_ROOT_CAUSE_CONFIRMED,
        EVENT_CASE_CLOSED: CASE_CLOSED,
    },
    CASE_ROOT_CAUSE_CONFIRMED: {
        EVENT_ACTION_DRAFTED: CASE_ACTION_DRAFTED,
        EVENT_CASE_CLOSED: CASE_CLOSED,
    },
    CASE_ACTION_DRAFTED: {
        EVENT_APPROVAL_REQUESTED: CASE_PENDING_APPROVAL,
        EVENT_APPROVAL_APPROVED: CASE_ACTION_IN_PROGRESS,
        EVENT_ACTION_EXECUTED: CASE_ACTION_IN_PROGRESS,
        EVENT_CASE_CLOSED: CASE_CLOSED,
    },
    CASE_PENDING_APPROVAL: {
        EVENT_APPROVAL_APPROVED: CASE_ACTION_IN_PROGRESS,
        EVENT_CASE_CLOSED: CASE_CLOSED,
    },
    CASE_ACTION_IN_PROGRESS: {
        EVENT_ACTION_EXECUTED: CASE_ACTION_IN_PROGRESS,
        EVENT_OUTCOME_MEASURED: CASE_OUTCOME_MEASURING,
        EVENT_CASE_CLOSED: CASE_CLOSED,
    },
    CASE_OUTCOME_MEASURING: {
        EVENT_OUTCOME_MEASURED: CASE_RESOLVED,
        EVENT_CASE_CLOSED: CASE_CLOSED,
    },
    CASE_RESOLVED: {EVENT_CASE_CLOSED: CASE_CLOSED},
    CASE_CLOSED: {},
}


class CaseStateTransitionResult(object):
    def __init__(self, ok, previous_status, next_status=None, error=None, event=None):
        self.ok = bool(ok)
        self.previous_status = previous_status
        self.next_status = next_status or previous_status
        self.error = error
        self.event = event

    def to_dict(self):
        return {
            'ok': self.ok,
            'previous_status': self.previous_status,
            'next_status': self.next_status,
            'error': self.error,
            'event': self.event.to_dict() if hasattr(self.event, 'to_dict') else self.event,
        }


class CaseStateMachine(object):
    def can_apply(self, case_obj, event):
        case_obj = case_obj if isinstance(case_obj, BusinessCase) else BusinessCase.from_dict(case_obj)
        event = event if isinstance(event, CaseEvent) else CaseEvent.from_dict(event)
        return event.event_type in _ALLOWED.get(case_obj.status, {})

    def apply(self, case_obj, event):
        case_obj = case_obj if isinstance(case_obj, BusinessCase) else BusinessCase.from_dict(case_obj)
        event = event if isinstance(event, CaseEvent) else CaseEvent.from_dict(event)
        previous = case_obj.status
        mapping = _ALLOWED.get(previous, {})
        if event.event_type not in mapping:
            return CaseStateTransitionResult(False, previous, error='invalid_case_transition', event=event)
        case_obj.status = mapping[event.event_type]
        case_obj.updated_at = time.time()
        case_obj.timeline.append(event.to_dict())
        return CaseStateTransitionResult(True, previous, case_obj.status, event=event)


__all__ = ['CaseStateMachine', 'CaseStateTransitionResult']
