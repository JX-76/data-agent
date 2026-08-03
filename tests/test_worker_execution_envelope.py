# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'src')
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from contracts import build_execution_envelope
from multi_agent_contracts import AgentResult, RESULT_OK, RESULT_ERROR
from supervisor_runtime import SupervisorRuntime
from worker_registry import WorkerRegistry, WorkerSpec


def _run_worker(output):
    registry = WorkerRegistry()
    registry.register(WorkerSpec(
        'envelope_worker',
        lambda task, dag_state=None: AgentResult(task.task_id, status=RESULT_OK, output=output),
        output_schema={'required': ['execution_envelope']},
    ))
    runtime = SupervisorRuntime(worker_registry=registry, retry_limit=0, max_steps=3)
    return runtime.run([{'task_id': 'env', 'worker_type': 'envelope_worker', 'idempotency_key': 'env'}])


def test_worker_registry_rejects_ok_result_with_error_execution_envelope():
    result = _run_worker({'execution_envelope': build_execution_envelope(
        status='error', stage='external_tool', error_code='timeout', authority='unverified')})

    assert result['status'] == 'partial'
    assert result['node_states']['env'] == 'failed'
    assert result['results']['env']['status'] == RESULT_ERROR
    assert result['results']['env']['output']['authority'] == 'unverified'
    assert result['results']['env']['errors'][0]['error'] == 'invalid_execution_envelope'
    assert result['results']['env']['errors'][0]['reason'] == 'non_ok_envelope_status'


def test_worker_registry_rejects_verified_authority_without_evidence_id():
    result = _run_worker({'execution_envelope': build_execution_envelope(
        status='ok', stage='db_execute', query_id='q1', evidence_id=None,
        authority='verified_execution')})

    assert result['status'] == 'partial'
    assert result['results']['env']['status'] == RESULT_ERROR
    assert result['results']['env']['errors'][0]['reason'] == 'missing_evidence_id'


def test_worker_registry_accepts_valid_verified_execution_envelope():
    result = _run_worker({'execution_envelope': build_execution_envelope(
        status='ok', stage='db_execute', query_id='q1', evidence_id='ev1',
        dataid='orders', data_version='v1', row_count=1,
        authority='verified_execution')})

    assert result['status'] == 'ok'
    assert result['node_states']['env'] == 'succeeded'
    assert result['results']['env']['status'] == RESULT_OK
    assert result['results']['env']['output']['execution_envelope']['evidence_id'] == 'ev1'
