# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'src')
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from multi_agent_contracts import (
    A2AEnvelope, A2AMessage, A2A_MESSAGE_REQUEST, A2A_MESSAGE_RESULT,
    RESULT_OK, RESULT_ERROR, RESULT_BLOCKED, RESULT_NEED_CLARIFICATION,
    WORKER_DATA_ANALYST, WORKER_DIAGNOSIS, WORKER_AUDITOR,
    AgentTask, AgentResult, validate_a2a_message, validate_a2a_request,
    validate_a2a_result,
)
from worker_registry import build_default_worker_registry


def _valid_request():
    return {
        'trace_id': 'trace-1',
        'task_id': 'task-1',
        'from_agent': 'orchestrator',
        'to_agent': WORKER_DATA_ANALYST,
        'message_type': A2A_MESSAGE_REQUEST,
        'constraints': {'max_steps': 2},
        'evidence_context': {'required': True},
        'expected_schema': {'type': 'execution_envelope'},
        'reply_to': 'orchestrator',
        'payload': {'metric': 'gmv'},
    }


def test_a2a_request_round_trip_and_validation_passes():
    envelope = A2AEnvelope.from_dict(_valid_request())
    data = envelope.to_dict()
    assert data['trace_id'] == 'trace-1'
    assert data['to_agent'] == WORKER_DATA_ANALYST
    assert data['payload']['metric'] == 'gmv'
    assert validate_a2a_request(data) == []
    assert validate_a2a_message(data) == []


def test_a2a_result_round_trip_and_validation_passes():
    data = _valid_request()
    data['message_type'] = A2A_MESSAGE_RESULT
    data['from_agent'] = WORKER_DATA_ANALYST
    data['to_agent'] = 'orchestrator'
    data['status'] = RESULT_OK
    data['payload'] = {'output': {'evidence_id': 'ev-1'}}
    result = A2AMessage.from_dict(data).to_dict()
    assert result['status'] == RESULT_OK
    assert validate_a2a_result(result) == []
    assert validate_a2a_message(result) == []


def test_a2a_validator_reports_missing_required_fields_without_exception():
    errors = validate_a2a_request({'message_type': A2A_MESSAGE_REQUEST, 'payload': {'x': 1}})
    fields = set([e.get('field') for e in errors])
    assert 'trace_id' in fields
    assert 'task_id' in fields
    assert 'from_agent' in fields
    assert 'to_agent' in fields
    assert 'expected_schema' in fields


def test_a2a_validator_rejects_invalid_status():
    data = _valid_request()
    data['message_type'] = A2A_MESSAGE_RESULT
    data['status'] = 'mystery'
    errors = validate_a2a_result(data)
    assert any(e.get('field') == 'status' and e.get('error') == 'invalid_status' for e in errors)


def test_legacy_agent_task_from_dict_and_to_dict_still_work():
    task = AgentTask.from_dict({
        'task_id': 'legacy-task',
        'worker_type': 'data_analysis',
        'input': {'metric': 'gmv'},
        'dependencies': ['dep-1'],
    })
    data = task.to_dict()
    assert data['task_id'] == 'legacy-task'
    assert data['worker_type'] == 'data_analysis'
    assert data['input']['metric'] == 'gmv'
    assert data['dependencies'] == ['dep-1']


def test_agent_task_can_be_adapted_from_a2a_like_payload():
    task = AgentTask.from_dict({
        'task_id': 'a2a-task',
        'to_agent': WORKER_DATA_ANALYST,
        'payload': {'input': {'metric': 'gmv'}, 'intent': 'metric_query'},
    })
    assert task.task_id == 'a2a-task'
    assert task.worker_type == WORKER_DATA_ANALYST
    assert task.input['metric'] == 'gmv'
    assert task.intent == 'metric_query'


def test_default_registry_contains_new_worker_types_and_executes_echo():
    registry = build_default_worker_registry()
    worker_types = set([w['worker_type'] for w in registry.list_workers()])
    assert WORKER_DATA_ANALYST in worker_types
    assert WORKER_DIAGNOSIS in worker_types
    assert WORKER_AUDITOR in worker_types

    task = AgentTask(WORKER_DATA_ANALYST, task_input={'metric': 'gmv'}, task_id='worker-1')
    result = registry.run(task)
    assert result.status == RESULT_OK
    assert result.output['metric'] == 'gmv'


def test_non_ok_results_are_not_equal_to_ok():
    assert AgentResult('t1', status=RESULT_ERROR).status != RESULT_OK
    assert AgentResult('t2', status=RESULT_BLOCKED).status != RESULT_OK
    assert AgentResult('t3', status=RESULT_NEED_CLARIFICATION).status != RESULT_OK
