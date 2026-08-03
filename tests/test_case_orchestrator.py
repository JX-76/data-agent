# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'src')
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from case_blackboard import CaseBlackboard
from case_orchestrator import CaseOrchestrator
from contracts import build_execution_envelope
from dynamic_task_planner import DynamicTaskPlanner
from gmv_health_playbook import build_gmv_health_case
from durable_task_control_plane import DurableTaskControlPlane


def _executor(agent_task, board):
    task_type = agent_task.metadata['dynamic_task_type']
    output = {'summary': task_type}
    if task_type in ('verify_gmv_signal', 'decompose_gmv_drivers'):
        evidence_id = 'ev_%s' % task_type
        output.update({
            'evidence_ids': [evidence_id],
            'execution_envelope': build_execution_envelope(
                status='ok', stage='db_execute', query_id='q_%s' % task_type,
                evidence_id=evidence_id, dataid='orders', data_version='v1',
                row_count=1, time_range='last_7_days', authority='verified_execution',
                metadata={'metric': 'gmv', 'dimensions': ['channel'], 'filters': {}}),
        })
    if task_type == 'decompose_gmv_drivers':
        output['drivers'] = [{'dimension': 'channel', 'value': 'ads', 'contribution': -20}]
        output['hypothesis'] = {'statement': 'ads channel may explain the change'}
    return {'status': 'ok', 'output': output}


def test_case_orchestrator_runs_evidence_gated_append_loop_and_writes_timeline():
    case = build_gmv_health_case(data_version='v1')
    board = CaseBlackboard(case)
    result = CaseOrchestrator(task_executor=_executor, max_rounds=6).run_case_loop(
        board, trace_id='trace-case', now=100.0)

    assert result['status'] == 'ok'
    assert result['executed_task_types'] == [
        'verify_gmv_signal', 'decompose_gmv_drivers', 'challenge_root_cause']
    assert [task['task_type'] for task in result['appended_tasks']] == result['executed_task_types']
    assert result['stop_reason'] == 'no_new_information_gain'
    context = result['case_context']
    assert context['evidence_view']['accepted_evidence_ids'] == [
        'ev_verify_gmv_signal', 'ev_decompose_gmv_drivers']
    assert len(context['artifacts']) == 2
    event_types = [event['event_type'] for event in context['events']]
    assert event_types.count('orchestrator.task_appended') == 3
    assert event_types.count('orchestrator.task_completed') == 3
    assert 'planner.append_task' in event_types
    assert 'planner.stop' in event_types
    assert 'orchestrator.stopped' in event_types


def test_case_orchestrator_stops_after_failed_signal_without_driver_append():
    case = build_gmv_health_case(data_version='v1')
    board = CaseBlackboard(case)

    def fail_signal(agent_task, ignored_board):
        return {'status': 'error', 'errors': [{'error': 'db_timeout'}]}

    result = CaseOrchestrator(task_executor=fail_signal).run_case_loop(board, now=100.0)

    assert result['status'] == 'error'
    assert result['stop_reason'] == 'task_execution_failed'
    assert [task['task_type'] for task in result['appended_tasks']] == ['verify_gmv_signal']
    assert result['executed_task_types'] == []
    assert any(event['event_type'] == 'orchestrator.task_failed'
               for event in result['case_context']['events'])


def test_case_orchestrator_respects_case_budget_before_downstream_append():
    case = build_gmv_health_case(data_version='v1')
    case.budget['max_dynamic_tasks'] = 1
    case.mission.budget['max_dynamic_tasks'] = 1
    board = CaseBlackboard(case)

    result = CaseOrchestrator(task_executor=_executor).run_case_loop(board, now=100.0)

    assert result['status'] == 'ok'
    assert result['executed_task_types'] == ['verify_gmv_signal']
    assert result['stop_reason'] == 'budget_exhausted'
    assert [task['task_type'] for task in result['appended_tasks']] == ['verify_gmv_signal']


def test_case_orchestrator_stops_when_worker_capability_is_not_available():
    case = build_gmv_health_case(data_version='v1')
    board = CaseBlackboard(case)
    planner = DynamicTaskPlanner(worker_capabilities={'data_analyst': []})

    result = CaseOrchestrator(planner=planner, task_executor=_executor).run_case_loop(board, now=100.0)

    assert result['status'] == 'ok'
    assert result['appended_tasks'] == []
    assert result['stop_reason'] == 'worker_capability_missing'
    assert result['runtime_results'] == []


def test_case_orchestrator_writes_durable_task_state_without_breaking_sync_executor():
    case = build_gmv_health_case(data_version='v1')
    board = CaseBlackboard(case)
    control = DurableTaskControlPlane(clock=lambda: 100.0)

    result = CaseOrchestrator(task_executor=_executor, durable_task_control_plane=control,
                              max_rounds=2).run_case_loop(board, trace_id='trace-durable',
                                                           session_id='session-durable', now=100.0)

    assert result['status'] == 'ok'
    records = control.repository.list(case_id=case.case_id)
    assert [record.state for record in records] == ['succeeded', 'succeeded']
    assert records[0].trace_id == 'trace-durable'
    assert result['runtime_results'][0]['durable_task']['contract'] == 'durable_task_status_v1'
    assert result['runtime_results'][0]['durable_task']['state'] == 'succeeded'


def test_case_orchestrator_durable_failure_does_not_advance_downstream_tasks():
    case = build_gmv_health_case(data_version='v1')
    board = CaseBlackboard(case)
    control = DurableTaskControlPlane(clock=lambda: 100.0)

    def fail_signal(agent_task, ignored_board):
        return {'status': 'error', 'errors': [{'error': 'schema_invalid', 'message': 'bad input'}]}

    result = CaseOrchestrator(task_executor=fail_signal, durable_task_control_plane=control).run_case_loop(
        board, trace_id='trace-durable-fail', now=100.0)

    assert result['status'] == 'error'
    assert result['executed_task_types'] == []
    records = control.repository.list(case_id=case.case_id)
    assert len(records) == 1
    assert records[0].state == 'failed'
    assert records[0].safe_error_summary == 'bad input'


def test_case_orchestrator_does_not_duplicate_an_already_executed_task_type():
    case = build_gmv_health_case(data_version='v1')
    board = CaseBlackboard(case)

    result = CaseOrchestrator(task_executor=_executor).run_case_loop(
        board, executed_task_types=['verify_gmv_signal'], now=100.0)

    assert result['appended_tasks'] == []
    assert result['stop_reason'] == 'verified_signal_required'
    assert result['executed_task_types'] == ['verify_gmv_signal']
