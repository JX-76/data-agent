# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from trace_contracts import (build_replay_package, build_trace_envelope,
                             validate_trace_envelope, validate_replay_evidence_freshness)
from benchmark_scorer import score_case, score_suite


def test_trace_envelope_requires_replay_stages_for_ok_data_answer():
    result = {
        'status': 'ok',
        'trace_id': 't-1',
        'task_id': 'task-1',
        'facts': [{'text': 'GMV=100', 'evidence_ids': ['ev-1']}],
        'provenance': {'query_id': 'q-1', 'row_count': 1},
    }
    trace = [
        {'name': 'governance', 'trace_id': 't-1', 'task_id': 'task-1', 'status': 'ok'},
        {'name': 'plan', 'trace_id': 't-1', 'task_id': 'task-1', 'status': 'ok'},
        {'name': 'execution', 'trace_id': 't-1', 'task_id': 'task-1', 'status': 'ok', 'evidence_id': 'ev-1'},
        {'name': 'answer_audit', 'trace_id': 't-1', 'task_id': 'task-1', 'status': 'ok'},
        {'name': 'complete', 'trace_id': 't-1', 'task_id': 'task-1', 'status': 'ok'},
    ]
    envelope = build_trace_envelope(trace, result=result, case={'id': 'case-1'})
    assert envelope['contract'] == 'trace_envelope_v2'
    assert envelope['complete'] is True
    assert envelope['stage_order'] == ['precheck', 'plan', 'execute', 'answer_audit', 'complete']
    assert validate_trace_envelope(envelope)['valid'] is True


def test_trace_envelope_blocks_missing_execute_for_data_answer():
    result = {
        'status': 'ok',
        'trace_id': 't-2',
        'facts': [{'text': 'GMV=100', 'evidence_ids': ['ev-1']}],
    }
    trace = [
        {'name': 'governance', 'trace_id': 't-2', 'status': 'ok'},
        {'name': 'plan', 'trace_id': 't-2', 'status': 'ok'},
        {'name': 'answer_audit', 'trace_id': 't-2', 'status': 'ok'},
        {'name': 'complete', 'trace_id': 't-2', 'status': 'ok'},
    ]
    envelope = build_trace_envelope(trace, result=result, case={'id': 'case-2'})
    validation = validate_trace_envelope(envelope)
    assert envelope['complete'] is False
    assert 'execute' in envelope['missing_required_stages']
    assert 'missing_stage:execute' in validation['errors']


def test_trace_envelope_rejects_execute_after_terminal_blocked():
    result = {'status': 'blocked', 'trace_id': 't-3'}
    trace = [
        {'name': 'governance', 'trace_id': 't-3', 'status': 'blocked'},
        {'name': 'execution', 'trace_id': 't-3', 'status': 'ok'},
        {'name': 'complete', 'trace_id': 't-3', 'status': 'blocked'},
    ]
    envelope = build_trace_envelope(trace, result=result, case={'id': 'case-3'})
    validation = validate_trace_envelope(envelope)
    assert envelope['unexpected_execute'] is True
    assert 'unexpected_execute_for_terminal_status' in validation['errors']


def test_replay_package_and_scorer_surface_trace_contract_violation():
    case = {'id': 'case-4', 'expected': {'status': 'ok'}}
    result = {'status': 'ok', 'trace_id': 't-4', 'facts': [{'text': 'x', 'evidence_ids': ['ev']}]} 
    evaluated = {
        'id': 'case-4',
        'case': case,
        'expected': case['expected'],
        'result': result,
        'trace': [{'name': 'governance', 'trace_id': 't-4', 'status': 'ok'}],
        'passed': False,
        'failure_type': 'trace_contract_violation',
    }
    replay = build_replay_package(case, evaluated)
    assert replay['contract'] == 'trace_replay_v2'
    assert replay['trace_validation']['valid'] is False
    evaluated['trace_envelope'] = replay['trace_envelope']
    evaluated['trace_validation'] = replay['trace_validation']
    scored = score_case(case, evaluated)
    assert scored['trace_contract_validity'] == 0
    assert scored['semantic_failure_type'] == 'trace_error'
    assert scored['failure_stage'] == 'trace'
    suite = score_suite([evaluated])
    assert suite['trace_contract_validity'] == 0.0


def _evidence_backed_result(recorded_at):
    record = {
        'status': 'ok', 'stage': 'db_execute', 'query_id': 'q-fresh',
        'evidence_id': 'ev-fresh', 'dataid': 'orders', 'data_version': 'v1',
        'row_count': 1, 'time_range': 'last_7_days',
        'authority': 'verified_execution', 'recorded_at': recorded_at,
        'metric': 'gmv', 'dimensions': ['channel'], 'filters': {},
        'metadata': {'metric': 'gmv', 'dimensions': ['channel'], 'filters': {}},
    }
    final_answer = {
        'status': 'ok', 'answer_type': 'analysis',
        'facts': [{'text': 'GMV is 100', 'evidence_ids': ['ev-fresh']}],
        'hypotheses': [], 'limitations': [], 'evidence_ids': ['ev-fresh'],
    }
    return {
        'status': 'ok', 'final_answer': final_answer,
        'provenance': {'evidence_bus': {'contract': 'evidence_bus_v1', 'records': [record]}},
        'claim_graduation': {
            'expected_scope': {'metric': 'gmv', 'allowed_time_ranges': ['last_7_days'],
                               'dataid': 'orders', 'data_version': 'v1',
                               'dimensions': ['channel'], 'filters': {}},
            'ttl_seconds': 10,
        },
    }


def test_replay_freshness_validation_flags_expired_final_evidence():
    validation = validate_replay_evidence_freshness(_evidence_backed_result(10.0), now=21.0)

    assert validation['audited'] is True
    assert validation['valid'] is False
    assert validation['errors'] == ['evidence_ttl_expired:ev-fresh']
    assert validation['audited_status'] == 'no_answer'


def test_replay_package_includes_freshness_validation_for_evidence_result():
    result = _evidence_backed_result(10.0)
    evaluated = {'id': 'case-fresh', 'result': result, 'replay_now': 15.0, 'trace': []}

    replay = build_replay_package({'id': 'case-fresh'}, evaluated)

    assert replay['evidence_freshness_validation']['contract'] == 'replay_evidence_freshness_v1'
    assert replay['evidence_freshness_validation']['valid'] is True
