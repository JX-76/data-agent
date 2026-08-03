# -*- coding: utf-8 -*-
"""Case-level dynamic task orchestration control plane.

This module keeps runtime task append decisions outside framework-specific graph
state.  It drives a BusinessCase through conservative planner decisions, executes
one appended task at a time, and writes every append/reject/stop/completion back
to the CaseBlackboard timeline.
"""
from __future__ import unicode_literals

import time
import uuid

from case_contracts import CaseArtifact, Hypothesis, CaseEvent, ARTIFACT_SIGNAL, ARTIFACT_CONTRIBUTION
from dynamic_task_planner import DynamicTaskPlanner, DECISION_APPEND_TASK, DECISION_STOP
from gmv_health_playbook import gmv_health_expected_scope
from multi_agent_contracts import AgentTask, RESULT_OK
from supervisor_runtime import SupervisorRuntime
from durable_task_control_plane import DurableTaskControlPlane, TaskRecord, TaskConflictError


ORCHESTRATOR_CONTRACT = 'case_orchestrator_result_v1'
EVENT_TASK_APPENDED = 'orchestrator.task_appended'
EVENT_TASK_REJECTED = 'orchestrator.task_rejected'
EVENT_TASK_COMPLETED = 'orchestrator.task_completed'
EVENT_TASK_FAILED = 'orchestrator.task_failed'
EVENT_STOPPED = 'orchestrator.stopped'


def _new_id(prefix):
    return '%s_%s' % (prefix, str(uuid.uuid4()).replace('-', '')[:16])


def _as_dict(value):
    return value if isinstance(value, dict) else {}


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


class CaseOrchestrator(object):
    """Bounded dynamic loop for CaseBlackboard + DynamicTaskPlanner."""

    def __init__(self, planner=None, runtime=None, task_executor=None,
                 max_rounds=6, max_appended_tasks=None, durable_task_control_plane=None):
        self.planner = planner or DynamicTaskPlanner()
        self.runtime = runtime or SupervisorRuntime(max_steps=10, semaphore_limit=1)
        self.task_executor = task_executor
        self.max_rounds = int(max_rounds or 6)
        self.max_appended_tasks = max_appended_tasks
        # Optional during migration: preserving the existing synchronous executor
        # contract while writing durable lifecycle/audit state for every append.
        self.durable_task_control_plane = durable_task_control_plane

    def run_case_loop(self, board, executed_task_types=None, trace_id=None, session_id=None, now=None):
        trace_id = trace_id or _new_id('trace')
        executed = list(executed_task_types or [])
        appended = []
        decisions = []
        runtime_results = []
        errors = []
        stop_reason = None
        t0 = time.time()

        for round_index in range(self.max_rounds):
            decision = self.planner.decide(board, executed_task_types=executed, now=now)
            decision_payload = self.planner.record_decision(board, decision, source='case_orchestrator')
            decision_payload['round'] = round_index + 1
            decisions.append(decision_payload)

            if decision.decision != DECISION_APPEND_TASK:
                stop_reason = decision.reason
                self._event(board, EVENT_STOPPED, {
                    'reason': stop_reason, 'decision': decision.to_dict(), 'round': round_index + 1,
                })
                break

            task = decision.task
            rejection = self._validate_append(task, executed, appended, board)
            if rejection:
                errors.append(rejection)
                self._event(board, EVENT_TASK_REJECTED, rejection)
                stop_reason = rejection.get('error')
                break

            agent_task = self._to_agent_task(task, trace_id=trace_id)
            appended.append(task.to_dict())
            self._event(board, EVENT_TASK_APPENDED, {
                'task': task.to_dict(), 'agent_task': agent_task.to_dict(),
                'reason': decision.reason, 'round': round_index + 1,
            })

            result = self._execute(agent_task, board, trace_id=trace_id, session_id=session_id)
            runtime_results.append(result)
            if not self._result_ok(result, agent_task.task_id):
                errors.append({'error': 'task_execution_failed', 'task_type': task.task_type,
                               'task_id': agent_task.task_id, 'result': result})
                self._event(board, EVENT_TASK_FAILED, {
                    'task': task.to_dict(), 'agent_task': agent_task.to_dict(), 'result': result,
                })
                stop_reason = 'task_execution_failed'
                break

            executed.append(task.task_type)
            self._integrate_result(board, task, result, now=now)
            self._event(board, EVENT_TASK_COMPLETED, {
                'task': task.to_dict(), 'agent_task': agent_task.to_dict(), 'result': result,
            })
        else:
            stop_reason = 'max_rounds_exhausted'
            self._event(board, EVENT_STOPPED, {'reason': stop_reason, 'max_rounds': self.max_rounds})

        return {
            'contract': ORCHESTRATOR_CONTRACT,
            'status': 'error' if errors else 'ok',
            'trace_id': trace_id,
            'session_id': session_id,
            'executed_task_types': list(executed),
            'appended_tasks': list(appended),
            'decisions': list(decisions),
            'runtime_results': list(runtime_results),
            'errors': list(errors),
            'stop_reason': stop_reason,
            'case_context': board.get_case_context(),
            'metrics': {
                'rounds': len(decisions),
                'appended_count': len(appended),
                'duration_ms': int((time.time() - t0) * 1000),
            },
        }

    def _validate_append(self, task, executed, appended, board):
        if task is None:
            return {'error': 'missing_dynamic_task'}
        max_tasks = self.max_appended_tasks
        if max_tasks is None:
            budget = board.case.budget or board.case.mission.budget or {}
            max_tasks = budget.get('max_dynamic_tasks')
        if max_tasks and len(appended) >= int(max_tasks):
            return {'error': 'budget_exhausted', 'max_dynamic_tasks': int(max_tasks)}
        if task.task_type in set(executed):
            return {'error': 'duplicate_task_type', 'task_type': task.task_type}
        keys = set([item.get('metadata', {}).get('idempotency_key') for item in appended])
        idem = self._idempotency_key(task, board)
        if idem in keys:
            return {'error': 'duplicate_idempotency_key', 'idempotency_key': idem}
        if task.expected_information_gain <= 0:
            return {'error': 'non_positive_information_gain', 'task_type': task.task_type}
        return None

    def _to_agent_task(self, task, trace_id=None):
        metadata = dict(task.metadata or {})
        metadata['dynamic_task_type'] = task.task_type
        metadata['expected_information_gain'] = task.expected_information_gain
        metadata['idempotency_key'] = self._idempotency_key(task, None)
        return AgentTask(
            worker_type=task.worker_type,
            task_input={
                'dynamic_task': task.to_dict(),
                'business_scope': self._input_value(task, 'business_scope'),
            },
            task_id=task.task_id,
            intent=task.intent,
            dependencies=[],
            budget=task.cost_budget,
            risk_level=task.risk_level,
            idempotency_key=metadata['idempotency_key'],
            metadata=metadata,
        )

    def _idempotency_key(self, task, board=None):
        case_id = None
        if board is not None:
            case_id = board.case.case_id
        case_id = case_id or (task.metadata or {}).get('case_id') or 'case'
        return 'idem:case_task:%s:%s' % (case_id, task.task_type)

    def _input_value(self, task, name):
        for item in _as_list(task.inputs):
            item = _as_dict(item)
            if item.get('name') == name:
                return item.get('value')
        return None

    def _execute(self, agent_task, board, trace_id=None, session_id=None):
        if self.durable_task_control_plane is None:
            if self.task_executor is not None:
                return self.task_executor(agent_task, board)
            return self.runtime.run([agent_task.to_dict()], trace_id=trace_id, session_id=session_id)

        control = self.durable_task_control_plane
        record, duplicate = control.submit(TaskRecord(
            task_id=agent_task.task_id, case_id=board.case.case_id, trace_id=trace_id,
            session_id=session_id, task_type=agent_task.metadata.get('dynamic_task_type') or agent_task.intent,
            worker_type=agent_task.worker_type, input_ref=agent_task.input,
            idempotency_key=agent_task.idempotency_key, dependencies=list(agent_task.dependencies or []),
            risk_level=agent_task.risk_level, budget=agent_task.budget.to_dict() if hasattr(agent_task.budget, 'to_dict') else agent_task.budget))
        if duplicate and record.state == 'succeeded':
            return {'status': RESULT_OK, 'output': {'durable_task_id': record.task_id, 'output_ref': record.output_ref},
                    'durable_task': control.status_snapshot(record.task_id)}
        try:
            control.claim(record.task_id, worker_id='case_orchestrator')
        except TaskConflictError:
            return {'status': 'error', 'errors': [{'error': 'durable_task_not_claimable', 'task_id': record.task_id}],
                    'durable_task': control.status_snapshot(record.task_id)}
        if self.task_executor is not None:
            result = self.task_executor(agent_task, board)
        else:
            result = self.runtime.run([agent_task.to_dict()], trace_id=trace_id, session_id=session_id)
        if self._result_ok(result, agent_task.task_id):
            control.succeed(record.task_id, output_ref='case_result:%s' % agent_task.task_id)
        else:
            errors = _as_list(_as_dict(result).get('errors'))
            error = _as_dict(errors[0]) if errors else {}
            code = error.get('error') or error.get('error_code') or 'executor_failed'
            error_class = 'transient' if code in ('db_timeout', 'timeout', '503', '429') else 'runtime'
            control.fail(record.task_id, code, error_class, summary=error.get('message') or code)
        result = dict(result or {})
        result['durable_task'] = control.status_snapshot(record.task_id)
        return result

    def _result_ok(self, result, task_id):
        result = _as_dict(result)
        if result.get('status') == RESULT_OK:
            return True
        node_states = _as_dict(result.get('node_states'))
        return result.get('status') == RESULT_OK and node_states.get(task_id) == 'succeeded'

    def _integrate_result(self, board, task, result, now=None):
        output = self._task_output(result, task.task_id)
        envelope = output.get('execution_envelope')
        evidence_ids = list(output.get('evidence_ids') or [])
        if envelope:
            record = board.record_execution_envelope(
                envelope, producer_task_id=task.task_id,
                trace_id=output.get('trace_id'), graph_type='case_orchestrator',
                expected_scope=gmv_health_expected_scope(board.case), now=now)
            if record and record.get('evidence_id') not in evidence_ids:
                evidence_ids.append(record.get('evidence_id'))
        if task.task_type == 'verify_gmv_signal' and evidence_ids:
            board.add_artifact(CaseArtifact(
                board.case.case_id, ARTIFACT_SIGNAL,
                payload=output.get('artifact') or {'summary': output.get('summary')},
                evidence_ids=evidence_ids, produced_by=task.task_id),
                expected_scope=gmv_health_expected_scope(board.case), now=now)
        if task.task_type == 'decompose_gmv_drivers' and evidence_ids:
            board.add_artifact(CaseArtifact(
                board.case.case_id, ARTIFACT_CONTRIBUTION,
                payload=output.get('artifact') or {'drivers': output.get('drivers') or []},
                evidence_ids=evidence_ids, produced_by=task.task_id),
                expected_scope=gmv_health_expected_scope(board.case), now=now)
            hypothesis = output.get('hypothesis')
            if isinstance(hypothesis, dict):
                data = dict(hypothesis)
                data.setdefault('case_id', board.case.case_id)
                data.setdefault('support_evidence_ids', evidence_ids)
                board.propose_hypothesis(Hypothesis.from_dict(data),
                                         expected_scope=gmv_health_expected_scope(board.case), now=now)
        return output

    def _task_output(self, result, task_id):
        result = _as_dict(result)
        if 'results' in result:
            task_result = _as_dict(_as_dict(result.get('results')).get(task_id))
            return _as_dict(task_result.get('output'))
        return _as_dict(result.get('output'))

    def _event(self, board, event_type, payload):
        return board.append_event(CaseEvent(board.case.case_id, event_type,
                                           payload=payload, source='case_orchestrator'),
                                  apply_state=False)


__all__ = ['CaseOrchestrator', 'ORCHESTRATOR_CONTRACT', 'EVENT_TASK_APPENDED',
           'EVENT_TASK_REJECTED', 'EVENT_TASK_COMPLETED', 'EVENT_TASK_FAILED',
           'EVENT_STOPPED']
