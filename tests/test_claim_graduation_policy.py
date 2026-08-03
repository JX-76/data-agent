# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'src')
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from case_blackboard import CaseBlackboard
from case_contracts import ActionCard, Hypothesis, OutcomeMeasurement
from claim_graduation import ClaimGraduationPolicy, LEVEL_CONFIRMED_CAUSAL, LEVEL_QUANTIFIED
from contracts import build_execution_envelope
from evidence_bus import EvidenceBus
from gmv_health_playbook import build_gmv_health_case, gmv_health_expected_scope


def _bus():
    bus = EvidenceBus()
    bus.record_envelope(build_execution_envelope(
        status='ok', stage='db_execute', query_id='q1', evidence_id='ev1',
        dataid='orders', data_version='v1', row_count=2, time_range='last_7_days',
        authority='verified_execution',
        metadata={'metric': 'gmv', 'dimensions': ['channel'], 'filters': {}}))
    return bus


def _scope():
    return {'metric': 'gmv', 'allowed_time_ranges': ['last_7_days'],
            'dimensions': ['channel'], 'filters': {}, 'dataid': 'orders', 'data_version': 'v1'}


def test_quantified_driver_requires_methodology_and_prohibits_causal_wording():
    policy = ClaimGraduationPolicy()
    missing_method = policy.evaluate({'evidence_level': LEVEL_QUANTIFIED, 'text': '广告渠道贡献 20%',
                                      'evidence_ids': ['ev1']}, evidence_bus=_bus(), expected_scope=_scope())
    causal = policy.evaluate({'evidence_level': LEVEL_QUANTIFIED, 'text': '广告渠道导致 GMV 下滑',
                              'evidence_ids': ['ev1'], 'baseline': 'last_week',
                              'metric_definition': 'gmv', 'grain_safe': True, 'lineage': 'q1'},
                             evidence_bus=_bus(), expected_scope=_scope())

    assert missing_method['allowed'] is False
    assert missing_method['limitations'][0].startswith('quantified_driver_missing_methodology:')
    assert causal['allowed'] is False
    assert causal['limitations'] == ['quantified_driver_cannot_use_causal_wording']


def test_confirmed_causal_requires_identification_strategy_and_current_evidence():
    policy = ClaimGraduationPolicy()
    rejected = policy.evaluate({'evidence_level': LEVEL_CONFIRMED_CAUSAL, 'text': 'A caused B',
                                'evidence_ids': ['ev1']}, evidence_bus=_bus(), expected_scope=_scope())
    allowed = policy.evaluate({'evidence_level': LEVEL_CONFIRMED_CAUSAL, 'text': 'A caused B',
                               'evidence_ids': ['ev1'], 'identification_strategy': 'randomized experiment'},
                              evidence_bus=_bus(), expected_scope=_scope())

    assert rejected['allowed'] is False
    assert rejected['limitations'] == ['confirmed_causal_requires_identification_strategy_and_current_evidence']
    assert allowed['allowed'] is True
    assert allowed['level'] == LEVEL_CONFIRMED_CAUSAL


def test_case_blackboard_blocks_action_without_constraints_or_approval_and_accepts_complete_action():
    case = build_gmv_health_case(data_version='v1')
    board = CaseBlackboard(case, evidence_bus=_bus())
    scope = gmv_health_expected_scope(case)
    board.evidence_view.link(['ev1'], expected_scope=scope)

    blocked = board.draft_action(ActionCard(case.case_id, 'investigate', evidence_ids=['ev1'],
                                             constraints=[], approval_required=False), expected_scope=scope)
    accepted = board.draft_action(ActionCard(case.case_id, 'investigate', evidence_ids=['ev1'],
                                              constraints=['read_only'], risk_level='medium',
                                              approval_required=True), expected_scope=scope)

    assert blocked['ok'] is False
    assert blocked['error'] == 'action_not_graduated'
    assert accepted['ok'] is True


def test_case_blackboard_blocks_outcome_without_baseline_window_or_method():
    case = build_gmv_health_case(data_version='v1')
    board = CaseBlackboard(case, evidence_bus=_bus())
    scope = gmv_health_expected_scope(case)
    board.evidence_view.link(['ev1'], expected_scope=scope)

    blocked = board.add_outcome(OutcomeMeasurement(case.case_id, 'action1', evidence_ids=['ev1'],
                                                    baseline={}, observation_window=None, method=''),
                                expected_scope=scope)
    accepted = board.add_outcome(OutcomeMeasurement(case.case_id, 'action1', evidence_ids=['ev1'],
                                                     baseline={'gmv': 100}, observation_window='7d',
                                                     method='matched_baseline'), expected_scope=scope)

    assert blocked['ok'] is False
    assert blocked['error'] == 'outcome_not_graduated'
    assert accepted['ok'] is True


def test_case_blackboard_blocks_hypothesis_using_evidence_linked_to_another_case():
    bus = _bus()
    case_a = build_gmv_health_case(data_version='v1')
    case_b = build_gmv_health_case(data_version='v1')
    board_a = CaseBlackboard(case_a, evidence_bus=bus)
    board_b = CaseBlackboard(case_b, evidence_bus=bus)
    scope = gmv_health_expected_scope(case_a)
    board_a.evidence_view.link(['ev1'], expected_scope=scope)

    result = board_b.propose_hypothesis(
        Hypothesis(case_b.case_id, 'Ads may explain the GMV change',
                   support_evidence_ids=['ev1']),
        expected_scope=gmv_health_expected_scope(case_b))

    assert result['ok'] is False
    assert result['error'] == 'invalid_evidence_refs'
    assert result['rejected'] == [{'evidence_id': 'ev1', 'error': 'evidence_not_linked_to_case'}]
    assert board_b.hypotheses == {}
    assert bus.has('ev1') is True
