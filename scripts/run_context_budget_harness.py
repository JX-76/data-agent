# -*- coding: utf-8 -*-
"""Run deterministic Phase 22 context-compaction harness cases."""
from __future__ import unicode_literals

import codecs
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.join(ROOT, 'src'))

from context import MessageCompactor

DEFAULT_CASE_PATH = os.path.join(ROOT, 'harness', 'cases', 'context_budget.jsonl')


def _load_cases(path):
    cases = []
    with codecs.open(path, 'r', encoding='utf-8') as handle:
        for line in handle:
            line = line.strip()
            if line and not line.startswith('#'):
                cases.append(json.loads(line))
    return cases


def _content(messages):
    return '\n'.join(message.get('content', '') for message in messages)


def evaluate_case(case):
    expected = case.get('expected') or {}
    messages, trace = MessageCompactor(max_tokens=case.get('max_tokens', 8000)).compact(
        case.get('messages') or [], return_trace=True)
    text = _content(messages)
    actions = [item.get('action') for item in trace]
    evidence_ids = [item.get('evidence_id') for item in trace if item.get('evidence_id')]
    errors = []

    for action in expected.get('trace_actions') or []:
        if action not in actions:
            errors.append('missing_trace_action: %s' % action)
    for fragment in expected.get('content_contains') or []:
        if fragment not in text:
            errors.append('missing_content: %s' % fragment)
    for fragment in expected.get('content_not_contains') or []:
        if fragment in text:
            errors.append('forbidden_content: %s' % fragment)
    for evidence_id in expected.get('trace_evidence_ids') or []:
        if evidence_id not in evidence_ids:
            errors.append('missing_trace_evidence_id: %s' % evidence_id)

    return {
        'id': case.get('id'),
        'passed': not errors,
        'errors': errors,
        'trace_actions': actions,
        'trace_evidence_ids': evidence_ids,
    }


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    path = argv[0] if argv else DEFAULT_CASE_PATH
    cases = _load_cases(path)
    results = [evaluate_case(case) for case in cases]
    failed = [item for item in results if not item['passed']]
    print('CONTEXT_BUDGET_HARNESS total=%d passed=%d failed=%d' % (
        len(results), len(results) - len(failed), len(failed)))
    if failed:
        print('FAILURES %s' % json.dumps(failed, ensure_ascii=False, sort_keys=True))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
