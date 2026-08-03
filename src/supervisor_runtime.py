# -*- coding: utf-8 -*-
"""Controlled Supervisor DAG runtime for local multi-agent execution.

This is intentionally deterministic: no free-form agent negotiation, no hidden
worker-to-worker calls. Supervisor owns validation, scheduling, budgets,
termination, trace, and merge.
"""
from __future__ import unicode_literals

import time
import uuid

from multi_agent_contracts import (
    AgentTask, AgentObservation, AgentResult,
    STATUS_PENDING, STATUS_READY, STATUS_RUNNING, STATUS_SUCCEEDED,
    STATUS_FAILED, STATUS_SKIPPED, STATUS_BLOCKED,
    RESULT_OK, RESULT_ERROR, RESULT_BLOCKED, RESULT_PARTIAL,
    RESULT_PENDING_HUMAN_REVIEW,
)
from worker_registry import build_default_worker_registry


TERMINAL_NODE_STATES = (STATUS_SUCCEEDED, STATUS_FAILED, STATUS_SKIPPED, STATUS_BLOCKED)


def _new_id():
    return str(uuid.uuid4())


class SupervisorRuntimeResult(object):
    def __init__(self, status='ok', results=None, trace_events=None, node_states=None,
                 errors=None, metrics=None, final_output=None):
        self.status = status
        self.results = results or {}
        self.trace_events = trace_events or []
        self.node_states = node_states or {}
        self.errors = errors or []
        self.metrics = metrics or {}
        self.final_output = final_output or {}

    def to_dict(self):
        return {
            'status': self.status,
            'results': dict(self.results),
            'trace_events': list(self.trace_events),
            'node_states': dict(self.node_states),
            'errors': list(self.errors),
            'metrics': dict(self.metrics),
            'final_output': dict(self.final_output),
        }


class SupervisorRuntime(object):
    def __init__(self, worker_registry=None, observer=None, max_nodes=20,
                 max_steps=50, semaphore_limit=3, retry_limit=1):
        self.worker_registry = worker_registry or build_default_worker_registry()
        self.observer = observer
        self.max_nodes = int(max_nodes or 20)
        self.max_steps = int(max_steps or 50)
        self.semaphore_limit = int(semaphore_limit or 3)
        self.retry_limit = int(retry_limit if retry_limit is not None else 1)

    def run(self, tasks, trace_id=None, session_id=None):
        trace_id = trace_id or _new_id()
        t0 = time.time()
        try:
            parsed = [AgentTask.from_dict(t) if not isinstance(t, AgentTask) else t for t in (tasks or [])]
        except Exception as exc:
            return SupervisorRuntimeResult(status=RESULT_ERROR, errors=[{
                'error': 'invalid_task_payload', 'message': str(exc)}],
                metrics={'duration_ms': int((time.time() - t0) * 1000)}).to_dict()
        errors = self._validate_graph(parsed)
        if errors:
            return SupervisorRuntimeResult(status=RESULT_ERROR, errors=errors,
                                           metrics={'duration_ms': int((time.time() - t0) * 1000)}).to_dict()

        state = {
            'trace_id': trace_id,
            'session_id': session_id,
            'tasks': dict((t.task_id, t) for t in parsed),
            'results': {},
            'node_states': dict((t.task_id, STATUS_PENDING) for t in parsed),
            'trace_events': [],
            'attempts': {},
        }

        step = 0
        while not self._all_terminal(state):
            step += 1
            if step > self.max_steps:
                errors.append({'error': 'max_steps_exceeded', 'max_steps': self.max_steps})
                break
            ready = self._ready_tasks(parsed, state)[:self.semaphore_limit]
            if not ready:
                if self._all_terminal(state):
                    break
                errors.append({'error': 'dag_stalled', 'node_states': dict(state['node_states'])})
                break
            for task in ready:
                self._run_one(task, state)

        final_status = self._final_status(state, errors)
        final_output = self._build_final_output(parsed, state)
        metrics = {
            'duration_ms': int((time.time() - t0) * 1000),
            'node_count': len(parsed),
            'steps': step,
            'semaphore_limit': self.semaphore_limit,
            'trace_complete': self._trace_complete(parsed, state),
        }
        return SupervisorRuntimeResult(
            status=final_status,
            results=dict(state['results']),
            trace_events=list(state['trace_events']),
            node_states=dict(state['node_states']),
            errors=errors,
            metrics=metrics,
            final_output=final_output,
        ).to_dict()

    def _validate_graph(self, tasks):
        errors = []
        if len(tasks) > self.max_nodes:
            errors.append({'error': 'max_nodes_exceeded', 'max_nodes': self.max_nodes, 'actual': len(tasks)})
        ids = [t.task_id for t in tasks]
        if len(ids) != len(set(ids)):
            errors.append({'error': 'duplicate_task_id'})
        idset = set(ids)
        seen_keys = {}
        for task in tasks:
            if not task.worker_type:
                errors.append({'error': 'missing_worker_type', 'task_id': task.task_id})
            for dep in task.dependencies:
                if dep not in idset:
                    errors.append({'error': 'unknown_dependency', 'task_id': task.task_id, 'dependency': dep})
            seen_keys[task.idempotency_key] = seen_keys.get(task.idempotency_key, 0) + 1
        for key, count in seen_keys.items():
            if key and count > 1:
                errors.append({'error': 'duplicate_idempotency_key', 'idempotency_key': key, 'count': count})
        if self._has_cycle(tasks):
            errors.append({'error': 'dag_cycle_detected'})
        return errors

    def _has_cycle(self, tasks):
        graph = dict((t.task_id, list(t.dependencies)) for t in tasks)
        visiting = set()
        visited = set()

        def visit(node):
            if node in visiting:
                return True
            if node in visited:
                return False
            visiting.add(node)
            for dep in graph.get(node, []):
                if visit(dep):
                    return True
            visiting.remove(node)
            visited.add(node)
            return False

        for task in tasks:
            if visit(task.task_id):
                return True
        return False

    def _all_terminal(self, state):
        return all(v in TERMINAL_NODE_STATES for v in state['node_states'].values())

    def _ready_tasks(self, tasks, state):
        ready = []
        for task in tasks:
            if state['node_states'].get(task.task_id) != STATUS_PENDING:
                continue
            deps = [state['node_states'].get(d) for d in task.dependencies]
            if all(s == STATUS_SUCCEEDED for s in deps):
                ready.append(task)
            elif any(s in (STATUS_FAILED, STATUS_BLOCKED, STATUS_SKIPPED) for s in deps):
                state['node_states'][task.task_id] = STATUS_SKIPPED
                self._trace(state, task.task_id, 'skip', STATUS_SKIPPED, 'dependency_not_succeeded')
        for task in ready:
            state['node_states'][task.task_id] = STATUS_READY
        return ready

    def _run_one(self, task, state):
        state['node_states'][task.task_id] = STATUS_RUNNING
        self._trace(state, task.task_id, 'node_start', STATUS_RUNNING, task.worker_type)
        attempts = state['attempts'].get(task.task_id, 0) + 1
        state['attempts'][task.task_id] = attempts

        if task.risk_level in ('high', 'critical') and task.worker_type != 'safety':
            result = AgentResult(task.task_id, status=RESULT_PENDING_HUMAN_REVIEW,
                                 output={'requires_human_review': True, 'risk_level': task.risk_level},
                                 errors=[])
            state['results'][task.task_id] = result.to_dict()
            state['node_states'][task.task_id] = STATUS_BLOCKED
            self._trace(state, task.task_id, 'human_gate', STATUS_BLOCKED, 'pending_human_review')
            return

        result = self.worker_registry.run(task, dag_state=state)
        if result.status == RESULT_OK:
            state['node_states'][task.task_id] = STATUS_SUCCEEDED
            state['results'][task.task_id] = result.to_dict()
            self._trace(state, task.task_id, 'node_finish', STATUS_SUCCEEDED, 'ok')
            return

        if result.status == RESULT_PENDING_HUMAN_REVIEW or result.status == RESULT_BLOCKED:
            state['node_states'][task.task_id] = STATUS_BLOCKED
            state['results'][task.task_id] = result.to_dict()
            self._trace(state, task.task_id, 'node_blocked', STATUS_BLOCKED, result.status)
            return

        if attempts <= self.retry_limit:
            state['node_states'][task.task_id] = STATUS_PENDING
            self._trace(state, task.task_id, 'retry', STATUS_PENDING, 'attempt_%s' % attempts)
            return

        state['node_states'][task.task_id] = STATUS_FAILED
        state['results'][task.task_id] = result.to_dict()
        self._trace(state, task.task_id, 'node_failed', STATUS_FAILED, 'retry_exhausted')

    def _trace(self, state, node_id, event, status, summary):
        obs = AgentObservation(node_id, event, status=status, summary=summary,
                               payload_ref='trace://%s/%s/%s' % (state.get('trace_id'), node_id, event))
        data = obs.to_dict()
        state['trace_events'].append(data)
        if self.observer is not None:
            try:
                self.observer.record('multi_agent_%s' % event, trace_id=state.get('trace_id'),
                                     task_id=node_id, session_id=state.get('session_id'),
                                     status=status, metadata=data)
            except Exception:
                pass
        return data

    def _final_status(self, state, errors):
        if errors:
            return RESULT_ERROR
        values = state['node_states'].values()
        if any(v == STATUS_BLOCKED for v in values):
            return RESULT_PENDING_HUMAN_REVIEW
        if any(v == STATUS_FAILED for v in values):
            return RESULT_PARTIAL
        if any(v == STATUS_SKIPPED for v in values):
            return RESULT_PARTIAL
        return RESULT_OK

    def _build_final_output(self, tasks, state):
        if not tasks:
            return {}
        terminal = []
        task_ids = set([t.task_id for t in tasks])
        depended = set()
        for t in tasks:
            depended.update(t.dependencies)
        for task_id in task_ids:
            if task_id not in depended and task_id in state['results']:
                terminal.append(state['results'][task_id])
        if not terminal:
            terminal = [state['results'][t.task_id] for t in tasks if t.task_id in state['results']]
        return {'terminal_results': terminal, 'terminal_count': len(terminal)}

    def _trace_complete(self, tasks, state):
        events = state.get('trace_events') or []
        by_node = {}
        for event in events:
            by_node.setdefault(event.get('node_id'), set()).add(event.get('event'))
        for task in tasks:
            node_events = by_node.get(task.task_id, set())
            if state['node_states'].get(task.task_id) == STATUS_SKIPPED:
                if 'skip' not in node_events:
                    return False
            elif 'node_start' not in node_events:
                return False
        return True


__all__ = ['SupervisorRuntime', 'SupervisorRuntimeResult']
