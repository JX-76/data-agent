# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'src')
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from multi_agent_contracts import AgentResult, RESULT_ERROR, RESULT_OK
from supervisor_runtime import SupervisorRuntime
from worker_registry import WorkerRegistry, WorkerSpec, build_default_worker_registry


def test_supervisor_runs_serial_dag_and_merges_terminal_output():
    runtime = SupervisorRuntime(max_steps=10, semaphore_limit=2)
    result = runtime.run([
        {'task_id': 'qa', 'worker_type': 'knowledge_qa', 'input': {'answer': '口径'}, 'idempotency_key': 'qa'},
        {'task_id': 'analysis', 'worker_type': 'data_analysis', 'input': {'metric': 'gmv'}, 'dependencies': ['qa'], 'idempotency_key': 'analysis'},
        {'task_id': 'merge', 'worker_type': 'merge', 'input': {'format': 'report'}, 'dependencies': ['analysis'], 'idempotency_key': 'merge'},
    ])
    assert result['status'] == 'ok'
    assert result['node_states']['qa'] == 'succeeded'
    assert result['node_states']['analysis'] == 'succeeded'
    assert result['node_states']['merge'] == 'succeeded'
    assert result['metrics']['trace_complete'] is True
    assert result['final_output']['terminal_count'] == 1


def test_supervisor_blocks_high_risk_non_safety_task():
    runtime = SupervisorRuntime(max_steps=5)
    result = runtime.run([
        {'task_id': 'write_action', 'worker_type': 'tool', 'input': {'action': 'delete'}, 'risk_level': 'high', 'idempotency_key': 'write_action'}
    ])
    assert result['status'] == 'pending_human_review'
    assert result['node_states']['write_action'] == 'blocked'
    assert result['results']['write_action']['output']['requires_human_review'] is True


def test_supervisor_rejects_invalid_dag_cycle():
    runtime = SupervisorRuntime(max_steps=5)
    result = runtime.run([
        {'task_id': 'a', 'worker_type': 'data_analysis', 'dependencies': ['b'], 'idempotency_key': 'a'},
        {'task_id': 'b', 'worker_type': 'knowledge_qa', 'dependencies': ['a'], 'idempotency_key': 'b'},
    ])
    assert result['status'] == 'error'
    assert any(e.get('error') == 'dag_cycle_detected' for e in result['errors'])


def test_supervisor_retries_then_succeeds():
    calls = {'n': 0}

    def flaky(task, dag_state=None):
        calls['n'] += 1
        if calls['n'] == 1:
            return AgentResult(task.task_id, status=RESULT_ERROR, errors=[{'error': 'temporary'}])
        return AgentResult(task.task_id, status=RESULT_OK, output={'ok': True})

    registry = build_default_worker_registry()
    registry.register(WorkerSpec('flaky', flaky))
    runtime = SupervisorRuntime(worker_registry=registry, retry_limit=1, max_steps=5)
    result = runtime.run([{'task_id': 'f1', 'worker_type': 'flaky', 'idempotency_key': 'f1'}])
    assert result['status'] == 'ok'
    assert calls['n'] == 2
    assert result['node_states']['f1'] == 'succeeded'
    assert any(e.get('event') == 'retry' for e in result['trace_events'])


def test_worker_registry_validates_required_inputs():
    registry = WorkerRegistry()
    registry.register(WorkerSpec('strict', lambda task, dag_state=None: AgentResult(task.task_id), input_schema={'required': ['x']}))
    runtime = SupervisorRuntime(worker_registry=registry, retry_limit=0)
    result = runtime.run([{'task_id': 's1', 'worker_type': 'strict', 'input': {}, 'idempotency_key': 's1'}])
    assert result['status'] == 'partial'
    assert result['node_states']['s1'] == 'failed'
    assert result['results']['s1']['errors'][0]['error'] == 'missing_required_input'


def test_worker_registry_rejects_unknown_result_status_without_ok_consumption():
    registry = WorkerRegistry()

    def bad_status(task, dag_state=None):
        return {'task_id': task.task_id, 'status': 'mystery', 'output': {'facts': [{'text': 'unsafe'}]}}

    registry.register(WorkerSpec('bad_status', bad_status))
    runtime = SupervisorRuntime(worker_registry=registry, retry_limit=0, max_steps=3)
    result = runtime.run([{'task_id': 'bad', 'worker_type': 'bad_status', 'idempotency_key': 'bad'}])

    assert result['status'] == 'partial'
    assert result['node_states']['bad'] == 'failed'
    assert result['results']['bad']['status'] == RESULT_ERROR
    assert result['results']['bad']['output']['authority'] == 'unverified'
    assert result['results']['bad']['errors'][0]['error'] == 'invalid_worker_result_status'
    assert result['results']['bad']['errors'][0]['status'] == 'mystery'


def test_invalid_worker_status_skips_downstream_nodes():
    registry = WorkerRegistry()
    registry.register(WorkerSpec('bad_status', lambda task, dag_state=None: {'status': 'mystery'}))
    registry.register(WorkerSpec('downstream', lambda task, dag_state=None: AgentResult(task.task_id, status=RESULT_OK)))
    runtime = SupervisorRuntime(worker_registry=registry, retry_limit=0, max_steps=5)

    result = runtime.run([
        {'task_id': 'bad', 'worker_type': 'bad_status', 'idempotency_key': 'bad'},
        {'task_id': 'child', 'worker_type': 'downstream', 'dependencies': ['bad'], 'idempotency_key': 'child'},
    ])

    assert result['status'] == 'partial'
    assert result['node_states']['bad'] == 'failed'
    assert result['node_states']['child'] == 'skipped'
    assert 'child' not in result['results']


def test_worker_registry_rejects_missing_status_dict_without_default_ok():
    registry = WorkerRegistry()
    registry.register(WorkerSpec('missing_status', lambda task, dag_state=None: {'output': {'facts': [{'text': 'unsafe'}]}}))
    runtime = SupervisorRuntime(worker_registry=registry, retry_limit=0, max_steps=3)

    result = runtime.run([{'task_id': 'missing', 'worker_type': 'missing_status', 'idempotency_key': 'missing'}])

    assert result['status'] == 'partial'
    assert result['node_states']['missing'] == 'failed'
    assert result['results']['missing']['status'] == RESULT_ERROR
    assert result['results']['missing']['output']['authority'] == 'unverified'
    assert result['results']['missing']['errors'][0]['error'] == 'missing_worker_result_status'


def test_worker_registry_rejects_none_payload_without_default_ok():
    registry = WorkerRegistry()
    registry.register(WorkerSpec('none_payload', lambda task, dag_state=None: None))
    runtime = SupervisorRuntime(worker_registry=registry, retry_limit=0, max_steps=3)

    result = runtime.run([{'task_id': 'none', 'worker_type': 'none_payload', 'idempotency_key': 'none'}])

    assert result['status'] == 'partial'
    assert result['node_states']['none'] == 'failed'
    assert result['results']['none']['status'] == RESULT_ERROR
    assert result['results']['none']['output']['authority'] == 'unverified'
    assert result['results']['none']['errors'][0]['error'] == 'missing_worker_result_status'


def test_worker_registry_rejects_ok_output_missing_required_schema_fields():
    registry = WorkerRegistry()
    registry.register(WorkerSpec(
        'schema_worker',
        lambda task, dag_state=None: AgentResult(task.task_id, status=RESULT_OK, output={'summary': 'partial'}),
        output_schema={'required': ['summary', 'evidence_ids']},
    ))
    runtime = SupervisorRuntime(worker_registry=registry, retry_limit=0, max_steps=3)

    result = runtime.run([{'task_id': 'schema', 'worker_type': 'schema_worker', 'idempotency_key': 'schema'}])

    assert result['status'] == 'partial'
    assert result['node_states']['schema'] == 'failed'
    assert result['results']['schema']['status'] == RESULT_ERROR
    assert result['results']['schema']['output']['authority'] == 'unverified'
    assert result['results']['schema']['errors'][0]['error'] == 'missing_required_output'
    assert result['results']['schema']['errors'][0]['fields'] == ['evidence_ids']


def test_worker_registry_allows_ok_output_when_required_schema_fields_present():
    registry = WorkerRegistry()
    registry.register(WorkerSpec(
        'schema_worker',
        lambda task, dag_state=None: AgentResult(task.task_id, status=RESULT_OK, output={'summary': 'ok', 'evidence_ids': ['ev1']}),
        output_schema={'required': ['summary', 'evidence_ids']},
    ))
    runtime = SupervisorRuntime(worker_registry=registry, retry_limit=0, max_steps=3)

    result = runtime.run([{'task_id': 'schema', 'worker_type': 'schema_worker', 'idempotency_key': 'schema'}])

    assert result['status'] == 'ok'
    assert result['node_states']['schema'] == 'succeeded'
    assert result['results']['schema']['status'] == RESULT_OK
    assert result['results']['schema']['output']['evidence_ids'] == ['ev1']


def test_worker_registry_rejects_ok_output_invalid_schema_type():
    registry = WorkerRegistry()
    registry.register(WorkerSpec(
        'typed_worker',
        lambda task, dag_state=None: AgentResult(task.task_id, status=RESULT_OK, output={'summary': ['not', 'string'], 'evidence_ids': 'ev1'}),
        output_schema={
            'required': ['summary', 'evidence_ids'],
            'properties': {
                'summary': {'type': 'string'},
                'evidence_ids': {'type': 'array'},
            },
        },
    ))
    runtime = SupervisorRuntime(worker_registry=registry, retry_limit=0, max_steps=3)

    result = runtime.run([{'task_id': 'typed', 'worker_type': 'typed_worker', 'idempotency_key': 'typed'}])

    assert result['status'] == 'partial'
    assert result['node_states']['typed'] == 'failed'
    assert result['results']['typed']['status'] == RESULT_ERROR
    assert result['results']['typed']['output']['authority'] == 'unverified'
    assert result['results']['typed']['errors'][0]['error'] == 'invalid_output_type'
    fields = result['results']['typed']['errors'][0]['fields']
    assert {'field': 'summary', 'expected': 'string'} in fields
    assert {'field': 'evidence_ids', 'expected': 'array'} in fields


def test_worker_registry_allows_ok_output_valid_schema_type():
    registry = WorkerRegistry()
    registry.register(WorkerSpec(
        'typed_worker',
        lambda task, dag_state=None: AgentResult(task.task_id, status=RESULT_OK, output={'summary': 'ok', 'evidence_ids': ['ev1'], 'score': 1.0}),
        output_schema={
            'required': ['summary', 'evidence_ids', 'score'],
            'properties': {
                'summary': {'type': 'string'},
                'evidence_ids': {'type': 'array'},
                'score': {'type': 'number'},
            },
        },
    ))
    runtime = SupervisorRuntime(worker_registry=registry, retry_limit=0, max_steps=3)

    result = runtime.run([{'task_id': 'typed', 'worker_type': 'typed_worker', 'idempotency_key': 'typed'}])

    assert result['status'] == 'ok'
    assert result['node_states']['typed'] == 'succeeded'
    assert result['results']['typed']['status'] == RESULT_OK
    assert result['results']['typed']['output']['score'] == 1.0


def test_worker_registry_rejects_unresolved_evidence_ids_when_required():
    registry = WorkerRegistry()
    registry.register(WorkerSpec(
        'evidence_worker',
        lambda task, dag_state=None: AgentResult(task.task_id, status=RESULT_OK, output={'evidence_ids': ['missing_ev']}),
        output_schema={
            'required': ['evidence_ids'],
            'properties': {'evidence_ids': {'type': 'array'}},
            'evidence_ids_must_resolve': True,
        },
    ))
    runtime = SupervisorRuntime(worker_registry=registry, retry_limit=0, max_steps=3)

    result = runtime.run([{'task_id': 'ev', 'worker_type': 'evidence_worker', 'idempotency_key': 'ev'}])

    assert result['status'] == 'partial'
    assert result['node_states']['ev'] == 'failed'
    assert result['results']['ev']['status'] == RESULT_ERROR
    assert result['results']['ev']['output']['authority'] == 'unverified'
    assert result['results']['ev']['errors'][0]['error'] == 'unresolved_evidence_ids'
    assert result['results']['ev']['errors'][0]['missing'] == ['missing_ev']


def test_worker_registry_allows_resolved_evidence_ids_from_execution_envelope():
    envelope = {
        'status': 'ok',
        'authority': 'verified_execution',
        'evidence_id': 'ev_ok',
        'query_id': 'q1',
        'dataid': 'orders',
        'data_version': 'v1',
        'row_count': 1,
    }
    registry = WorkerRegistry()
    registry.register(WorkerSpec(
        'evidence_worker',
        lambda task, dag_state=None: AgentResult(task.task_id, status=RESULT_OK, output={'evidence_ids': ['ev_ok'], 'execution_envelope': envelope}),
        output_schema={
            'required': ['evidence_ids', 'execution_envelope'],
            'properties': {
                'evidence_ids': {'type': 'array'},
                'execution_envelope': {'type': 'object'},
            },
            'evidence_ids_must_resolve': True,
        },
    ))
    runtime = SupervisorRuntime(worker_registry=registry, retry_limit=0, max_steps=3)

    result = runtime.run([{'task_id': 'ev', 'worker_type': 'evidence_worker', 'idempotency_key': 'ev'}])

    assert result['status'] == 'ok'
    assert result['node_states']['ev'] == 'succeeded'
    assert result['results']['ev']['status'] == RESULT_OK
    assert result['results']['ev']['output']['evidence_ids'] == ['ev_ok']
