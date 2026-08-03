# -*- coding: utf-8 -*-
"""Idempotent in-process dispatcher for evidence re-execution plans.

The dispatcher is intentionally a control-plane adapter: it schedules only the
verification task described by a freshness decision and records lifecycle
transitions on the CaseBlackboard.  It never upgrades an old artifact or a
failed execution into current evidence.
"""
from __future__ import unicode_literals

from case_contracts import CaseEvent, DynamicTaskSpec
from rag_governance import IdempotencyKeyBuilder

REEXECUTION_DISPATCH_CONTRACT = 'evidence_reexecution_dispatch_v1'


def _as_dict(value):
    return dict(value) if isinstance(value, dict) else {}


def _event_schedule(events, idempotency_key):
    for event in reversed(list(events or [])):
        event = _as_dict(event)
        payload = _as_dict(event.get('payload'))
        if payload.get('idempotency_key') == idempotency_key:
            return event
    return None


class EvidenceReexecutionDispatcher(object):
    """Create one auditable verification task per case/scope/reason tuple."""
    def __init__(self, key_builder=None):
        self.key_builder = key_builder or IdempotencyKeyBuilder()

    def dispatch(self, board, plan, tenant_id=None, session_id=None):
        plan = _as_dict(plan)
        case = board.case
        expected_scope = _as_dict(plan.get('expected_scope'))
        if plan.get('contract') != 'evidence_reexecution_plan_v1':
            return self._result('blocked', case.case_id, 'invalid_reexecution_plan')
        if not plan.get('required'):
            return self._result('not_required', case.case_id, 'reexecution_not_required')
        if not plan.get('task_type') or not expected_scope:
            return self._result('blocked', case.case_id, 'incomplete_reexecution_plan')

        key = self.key_builder.build(
            tenant_id=tenant_id, session_id=session_id or case.case_id,
            task_id=case.case_id, stage='evidence_reexecution:%s' % plan.get('task_type'),
            input_value={'case_id': case.case_id, 'reason': plan.get('reason'),
                         'expected_scope': expected_scope,
                         'invalid_evidence_ids': plan.get('invalid_evidence_ids') or []},
            data_version=expected_scope.get('data_version'),
            policy_version='evidence_reexecution_dispatch_v1').get('idempotency_key')
        prior = _event_schedule(board.events, key)
        if prior:
            return self._result('duplicate', case.case_id, 'idempotency_replay',
                                idempotency_key=key, task=_as_dict(prior.get('payload')).get('task'),
                                event_id=prior.get('event_id'))

        task = DynamicTaskSpec(
            plan.get('task_type'), goal='Refresh current verified execution evidence',
            inputs=[expected_scope], preconditions=['current_verified_execution_evidence_required'],
            expected_information_gain=1.0, authority='readonly', worker_type='data_analysis',
            intent='verify', priority='high',
            metadata={'reexecution_plan': plan, 'expected_scope': expected_scope,
                      'idempotency_key': key})
        payload = {'contract': REEXECUTION_DISPATCH_CONTRACT, 'idempotency_key': key,
                   'plan': plan, 'task': task.to_dict(), 'status': 'scheduled'}
        event = board.append_event(CaseEvent(case.case_id, 'reexecution.scheduled', payload=payload,
                                             source='evidence_reexecution_dispatcher'), apply_state=False)
        return self._result('scheduled', case.case_id, plan.get('reason'), idempotency_key=key,
                            task=task.to_dict(), event_id=event.get('event_id'))

    def complete(self, board, dispatch, execution_envelope, now=None):
        """Record completion only when a new verified execution envelope validates."""
        dispatch = _as_dict(dispatch)
        if dispatch.get('status') not in ('scheduled', 'duplicate'):
            return self._result('blocked', board.case.case_id, 'reexecution_not_scheduled')
        task = _as_dict(dispatch.get('task'))
        metadata = _as_dict(task.get('metadata'))
        scope = _as_dict(metadata.get('expected_scope'))
        record = board.record_execution_envelope(execution_envelope,
                                                 producer_task_id=task.get('task_id'),
                                                 expected_scope=scope, now=now)
        key = dispatch.get('idempotency_key') or metadata.get('idempotency_key')
        if not record:
            status, reason = 'failed', 'reexecution_execution_not_verified'
        else:
            status, reason = 'completed', 'current_evidence_verified'
        board.append_event(CaseEvent(board.case.case_id, 'reexecution.%s' % status,
                                     payload={'contract': REEXECUTION_DISPATCH_CONTRACT,
                                              'idempotency_key': key, 'status': status,
                                              'reason': reason,
                                              'evidence_id': record.get('evidence_id') if record else None},
                                     evidence_ids=[record.get('evidence_id')] if record else [],
                                     source='evidence_reexecution_dispatcher'), apply_state=False)
        return self._result(status, board.case.case_id, reason, idempotency_key=key,
                            evidence_id=record.get('evidence_id') if record else None)

    def _result(self, status, case_id, reason, idempotency_key=None, task=None, event_id=None, evidence_id=None):
        return {'contract': REEXECUTION_DISPATCH_CONTRACT, 'status': status, 'case_id': case_id,
                'reason': reason, 'idempotency_key': idempotency_key, 'task': task,
                'event_id': event_id, 'evidence_id': evidence_id}


__all__ = ['EvidenceReexecutionDispatcher', 'REEXECUTION_DISPATCH_CONTRACT']
