# -*- coding: utf-8 -*-
"""Deterministic evidence-driven dynamic task planning for BusinessCase workflows."""
from __future__ import unicode_literals

from case_contracts import DynamicTaskSpec, CaseEvent
from evidence_freshness import assess_case_evidence_freshness
from gmv_health_playbook import build_gmv_health_dynamic_tasks, gmv_health_expected_scope

DECISION_APPEND_TASK = 'append_task'
DECISION_STOP = 'stop'
DECISION_NEED_CLARIFICATION = 'need_clarification'
DECISION_NEED_HUMAN_REVIEW = 'need_human_review'
DECISION_WAIT_FOR_EXTERNAL_EVENT = 'wait_for_external_event'


def _as_dict(value):
    return value if isinstance(value, dict) else {}


class DynamicTaskDecision(object):
    def __init__(self, decision, reason, task=None, metadata=None):
        self.decision = decision
        self.reason = reason
        self.task = task if isinstance(task, DynamicTaskSpec) else None
        self.metadata = _as_dict(metadata)

    def to_dict(self):
        return {'contract': 'dynamic_task_decision_v1', 'decision': self.decision,
                'reason': self.reason, 'task': self.task.to_dict() if self.task else None,
                'metadata': dict(self.metadata)}


class DynamicTaskPlanner(object):
    """Conservative planner; it only advances a GMV case after evidence exists."""
    def __init__(self, worker_capabilities=None, min_information_gain=0.01):
        self.worker_capabilities = worker_capabilities or {}
        self.min_information_gain = float(min_information_gain)

    def decide(self, board, executed_task_types=None, budget_used=None, now=None):
        executed = set(executed_task_types or [])
        budget_used = _as_dict(budget_used)
        case = board.case
        templates = build_gmv_health_dynamic_tasks(case)
        context = board.get_case_context()
        evidence_ids = set(context.get('evidence_view', {}).get('accepted_evidence_ids') or [])
        artifacts = list((context.get('artifacts') or {}).values())
        hypotheses = list((context.get('hypotheses') or {}).values())
        limit = int((case.budget or case.mission.budget or {}).get('max_dynamic_tasks') or 0)
        if limit and len(executed) >= limit:
            return DynamicTaskDecision(DECISION_STOP, 'budget_exhausted', metadata={'max_dynamic_tasks': limit})

        signal_exists = any(x.get('artifact_type') == 'signal' for x in artifacts)
        contribution_exists = any(x.get('artifact_type') == 'contribution' for x in artifacts)
        freshness = None
        if evidence_ids:
            freshness = assess_case_evidence_freshness(board, expected_scope=gmv_health_expected_scope(case), now=now)
            if freshness.get('needs_reexecution') and ('verify_gmv_signal' in executed or signal_exists):
                verify_task = templates[0]
                return self._append(verify_task, 'current_evidence_requires_reexecution', case,
                                    metadata={'freshness': freshness})
        for task in templates:
            if task.task_type in executed:
                continue
            if task.expected_information_gain < self.min_information_gain:
                return DynamicTaskDecision(DECISION_STOP, 'information_gain_too_low')
            if not self._worker_allowed(task):
                return DynamicTaskDecision(DECISION_STOP, 'worker_capability_missing', metadata={'worker_type': task.worker_type})
            if task.task_type == 'verify_gmv_signal':
                return self._append(task, 'signal_not_verified', case)
            if task.task_type == 'decompose_gmv_drivers':
                if signal_exists and evidence_ids:
                    return self._append(task, 'verified_signal_available', case)
                return DynamicTaskDecision(DECISION_STOP, 'verified_signal_required')
            if task.task_type == 'challenge_root_cause':
                if contribution_exists and hypotheses:
                    return self._append(task, 'hypothesis_and_contribution_available', case)
                return DynamicTaskDecision(DECISION_STOP, 'hypothesis_and_contribution_required')
        return DynamicTaskDecision(DECISION_STOP, 'no_new_information_gain')

    def record_decision(self, board, decision, source='dynamic_task_planner'):
        payload = decision.to_dict()
        board.append_event(CaseEvent(board.case.case_id, 'planner.%s' % decision.decision,
                                     payload=payload, source=source,
                                     evidence_ids=(payload.get('task') or {}).get('required_evidence') or []),
                           apply_state=False)
        return payload

    def _append(self, task, reason, case, metadata=None):
        payload = {'expected_scope': gmv_health_expected_scope(case)}
        payload.update(_as_dict(metadata))
        return DynamicTaskDecision(DECISION_APPEND_TASK, reason, task=task,
                                   metadata=payload)

    def _worker_allowed(self, task):
        if not self.worker_capabilities:
            return True
        allowed = self.worker_capabilities.get(task.worker_type)
        return allowed is True or task.intent in (allowed or [])


__all__ = ['DynamicTaskPlanner', 'DynamicTaskDecision', 'DECISION_APPEND_TASK',
           'DECISION_STOP', 'DECISION_NEED_CLARIFICATION', 'DECISION_NEED_HUMAN_REVIEW',
           'DECISION_WAIT_FOR_EXTERNAL_EVENT']
