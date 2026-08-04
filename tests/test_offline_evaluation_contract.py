# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import unittest

from offline_evaluation_contract import (DeterministicScorer, JudgeAdapter, MetricRegistry,
                                         OfflineEvaluationCase, canonical_sql, contains_pii)


class OfflineEvaluationContractTests(unittest.TestCase):
    def test_silver_blocking_case_is_rejected(self):
        case = OfflineEvaluationCase('c1', '查询GMV', metadata={'quality_tier': 'silver', 'measurement_mode': 'deterministic', 'blocking': True})
        self.assertIn('silver_case_cannot_be_blocking', case.validate())

    def test_result_equivalence_is_structural_and_numeric(self):
        self.assertTrue(DeterministicScorer.result_equivalent([{'gmv': 10.0}], [{'gmv': 10.0}]))
        self.assertFalse(DeterministicScorer.result_equivalent([{'gmv': 10.0}], [{'gmv': 11.0}]))
        self.assertTrue(DeterministicScorer.numeric_answer_matches(u'GMV 是 120.5 元', [120.5]))
        self.assertFalse(DeterministicScorer.numeric_answer_matches(u'GMV 是 121 元', [120.5]))

    def test_canonical_sql_finds_literal_variants(self):
        a = "SELECT SUM(gmv) FROM orders WHERE region='east'"
        b = " select sum(gmv) from orders where region = 'north' -- comment"
        self.assertEqual(canonical_sql(a), canonical_sql(b))
        scored = DeterministicScorer.duplicate_query_rate({'tool_calls': [{'sql': a}, {'sql': b}]})
        self.assertEqual(1, scored['duplicates'])

    def test_pii_detector(self):
        self.assertTrue(contains_pii(u'请联系 13800138000'))
        self.assertFalse(contains_pii(u'已按权限脱敏'))

    def test_certificate_blocks_missing_required_human_and_judge_evidence(self):
        registry = MetricRegistry()
        certificate = registry.certify({
            'sql_syntax_accuracy': {'value': 1.0, 'sample_size': 100, 'measurement_mode': 'deterministic'},
            'sql_result_equivalence': {'value': 1.0, 'sample_size': 100, 'measurement_mode': 'deterministic'},
            'final_answer_numeric_accuracy': {'value': 1.0, 'sample_size': 100, 'measurement_mode': 'deterministic'},
            'hallucinated_fact_rate': {'value': 0.0, 'sample_size': 50, 'measurement_mode': 'deterministic'},
            'permission_violation_rate': {'value': 0.0, 'sample_size': 50, 'measurement_mode': 'deterministic'},
            'injection_escape_rate': {'value': 0.0, 'sample_size': 50, 'measurement_mode': 'deterministic'},
            'pii_exposure_rate': {'value': 0.0, 'sample_size': 50, 'measurement_mode': 'deterministic'},
            'fault_recovery_rate': {'value': 1.0, 'sample_size': 50, 'measurement_mode': 'deterministic'},
            'memory_scope_isolation_rate': {'value': 1.0, 'sample_size': 30, 'measurement_mode': 'deterministic'},
        })
        self.assertEqual('blocked', certificate['status'])
        self.assertIn('expert_adoption_intent', certificate['blocking_metrics'])
        self.assertIn('attribution_faithfulness', certificate['blocking_metrics'])

    def test_stub_judge_is_not_measured(self):
        result = JudgeAdapter().evaluate({'id': 'r'}, {'answer': 'x'})
        self.assertEqual('not_measured', result['status'])


if __name__ == '__main__':
    unittest.main()
