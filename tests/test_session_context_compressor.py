# -*- coding: utf-8 -*-
"""Regression tests for bounded, hallucination-safe multi-turn context."""
from __future__ import unicode_literals

import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.join(ROOT, 'src'))

from context import SessionContextCompressor
from rag_governance import PromptContextCompiler


class SessionContextCompressorTest(unittest.TestCase):
    def _result(self, index):
        return {
            'status': 'ok', 'task_id': 'task_%s' % index,
            'metric': 'gmv', 'dimensions': ['channel'],
            'filters': {'region': 'east'}, 'time_range': 'last_7_days',
            'task_type': 'descriptive', 'dataid': 'data_%s' % index,
            # These must never be copied into the long-lived checkpoint.
            'answer': '模型臆造结论：GMV增长999%%',
            'insight': {'insight': '模型臆造结论：GMV增长999%%'},
            'results': [{'secret': 'raw row should not persist'}],
        }

    def test_long_session_keeps_bounded_window_and_excludes_generated_prose(self):
        context = SessionContextCompressor(max_recent_turns=3, max_tokens=500)
        for index in range(20):
            context.add_turn('第%s轮查询 %s' % (index, 'x' * 100), self._result(index))
        built = context.build('当前追问')
        rendered = context.render('当前追问')
        self.assertEqual(20, built['turn_count'])
        self.assertLessEqual(len(built['recent_turns']), 3)
        self.assertTrue(built['within_budget'])
        self.assertNotIn('臆造结论', rendered)
        self.assertNotIn('raw row should not persist', rendered)
        self.assertIn('task_19', rendered)

    def test_over_budget_folds_old_turns_but_retains_evidence_references(self):
        context = SessionContextCompressor(max_recent_turns=4, max_tokens=160)
        for index in range(8):
            value = self._result(index)
            value['filters'] = {'very_long_filter': 'z' * 200}
            context.add_turn('query ' + ('y' * 300), value)
        built = context.build()
        self.assertTrue(built['within_budget'])
        self.assertLessEqual(len(built['evidence_refs']), 8)
        self.assertLessEqual(len(built['recent_turns']), 4)

    def test_prompt_marks_session_context_as_non_factual(self):
        context = SessionContextCompressor()
        context.add_turn('看GMV', self._result(1))
        prompt = PromptContextCompiler().compile(
            'report', '继续看', conversation_context=context.build())
        self.assertIn('BOUNDED_SESSION_CONTEXT_NOT_FACT', prompt)
        self.assertIn('只能由当前任务匹配的工具/SQL执行证据支持', prompt)

    def test_snapshot_restore_is_protocol_bounded_and_strips_unknown_state(self):
        context = SessionContextCompressor(max_recent_turns=2, max_tokens=300)
        snapshot = context.restore({
            'version': 1,
            'turn_count': 7,
            'task_state': {
                'metric': 'gmv', 'filters': {'region': 'east'},
                'answer': '模型臆造结论：GMV增长999%%',
                'sql': 'select * from secret_table',
            },
            'evidence_refs': ['e1', 'e1', 'e2'],
            'dataids': ['d1'],
            'pending': {'clarify': 'choose metric'},
        })
        self.assertEqual(2, snapshot['version'])
        self.assertEqual(7, snapshot['turn_count'])
        self.assertIn('metric', snapshot['task_state'])
        self.assertNotIn('answer', snapshot['task_state'])
        self.assertNotIn('sql', snapshot['task_state'])
        rendered = context.render()
        self.assertNotIn('臆造结论', rendered)
        self.assertNotIn('secret_table', rendered)
        self.assertEqual(['e1', 'e2'], snapshot['evidence_refs'])

    def test_snapshot_restore_rejects_untrusted_fields(self):
        context = SessionContextCompressor()
        with self.assertRaises(ValueError):
            context.restore({'version': 2, 'answer': 'should not restore'})
        with self.assertRaises(ValueError):
            context.restore({'version': 99})


if __name__ == '__main__':
    unittest.main()
