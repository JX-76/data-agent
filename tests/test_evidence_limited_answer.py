# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from evidence_limited_answer import build_evidence_limited_answer
from answer_quality_evaluator import evaluate_turn


def test_limited_answer_is_useful_without_turning_hypotheses_into_facts():
    answer = build_evidence_limited_answer(u'双11预热加购总件数是去年的80%，帮我分析。', 'schema_error')
    assert u'受限证据分析' in answer
    assert u'尚未由当前数据源核验' in answer
    assert u'渠道、商品/SKU、时段' in answer
    assert u'最小补数清单' in answer
    assert u'已确认原因' not in answer


def test_quality_evaluator_penalizes_distractor_claims():
    result = {'status': 'degraded', 'answer': u'受限证据分析\n建议核对渠道和商品/SKU。'}
    score = evaluate_turn(u'为什么转化下降', result, {
        'expected_evidence_modes': ['limited_analysis'],
        'expected_hypotheses': [u'渠道'],
        'forbidden_claims': [u'竞品降价就是根因'],
    })
    assert score['credibility_score'] == 60
    assert score['usefulness_score'] >= 36


def test_quality_evaluator_detects_forbidden_claim():
    result = {'status': 'degraded', 'answer': u'竞品降价就是根因'}
    score = evaluate_turn(u'为什么转化下降', result, {
        'expected_evidence_modes': ['limited_analysis'],
        'forbidden_claims': [u'竞品降价就是根因'],
    })
    assert any(item['code'] == 'forbidden_claims_hit' for item in score['issues'])
