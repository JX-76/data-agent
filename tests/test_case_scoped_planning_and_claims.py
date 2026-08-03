# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'src')
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from case_blackboard import CaseBlackboard
from case_contracts import CaseArtifact, Hypothesis, ARTIFACT_SIGNAL, ARTIFACT_CONTRIBUTION
from claim_graduation import (ClaimGraduationPolicy, audit_final_answer_claims,
                              audit_answer_contract_with_provenance,
                              DEFAULT_FINAL_EVIDENCE_TTL_SECONDS)
from contracts import build_execution_envelope
from dynamic_task_planner import DynamicTaskPlanner, DECISION_APPEND_TASK, DECISION_STOP
from evidence_bus import EvidenceBus
from evidence_freshness import (assess_case_evidence_freshness, build_reexecution_plan,
                                resolve_evidence_ttl)
from gmv_health_playbook import build_gmv_health_case, gmv_health_expected_scope
from reexecution_dispatcher import EvidenceReexecutionDispatcher
from trace_contracts import validate_reexecution_replay


def _envelope(evidence_id='ev_case', metric='gmv', data_version='v1'):
    return build_execution_envelope(
        status='ok', stage='db_execute', query_id='q_case', evidence_id=evidence_id,
        dataid='orders', data_version=data_version, row_count=1, time_range='last_7_days',
        authority='verified_execution', metadata={'metric': metric, 'dimensions': ['channel'], 'filters': {}})


def test_case_blackboard_rejects_invalid_scope_without_deleting_shared_bus_record():
    bus = EvidenceBus()
    shared = bus.record_envelope(_envelope(metric='orders'))
    case = build_gmv_health_case(metric='gmv', dimensions=['channel'], data_version='v1')
    board = CaseBlackboard(case, evidence_bus=bus)

    rejected = board.prune_invalid_evidence(expected_scope=gmv_health_expected_scope(case))

    assert shared['evidence_id'] in bus.records
    assert rejected[0]['error'] == 'evidence_scope_mismatch'
    context = board.get_case_context()
    assert context['evidence_records'] == []
    assert context['evidence_view']['rejections'][0]['reason'] == 'rejected_scope_mismatch'


def test_case_scoped_rejection_does_not_hide_or_delete_another_case_evidence():
    bus = EvidenceBus()
    case_a = build_gmv_health_case(metric='gmv', dimensions=['channel'], data_version='v1')
    case_b = build_gmv_health_case(metric='orders', dimensions=['channel'], data_version='v1')
    board_a = CaseBlackboard(case_a, evidence_bus=bus)
    board_b = CaseBlackboard(case_b, evidence_bus=bus)

    accepted = board_a.record_execution_envelope(
        _envelope(evidence_id='ev_shared', metric='gmv'),
        expected_scope=gmv_health_expected_scope(case_a), now=100.0)
    rejected = board_b.prune_invalid_evidence(
        expected_scope=gmv_health_expected_scope(case_b), now=100.0)

    assert accepted['evidence_id'] == 'ev_shared'
    assert bus.has('ev_shared') is True
    assert [x['evidence_id'] for x in board_a.get_case_context()['evidence_records']] == ['ev_shared']
    assert board_b.get_case_context()['evidence_records'] == []
    assert rejected == [{'evidence_id': 'ev_shared', 'error': 'evidence_scope_mismatch', 'fields': ['metric']}]
    assert board_b.get_case_context()['evidence_view']['rejections'][0]['reason'] == 'rejected_scope_mismatch'
    assert any(event['event_type'] == 'evidence.rejected' for event in board_b.events)


def test_case_blackboard_rejects_unlinked_global_evidence_for_artifact_consumption():
    bus = EvidenceBus()
    case_a = build_gmv_health_case(metric='gmv', dimensions=['channel'], data_version='v1')
    case_b = build_gmv_health_case(metric='gmv', dimensions=['channel'], data_version='v1')
    board_a = CaseBlackboard(case_a, evidence_bus=bus)
    board_b = CaseBlackboard(case_b, evidence_bus=bus)
    board_a.record_execution_envelope(
        _envelope(evidence_id='ev_a', metric='gmv'),
        expected_scope=gmv_health_expected_scope(case_a), now=100.0)

    result = board_b.add_artifact(CaseArtifact(
        case_b.case_id, ARTIFACT_SIGNAL, evidence_ids=['ev_a']),
        expected_scope=gmv_health_expected_scope(case_b), now=100.0)

    assert result['ok'] is False
    assert result['error'] == 'invalid_evidence_refs'
    assert result['rejected'] == [{'evidence_id': 'ev_a', 'error': 'evidence_not_linked_to_case'}]
    assert bus.has('ev_a') is True


def test_case_view_tracks_ttl_rejection_non_destructively():
    bus = EvidenceBus()
    record = bus.record_envelope(_envelope())
    record['recorded_at'] = 10.0
    view = bus.case_view('case-1', expected_scope={'metric': 'gmv', 'allowed_time_ranges': ['last_7_days']}, ttl_seconds=1)

    valid, rejected = view.link(['ev_case'], now=20.0)

    assert valid == []
    assert rejected[0]['error'] == 'evidence_ttl_expired'
    assert 'ev_case' in bus.records
    assert view.to_dict()['rejections'][0]['reason'] == 'rejected_ttl_expired'


def test_dynamic_task_planner_advances_only_after_required_evidence():
    case = build_gmv_health_case(dimensions=['channel'], data_version='v1')
    board = CaseBlackboard(case)
    planner = DynamicTaskPlanner()

    first = planner.decide(board, executed_task_types=[])
    assert first.decision == DECISION_APPEND_TASK
    assert first.task.task_type == 'verify_gmv_signal'

    stopped = planner.decide(board, executed_task_types=['verify_gmv_signal'])
    assert stopped.decision == DECISION_STOP
    assert stopped.reason == 'verified_signal_required'

    board.record_execution_envelope(_envelope(), expected_scope=gmv_health_expected_scope(case), now=100.0)
    board.add_artifact(CaseArtifact(case.case_id, ARTIFACT_SIGNAL, artifact_id='signal-1', evidence_ids=['ev_case']))
    next_decision = planner.decide(board, executed_task_types=['verify_gmv_signal'])
    assert next_decision.decision == DECISION_APPEND_TASK
    assert next_decision.task.task_type == 'decompose_gmv_drivers'

    board.add_artifact(CaseArtifact(case.case_id, ARTIFACT_CONTRIBUTION, artifact_id='contrib-1', evidence_ids=['ev_case']))
    board.propose_hypothesis(Hypothesis(case.case_id, 'h1', 'ads channel contributed to GMV change', support_evidence_ids=['ev_case']))
    audit_decision = planner.decide(board, executed_task_types=['verify_gmv_signal', 'decompose_gmv_drivers'])
    assert audit_decision.task.task_type == 'challenge_root_cause'


def test_case_freshness_policy_honors_case_and_metric_ttl_overrides():
    case = build_gmv_health_case(metric='gmv', data_version='v1')
    case.mission.policy['evidence_ttl_seconds'] = 60
    case.mission.policy['evidence_ttl_by_metric'] = {'gmv': 10}
    board = CaseBlackboard(case)
    record = board.record_execution_envelope(_envelope(), expected_scope=gmv_health_expected_scope(case))
    record['recorded_at'] = 100.0

    freshness = assess_case_evidence_freshness(board, now=111.0)

    assert resolve_evidence_ttl(case) == 10.0
    assert freshness['contract'] == 'case_evidence_freshness_v2'
    assert freshness['needs_reexecution'] is True
    assert freshness['reexecution_reason'] == 'evidence_ttl_expired'
    assert freshness['rejected'][0]['error'] == 'evidence_ttl_expired'
    assert freshness['reexecution_plan']['contract'] == 'evidence_reexecution_plan_v1'
    assert freshness['reexecution_plan']['required'] is True
    assert freshness['reexecution_plan']['task_type'] == 'verify_gmv_signal'
    assert freshness['reexecution_plan']['invalid_evidence_ids'] == ['ev_case']


def test_planner_reexecutes_stale_case_evidence_with_auditable_decision():
    case = build_gmv_health_case(metric='gmv', data_version='v1')
    case.mission.policy['evidence_ttl_seconds'] = 1
    board = CaseBlackboard(case)
    record = board.record_execution_envelope(_envelope(), expected_scope=gmv_health_expected_scope(case))
    record['recorded_at'] = 10.0
    board.add_artifact(CaseArtifact(case.case_id, ARTIFACT_SIGNAL,
                                    artifact_id='signal-stale', evidence_ids=['ev_case']))

    decision = DynamicTaskPlanner().decide(board, executed_task_types=['verify_gmv_signal'], now=20.0)

    assert decision.decision == DECISION_APPEND_TASK
    assert decision.reason == 'current_evidence_requires_reexecution'
    assert decision.task.task_type == 'verify_gmv_signal'
    assert decision.metadata['freshness']['rejected'][0]['error'] == 'evidence_ttl_expired'
    assert decision.metadata['freshness']['reexecution_plan']['required'] is True
    assert decision.metadata['expected_scope']['metric'] == 'gmv'


def test_case_freshness_for_scope_change_requires_reexecution():
    case = build_gmv_health_case(metric='gmv', data_version='v1')
    board = CaseBlackboard(case)
    board.record_execution_envelope(_envelope(), expected_scope=gmv_health_expected_scope(case))

    expected = dict(gmv_health_expected_scope(case))
    expected['data_version'] = 'v2'
    freshness = assess_case_evidence_freshness(board, expected_scope=expected, now=100.0)

    assert freshness['needs_reexecution'] is True
    assert freshness['reexecution_reason'] == 'evidence_scope_mismatch'
    assert freshness['reexecution_plan']['reason'] == 'evidence_scope_mismatch'
    assert freshness['rejected'][0]['fields'] == ['data_version']


def test_invalid_ttl_policy_fails_closed_to_default_ttl_not_unlimited():
    case = build_gmv_health_case(metric='gmv', data_version='v1')
    case.mission.policy['evidence_ttl_seconds'] = 0
    board = CaseBlackboard(case)
    record = board.record_execution_envelope(_envelope(), expected_scope=gmv_health_expected_scope(case))
    record['recorded_at'] = 10.0

    freshness = assess_case_evidence_freshness(board, now=10.0 + DEFAULT_FINAL_EVIDENCE_TTL_SECONDS + 1)

    assert resolve_evidence_ttl(case) == float(DEFAULT_FINAL_EVIDENCE_TTL_SECONDS)
    assert freshness['needs_reexecution'] is True
    assert freshness['reexecution_reason'] == 'evidence_ttl_expired'


def test_reexecution_plan_is_serializable_and_scope_bound():
    freshness = {
        'needs_reexecution': True,
        'reexecution_reason': 'evidence_scope_mismatch',
        'expected_scope': {'metric': 'gmv', 'data_version': 'v2'},
        'rejected': [{'evidence_id': 'ev_old', 'error': 'evidence_scope_mismatch'}],
        'ttl_seconds': 300,
    }

    plan = build_reexecution_plan(freshness, task_type='verify_current_scope')

    assert plan == {
        'contract': 'evidence_reexecution_plan_v1',
        'required': True,
        'task_type': 'verify_current_scope',
        'reason': 'evidence_scope_mismatch',
        'expected_scope': {'metric': 'gmv', 'data_version': 'v2'},
        'invalid_evidence_ids': ['ev_old'],
        'ttl_seconds': 300,
    }


def test_reexecution_dispatcher_schedules_idempotently_and_records_completion():
    case = build_gmv_health_case(metric='gmv', data_version='v1')
    board = CaseBlackboard(case)
    freshness = {
        'needs_reexecution': True,
        'reexecution_reason': 'evidence_ttl_expired',
        'expected_scope': gmv_health_expected_scope(case),
        'rejected': [{'evidence_id': 'ev_old', 'error': 'evidence_ttl_expired'}],
        'ttl_seconds': 300,
    }
    plan = build_reexecution_plan(freshness)
    dispatcher = EvidenceReexecutionDispatcher()

    scheduled = dispatcher.dispatch(board, plan, tenant_id='tenant-a', session_id='session-a')
    duplicate = dispatcher.dispatch(board, plan, tenant_id='tenant-a', session_id='session-a')
    completed = dispatcher.complete(board, scheduled, _envelope(evidence_id='ev_new'), now=100.0)

    assert scheduled['status'] == 'scheduled'
    assert scheduled['idempotency_key'].startswith('idem:evidence_reexecution:verify_gmv_signal:')
    assert scheduled['task']['metadata']['expected_scope']['metric'] == 'gmv'
    assert duplicate['status'] == 'duplicate'
    assert duplicate['idempotency_key'] == scheduled['idempotency_key']
    assert completed['status'] == 'completed'
    assert completed['evidence_id'] == 'ev_new'
    event_types = [event['event_type'] for event in board.events]
    assert 'reexecution.scheduled' in event_types
    assert 'reexecution.completed' in event_types
    assert validate_reexecution_replay(board.get_case_context())['valid'] is True


def test_reexecution_dispatcher_does_not_complete_with_scope_mismatch():
    case = build_gmv_health_case(metric='gmv', data_version='v1')
    board = CaseBlackboard(case)
    freshness = {
        'needs_reexecution': True,
        'reexecution_reason': 'evidence_scope_mismatch',
        'expected_scope': gmv_health_expected_scope(case),
        'rejected': [{'evidence_id': 'ev_wrong', 'error': 'evidence_scope_mismatch'}],
        'ttl_seconds': 300,
    }
    scheduled = EvidenceReexecutionDispatcher().dispatch(board, build_reexecution_plan(freshness))

    failed = EvidenceReexecutionDispatcher().complete(
        board, scheduled, _envelope(evidence_id='ev_orders', metric='orders'), now=100.0)

    assert failed['status'] == 'failed'
    assert failed['evidence_id'] is None
    validation = validate_reexecution_replay(board.get_case_context())
    assert validation['valid'] is False
    assert validation['errors'][0].startswith('reexecution_failed:')


def test_reexecution_replay_flags_scheduled_without_terminal_event():
    case = build_gmv_health_case(metric='gmv', data_version='v1')
    board = CaseBlackboard(case)
    freshness = {
        'needs_reexecution': True,
        'reexecution_reason': 'evidence_ttl_expired',
        'expected_scope': gmv_health_expected_scope(case),
        'rejected': [],
        'ttl_seconds': 300,
    }
    scheduled = EvidenceReexecutionDispatcher().dispatch(board, build_reexecution_plan(freshness))

    validation = validate_reexecution_replay(board.get_case_context())

    assert scheduled['status'] == 'scheduled'
    assert validation['valid'] is False
    assert validation['errors'] == ['reexecution_missing_terminal_event:%s' % scheduled['idempotency_key']]


def test_claim_graduation_blocks_verified_fact_without_current_evidence():
    policy = ClaimGraduationPolicy()
    decision = policy.evaluate({'text': 'GMV grew 20%', 'kind': 'fact', 'evidence_ids': ['missing']},
                               evidence_bus=EvidenceBus(), expected_scope={'metric': 'gmv'})
    assert decision['allowed'] is False
    assert decision['limitations'] == ['verified_fact_requires_current_execution_evidence']


def test_final_answer_audit_demotes_fact_when_scope_mismatch():
    bus = EvidenceBus()
    bus.record_envelope(_envelope(metric='orders', evidence_id='ev_wrong'))
    answer = {'status': 'ok', 'answer_type': 'analysis', 'facts': [{'text': 'GMV is 100', 'evidence_ids': ['ev_wrong']}],
              'hypotheses': [], 'limitations': [], 'evidence_ids': ['ev_wrong']}

    audited, findings = audit_final_answer_claims(answer, evidence_bus=bus,
                                                  expected_scope={'metric': 'gmv', 'allowed_time_ranges': ['last_7_days']})

    assert audited['status'] == 'no_answer'
    assert audited['facts'] == []
    assert audited['hypotheses'][0]['validation_needed'] == 'current_verified_execution_evidence'
    assert findings[0]['code'] == 'fact_not_graduated'


def test_serialized_provenance_bus_audits_final_answer_scope_before_release():
    envelope = _envelope(metric='gmv', evidence_id='ev_release')
    answer = {
        'status': 'ok', 'answer_type': 'analysis',
        'facts': [{'text': 'GMV is 100', 'evidence_ids': ['ev_release']}],
        'hypotheses': [], 'limitations': [], 'evidence_ids': ['ev_release'],
    }
    provenance = {'evidence_bus': {'records': [envelope], 'contract': 'evidence_bus_v1'}}

    audited, findings, was_audited = audit_answer_contract_with_provenance(
        answer, provenance=provenance,
        scope={'metric': 'orders', 'allowed_time_ranges': ['last_7_days']})

    assert was_audited is True
    assert findings[0]['code'] == 'fact_not_graduated'
    assert audited['status'] == 'no_answer'
    assert audited['answer_type'] == 'evidence_limited'
    assert audited['facts'] == []


def test_serialized_provenance_bus_rejects_expired_final_answer_evidence():
    envelope = _envelope(metric='gmv', evidence_id='ev_old')
    bus = EvidenceBus()
    record = bus.record_envelope(envelope)
    record['recorded_at'] = 10.0
    answer = {
        'status': 'ok', 'answer_type': 'analysis',
        'facts': [{'text': 'GMV is 100', 'evidence_ids': ['ev_old']}],
        'hypotheses': [], 'limitations': [], 'evidence_ids': ['ev_old'],
    }
    provenance = {'evidence_bus': bus.to_dict()}

    audited, findings, was_audited = audit_answer_contract_with_provenance(
        answer, provenance=provenance,
        scope={'metric': 'gmv', 'allowed_time_ranges': ['last_7_days']},
        ttl_seconds=DEFAULT_FINAL_EVIDENCE_TTL_SECONDS,
        now=10.0 + DEFAULT_FINAL_EVIDENCE_TTL_SECONDS + 1)

    assert was_audited is True
    assert audited['status'] == 'no_answer'
    assert audited['facts'] == []
    assert audited['hypotheses'][0]['validation_needed'] == 'current_verified_execution_evidence'
    assert findings[0]['rejected'][0]['error'] == 'evidence_ttl_expired'


def test_required_serialized_provenance_bus_fails_closed_when_missing():
    answer = {
        'status': 'ok', 'answer_type': 'analysis',
        'facts': [{'text': 'GMV is 100', 'evidence_ids': ['ev_missing_bus']}],
        'hypotheses': [], 'limitations': [], 'evidence_ids': ['ev_missing_bus'],
        'citations': ['ev_missing_bus'],
    }

    audited, findings, was_audited = audit_answer_contract_with_provenance(
        answer, provenance={}, scope={'metric': 'gmv'}, require_evidence_bus=True)

    assert was_audited is True
    assert audited['status'] == 'no_answer'
    assert audited['answer_type'] == 'evidence_limited'
    assert audited['facts'] == []
    assert audited['evidence_ids'] == []
    assert audited['citations'] == []
    assert audited['hypotheses'][0]['validation_needed'] == 'serialized_current_execution_evidence'
    assert findings[0]['code'] == 'evidence_bus_missing'
    assert 'serialized_evidence_bus_required' in audited['limitations']


def test_missing_serialized_provenance_bus_legacy_mode_remains_unaudited():
    answer = {
        'status': 'ok', 'answer_type': 'analysis',
        'facts': [{'text': 'Legacy informational fact', 'evidence_ids': ['ev_legacy']}],
        'hypotheses': [], 'limitations': [], 'evidence_ids': ['ev_legacy'],
    }

    audited, findings, was_audited = audit_answer_contract_with_provenance(
        answer, provenance={}, scope={'metric': 'gmv'})

    assert was_audited is False
    assert findings == []
    assert audited['status'] == 'ok'
    assert audited['facts'][0]['text'] == 'Legacy informational fact'
