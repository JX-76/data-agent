# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'src')
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from ecommerce_diagnosis_harness import (EcommerceDiagnosisEvaluator,
                                         DeterministicQualityEvaluator,
                                         flatten_result_for_report)


def test_evaluator_requires_evidence_for_fact_bearing_ok_response():
    evaluator = EcommerceDiagnosisEvaluator()
    case = {'id': 'c1', 'category': 'core',
            'expected': {'status': 'ok', 'requires_evidence': True}}
    got = evaluator.evaluate(case, {'status': 'ok', 'report': '转化率为2%'}, [])
    assert got['passed'] is False
    assert 'hallucination_guard' in got['architecture_domains']


def test_evaluator_maps_missing_signal_to_case_architecture_domain():
    evaluator = EcommerceDiagnosisEvaluator()
    case = {'id': 'c2', 'category': 'governance',
            'expected': {'allowed_statuses': ['blocked'], 'must_contain': ['风险']},
            'failure_map': {'missing_signal': 'governance'}}
    got = evaluator.evaluate(case, {'status': 'blocked', 'blocked_reason': 'write forbidden'}, [])
    assert got['passed'] is False
    assert got['architecture_domains'] == ['governance']


def test_quality_audit_marks_explicit_untraceable_fact_as_critical():
    evaluator = DeterministicQualityEvaluator()
    quality = evaluator.evaluate(
        {'expected': {'requires_evidence': True}},
        {'status': 'ok', 'user_answer': 'ROI 是 0.8',
         'claims': [{'text': 'ROI 是 0.8', 'claim_type': 'numeric_fact'}]})
    assert quality['scorable'] is True
    assert quality['hallucination']['risk'] == 'critical'
    assert 'untraceable_fact_claim' in [x['type'] for x in quality['hallucination']['findings']]


def test_quality_audit_preserves_answer_evidence_and_claim_references():
    evaluator = DeterministicQualityEvaluator()
    result = {'status': 'ok', 'user_answer': '搜索 ROI 为 1.2，建议复核低效计划。',
              'provenance': [{'tool': 'channel_report', 'query_id': 'q1'}],
              'claims': [{'text': '搜索 ROI 为 1.2', 'claim_type': 'numeric_fact',
                          'evidence_refs': [{'tool': 'channel_report', 'query_id': 'q1'}]}],
              'actions': ['复核低效计划'], 'limitations': ['未含竞品数据']}
    quality = evaluator.evaluate({'expected': {'requires_evidence': True}}, result, [{'name': 'execute'}])
    envelope = quality['answer_envelope']
    assert quality['scorable'] is True
    assert quality['hallucination']['risk'] == 'none'
    assert envelope['user_answer'].startswith('搜索 ROI')
    assert envelope['claims'][0]['evidence_refs'][0]['query_id'] == 'q1'
    compact = flatten_result_for_report(result, [{'name': 'execute'}])
    assert compact['answer_observability']['trace_events'] == ['execute']


def test_quality_audit_marks_ok_without_user_answer_as_unscorable():
    quality = DeterministicQualityEvaluator().evaluate({}, {'status': 'ok', 'results_summary': {'rows': 1}})
    assert quality['scorable'] is True
    assert quality['hallucination']['risk'] == 'high'
    assert quality['score'] == 25


def test_summary_returns_category_rates_and_hotspots():
    evaluator = EcommerceDiagnosisEvaluator()
    summary = evaluator.summarize([
        {'category': 'core', 'passed': True, 'architecture_domains': []},
        {'category': 'core', 'passed': False, 'architecture_domains': ['tool_selection']},
    ])
    assert summary['total'] == 2
    assert summary['category_breakdown']['core']['pass_rate'] == 0.5
    assert summary['architecture_hotspots']['tool_selection'] == 1
