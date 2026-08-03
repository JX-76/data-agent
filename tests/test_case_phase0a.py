# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'src')
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from contracts import build_execution_envelope
from case_blackboard import CaseBlackboard
from case_contracts import (
    BusinessCase, CaseArtifact, Hypothesis, ActionCard, DynamicTaskSpec,
    ARTIFACT_SIGNAL, CASE_SCOPING, CASE_SIGNAL_CONFIRMED, CASE_HYPOTHESIS_PENDING,
    CASE_ACTION_DRAFTED, EVENT_SIGNAL_DETECTED, EVENT_ACTION_DRAFTED,
    validate_contract_payload, CASE_CONTRACT_VERSION,
)
from case_state_machine import CaseStateMachine
from gmv_health_playbook import build_gmv_health_case, build_gmv_health_dynamic_tasks, gmv_health_expected_scope


def _verified_envelope(evidence_id='ev_gmv', metric='gmv', dimensions=None, filters=None,
                       dataid='orders', data_version='v1', time_range='last_7_days'):
    return build_execution_envelope(
        status='ok', stage='db_execute', query_id='q_gmv', evidence_id=evidence_id,
        dataid=dataid, data_version=data_version, row_count=3, time_range=time_range,
        authority='verified_execution', metadata={
            'metric': metric,
            'dimensions': dimensions or ['channel'],
            'filters': filters or {'region': 'cn'},
            'tenant_id': 'tenant_a',
            'user_id': 'user_a',
            'permission_scope': {'regions': ['cn'], 'role': 'analyst'},
        })


def test_business_case_contract_roundtrip_and_validation():
    case = build_gmv_health_case(filters={'region': 'cn'}, data_version='v1',
                                 tenant_id='tenant_a', user_id='user_a',
                                 permission_scope={'regions': ['cn'], 'role': 'analyst'})
    payload = case.to_dict()

    assert payload['contract'] == CASE_CONTRACT_VERSION
    assert payload['scenario'] == 'gmv_health'
    assert payload['business_scope']['metric'] == 'gmv'
    assert validate_contract_payload(payload, CASE_CONTRACT_VERSION)['ok'] is True

    restored = BusinessCase.from_dict(payload).to_dict()
    assert restored['case_id'] == payload['case_id']
    assert restored['mission']['policy']['require_verified_evidence_for_actions'] is True


def test_case_state_machine_rejects_invalid_transition_and_applies_signal():
    case = BusinessCase(status=CASE_SCOPING)
    machine = CaseStateMachine()

    invalid = machine.apply(case, {'case_id': case.case_id, 'event_type': EVENT_ACTION_DRAFTED})
    assert invalid.ok is False
    assert invalid.error == 'invalid_case_transition'
    assert case.status == CASE_SCOPING

    valid = machine.apply(case, {'case_id': case.case_id, 'event_type': EVENT_SIGNAL_DETECTED})
    assert valid.ok is True
    assert case.status == CASE_SIGNAL_CONFIRMED
    assert case.timeline[-1]['event_type'] == EVENT_SIGNAL_DETECTED


def test_case_blackboard_records_only_verified_evidence_and_gates_artifacts_by_scope():
    case = build_gmv_health_case(filters={'region': 'cn'}, data_version='v1',
                                 tenant_id='tenant_a', user_id='user_a',
                                 permission_scope={'regions': ['cn'], 'role': 'analyst'})
    board = CaseBlackboard(case)
    assert board.case.status == CASE_SCOPING

    failed = build_execution_envelope(status='error', stage='db_execute', evidence_id='ev_bad',
                                      dataid='orders', data_version='v1', row_count=0,
                                      authority='unverified')
    assert board.record_execution_envelope(failed) is None

    record = board.record_execution_envelope(_verified_envelope(), producer_task_id='data_analyst', trace_id='trace-1')
    assert record['evidence_id'] == 'ev_gmv'

    expected_scope = gmv_health_expected_scope(case)
    signal = CaseArtifact(case.case_id, ARTIFACT_SIGNAL,
                          payload={'summary': 'GMV signal verified'}, evidence_ids=['ev_gmv'],
                          produced_by='data_analyst')
    added = board.add_artifact(signal, expected_scope=expected_scope)
    assert added['ok'] is True
    assert added['case_status'] == CASE_SIGNAL_CONFIRMED

    wrong_scope = dict(expected_scope)
    wrong_scope['metric'] = 'orders'
    rejected = board.add_artifact(CaseArtifact(case.case_id, ARTIFACT_SIGNAL,
                                               payload={'summary': 'wrong metric'}, evidence_ids=['ev_gmv']),
                                  expected_scope=wrong_scope)
    assert rejected['ok'] is False
    assert rejected['error'] == 'invalid_evidence_refs'
    assert rejected['rejected'][0]['fields'] == ['metric']


def test_hypothesis_and_action_require_verified_evidence_refs():
    case = build_gmv_health_case(filters={'region': 'cn'}, data_version='v1',
                                 tenant_id='tenant_a', user_id='user_a',
                                 permission_scope={'regions': ['cn'], 'role': 'analyst'})
    board = CaseBlackboard(case)
    board.record_execution_envelope(_verified_envelope())
    expected_scope = gmv_health_expected_scope(case)
    board.add_artifact(CaseArtifact(case.case_id, ARTIFACT_SIGNAL,
                                    payload={'summary': 'GMV signal verified'}, evidence_ids=['ev_gmv']),
                       expected_scope=expected_scope)

    hyp = Hypothesis(case.case_id, 'GMV decline is concentrated in paid channel',
                     support_evidence_ids=['ev_gmv'], confidence=0.4)
    proposed = board.propose_hypothesis(hyp, expected_scope=expected_scope)
    assert proposed['ok'] is True
    assert proposed['case_status'] == CASE_HYPOTHESIS_PENDING

    no_evidence_action = ActionCard(case.case_id, 'budget_shift', proposal={'move_budget': 'paid to organic'})
    rejected = board.draft_action(no_evidence_action, expected_scope=expected_scope)
    assert rejected == {'ok': False, 'error': 'missing_verified_evidence'}

    action = ActionCard(case.case_id, 'investigate_channel', proposal={'owner': 'growth_ops'}, evidence_ids=['ev_gmv'])
    drafted = board.draft_action(action, expected_scope=expected_scope)
    assert drafted['ok'] is True
    assert drafted['case_status'] == CASE_ACTION_DRAFTED
    assert drafted['action']['approval_required'] is True


def test_gmv_health_playbook_dynamic_tasks_are_typed_and_scoped():
    case = build_gmv_health_case(filters={'region': 'cn'}, data_version='v1')
    tasks = build_gmv_health_dynamic_tasks(case)

    assert len(tasks) == 3
    assert all(isinstance(task, DynamicTaskSpec) for task in tasks)
    payloads = [task.to_dict() for task in tasks]
    assert [p['task_type'] for p in payloads] == [
        'verify_gmv_signal', 'decompose_gmv_drivers', 'challenge_root_cause']
    assert payloads[0]['worker_type'] == 'data_analyst'
    assert payloads[0]['metadata']['case_id'] == case.case_id
    assert payloads[1]['expected_information_gain'] > 0
    assert payloads[2]['authority'] == 'analysis_only'
