# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'src')
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from intent_engine import IntentEngine


def test_explicit_external_tool_failure_is_no_answer_not_clarification():
    result = IntentEngine().parse(u'外部工具返回错误时继续分析')

    assert result['status'] == 'no_answer'
    assert result['intent'] == 'evidence_limited'
    assert result['should_execute'] is False
    assert 'failure_path.no_answer' in result['matched_rules']
    assert u'没有已验证证据' in result['blocked_reason']


def test_explicit_missing_or_empty_results_are_no_answer():
    engine = IntentEngine()

    for query in (u'空结果也给我结论', u'没有数据也判断原因', u'数据库连接失败时给数字'):
        result = engine.parse(query)
        assert result['status'] == 'no_answer'
        assert result['should_execute'] is False


def test_regular_tool_question_is_not_captured_as_failure_simulation():
    result = IntentEngine().parse(u'如何配置外部工具查询GMV')

    assert result['status'] != 'no_answer'
    assert 'failure_path.no_answer' not in result['matched_rules']
