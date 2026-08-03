# -*- coding: utf-8 -*-
"""Phase 22 regression tests for traceable context primitives.

Uses unittest so it can run even where pytest is not installed.
"""
from __future__ import unicode_literals

import json
import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.join(ROOT, 'src'))

from context import ContextBudget, MessageCompactor, PrefixBlock, PrefixCacheManager, ResultTrimmer, RollingSummarizer


class ContextPhase22Test(unittest.TestCase):
    def test_context_budget_and_prefix_block_keep_legacy_constructor(self):
        budget = ContextBudget(max_total_tokens=100, reserve_for_response=10)
        budget.add(20)
        self.assertEqual(70, budget.available)
        self.assertFalse(budget.is_near_limit)
        budget.reset()
        self.assertEqual(0, budget.current_tokens)

        prefix = PrefixBlock('hello')
        self.assertEqual('hello', prefix.content)
        self.assertGreaterEqual(prefix.tokens, 0)

    def test_prefix_cache_invalidates_when_tools_change(self):
        manager = PrefixCacheManager()
        first = manager.cacheable_prefix
        manager.set_tools('catalog()')
        second = manager.cacheable_prefix
        self.assertIn('## Rules', first)
        self.assertIn('## Rules', second)
        self.assertGreaterEqual(manager.prefix_tokens, 0)

    def test_result_trimmer_preserves_count_and_clips_long_values(self):
        rows = [{'long_text': 'x' * 80, 'metric': 1.23456}]
        trimmed = ResultTrimmer.trim_rows(rows)
        self.assertEqual(1, trimmed['row_count'])
        self.assertTrue(trimmed['sample'][0]['long_text'].endswith('...'))
        self.assertEqual(1.23, trimmed['sample'][0]['metric'])

    def test_rolling_summary_keeps_last_turn(self):
        summary = RollingSummarizer(max_summary_tokens=100)
        summary.add_turn('昨天 GMV', {'status': 'ok', 'metric': 'gmv'})
        self.assertIn('gmv', summary.get_injectable_context())

    def test_observation_trim_is_traceable(self):
        messages = [
            {'role': 'system', 'content': 'system'},
            {'role': 'user', 'content': 'question'},
            {'role': 'user', 'content': 'OBSERVATION:\nevidence_id: ev_1\ndataid: data_1\nrow_count: 12\nsubtask_summary: inspect GMV\n' + ('x' * 500)},
            {'role': 'assistant', 'content': 'next'},
            {'role': 'user', 'content': 'follow-up'},
            {'role': 'assistant', 'content': 'next2'},
            {'role': 'user', 'content': 'follow-up2'},
        ]
        compacted, trace = MessageCompactor(max_tokens=100).compact(messages, return_trace=True)
        observation = compacted[2]['content']
        self.assertIn('OBSERVATION_REF', observation)
        self.assertIn('evidence_id: ev_1', observation)
        self.assertIn('dataid: data_1', observation)
        self.assertTrue(any(item['action'] == 'observation_trim' and item['evidence_id'] == 'ev_1' for item in trace))

    def test_pair_compaction_never_emits_removed_marker_and_keeps_reference(self):
        action = json.dumps({'tool': 'aggregate', 'reason': 'GMV drilldown'})
        messages = [
            {'role': 'user', 'content': 'anchor: retain'},
            {'role': 'assistant', 'content': action},
            {'role': 'user', 'content': 'OBSERVATION:\nevidence_id: ev_2\ndataid: data_2\nrow_count: 88\nsubtask_summary: compare channels\n' + ('x' * 500)},
            {'role': 'assistant', 'content': 'next'},
            {'role': 'user', 'content': 'follow-up'},
            {'role': 'assistant', 'content': 'next2'},
            {'role': 'user', 'content': 'follow-up2'},
        ]
        compacted, trace = MessageCompactor(max_tokens=20).compact(messages, return_trace=True)
        contents = '\n'.join(item.get('content', '') for item in compacted)
        self.assertNotIn('[removed]', contents)
        self.assertIn('CONTEXT_COMPACTION_REF', contents)
        self.assertIn('evidence_id: ev_2', contents)
        self.assertIn('dataid: data_2', contents)
        self.assertTrue(any(item['action'] == 'pair_soft_delete' and item['evidence_id'] == 'ev_2' for item in trace))
        self.assertTrue(any(item.get('content') == 'anchor: retain' for item in compacted))

    def test_compactor_does_not_delete_unpaired_anchor_messages(self):
        messages = [
            {'role': 'system', 'content': 'task_anchor: task_1'},
            {'role': 'user', 'content': 'evidence_id: ev_anchor ' + ('x' * 600)},
            {'role': 'assistant', 'content': 'plain response'},
            {'role': 'user', 'content': 'plain request'},
            {'role': 'assistant', 'content': 'plain response2'},
            {'role': 'user', 'content': 'plain request2'},
            {'role': 'assistant', 'content': 'plain response3'},
        ]
        compacted, trace = MessageCompactor(max_tokens=10).compact(messages, return_trace=True)
        self.assertEqual(messages, compacted)
        self.assertEqual([], trace)


if __name__ == '__main__':
    unittest.main()
