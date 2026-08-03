# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import codecs
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'src')
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from external_tool_executor import ExternalToolExecutor
from observability import ObservationRecorder


def load_cases(path):
    cases = []
    with codecs.open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    return cases


def evaluate(case, result):
    expected = case.get('expected') or {}
    errors = []
    if expected.get('status') is not None and result.get('status') != expected.get('status'):
        errors.append('status expected=%s got=%s' % (expected.get('status'), result.get('status')))
    if expected.get('failure_type') is not None:
        got = (result.get('diagnostics') or {}).get('failure_type')
        if got != expected.get('failure_type'):
            errors.append('failure_type expected=%s got=%s' % (expected.get('failure_type'), got))
    for key in expected.get('output_keys') or []:
        if key not in (result.get('data') or {}):
            errors.append('missing_output_key: %s' % key)
    trace_event = result.get('trace_event') or {}
    if expected.get('trace_event') and trace_event.get('name') != expected.get('trace_event'):
        errors.append('trace_event expected=%s got=%s' % (expected.get('trace_event'), trace_event.get('name')))
    if expected.get('risk_level') and trace_event.get('risk_level') != expected.get('risk_level'):
        errors.append('risk_level expected=%s got=%s' % (expected.get('risk_level'), trace_event.get('risk_level')))
    if expected.get('side_effect') and trace_event.get('side_effect') != expected.get('side_effect'):
        errors.append('side_effect expected=%s got=%s' % (expected.get('side_effect'), trace_event.get('side_effect')))
    return {'id': case.get('id'), 'passed': len(errors) == 0, 'errors': errors, 'result': result}


def main(argv):
    suite = argv[1] if len(argv) > 1 else 'external_tools'
    if suite == 'external_tools':
        path = os.path.join(ROOT, 'harness', 'cases', 'external_tools.jsonl')
    else:
        path = suite if os.path.isabs(suite) else os.path.join(ROOT, suite)
    observer = ObservationRecorder()
    executor = ExternalToolExecutor(observer=observer)
    results = []
    for case in load_cases(path):
        result = executor.call(case.get('tool_id'), case.get('args') or {}, case.get('context') or {})
        results.append(evaluate(case, result))
    total = len(results)
    passed = len([r for r in results if r.get('passed')])
    failed = total - passed
    failures = [r for r in results if not r.get('passed')]
    metrics = {'total': total, 'passed': passed, 'failed': failed, 'pass_rate': (float(passed) / total if total else 0.0)}
    print('EXTERNAL_TOOL_HARNESS suite=%s total=%d passed=%d failed=%d' % (suite, total, passed, failed))
    print('METRICS %s' % json.dumps(metrics, ensure_ascii=False, sort_keys=True))
    print('FAILURES %s' % json.dumps(failures, ensure_ascii=False, sort_keys=True))
    return 0 if failed == 0 else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv))
