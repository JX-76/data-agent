# -*- coding: utf-8 -*-
"""Compatibility tests for split context modules."""
from __future__ import unicode_literals

import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.join(ROOT, 'src'))

from context_manager import (
    ContextBudget,
    MessageCompactor,
    PrefixCacheManager,
    ResultTrimmer,
    RollingSummarizer,
    estimate_messages_tokens,
    estimate_tokens,
)


class ContextCompatibilityTest(unittest.TestCase):
    def test_legacy_context_manager_exports_still_work(self):
        self.assertTrue(estimate_tokens('abcdef') >= 0)
        self.assertTrue(estimate_messages_tokens([{'role': 'user', 'content': 'hello'}]) > 0)

        manager = PrefixCacheManager()
        manager.set_tools('catalog()')
        self.assertIn('Rules', manager.cacheable_prefix)

        trimmed = ResultTrimmer.trim_rows([{'a': 1, 'b': 'x'}])
        self.assertEqual(1, trimmed['row_count'])
        self.assertEqual(['a', 'b'], trimmed['columns'])

        compacted = MessageCompactor(max_tokens=10000).compact([
            {'role': 'user', 'content': 'hello'},
        ])
        self.assertEqual('hello', compacted[0]['content'])

        budget = ContextBudget(max_total_tokens=100, reserve_for_response=10)
        budget.add(20)
        self.assertEqual(70, budget.available)

        summarizer = RollingSummarizer(max_summary_tokens=100)
        summarizer.add_turn(u'昨天 GMV', {'status': 'ok', 'metric': 'gmv'})
        self.assertIn(u'对话上下文', summarizer.get_injectable_context())


if __name__ == '__main__':
    unittest.main()
