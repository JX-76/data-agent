# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.join(ROOT, 'src'))

from agent_harness import AgentHarness
from trace_replay import find_case_by_id, replay_case, save_replay


def _configure_stdout():
    """Best-effort UTF-8 console output for Windows replay diagnostics."""
    try:
        if hasattr(sys.stdout, 'reconfigure'):
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass


CASES_DIR = os.path.join(ROOT, 'harness', 'cases')
DEFAULT_SUITE_PATHS = {
    'base': os.path.join(CASES_DIR, 'base.jsonl'),
    'phase8': os.path.join(CASES_DIR, 'phase8.jsonl'),
    'ecommerce': os.path.join(CASES_DIR, 'ecommerce_smoke.jsonl'),
    'benchmark_r20': os.path.join(CASES_DIR, 'benchmark_r20.jsonl'),
}


def _load_cases(harness, suite_or_path):
    if os.path.isfile(suite_or_path):
        return harness.load_cases(suite_or_path)
    if suite_or_path == 'all':
        cases = []
        for name in ['base', 'phase8', 'ecommerce']:
            path = DEFAULT_SUITE_PATHS.get(name)
            if path and os.path.exists(path):
                cases.extend(harness.load_cases(path))
        return cases
    path = DEFAULT_SUITE_PATHS.get(suite_or_path)
    if path and os.path.exists(path):
        return harness.load_cases(path)
    raise IOError('Unknown suite or case file: %s' % suite_or_path)


def _trace_names(trace):
    names = []
    for item in trace or []:
        if isinstance(item, dict):
            name = item.get('name') or item.get('stage')
            if name:
                names.append(name)
    return names


def main(argv=None):
    _configure_stdout()
    argv = argv or sys.argv[1:]
    if len(argv) < 2:
        print('Usage: python scripts/replay_harness_failure.py <suite|case_file> <case_id>')
        return 2
    suite = argv[0]
    case_id = argv[1]
    harness = AgentHarness()
    cases = _load_cases(harness, suite)
    case = find_case_by_id(cases, case_id)
    if case is None:
        print('Case not found: %s' % case_id)
        return 2
    replay = replay_case(case)
    path = save_replay(ROOT, replay)
    actual = replay.get('result') or {}
    print('HARNESS_REPLAY id=%s passed=%s failure_type=%s' % (replay.get('id'), replay.get('passed'), replay.get('failure_type')))
    print('QUERY %s' % json.dumps(replay.get('query'), ensure_ascii=False, default=str))
    print('EXPECTED %s' % json.dumps(replay.get('expected') or {}, ensure_ascii=False, sort_keys=True, default=str))
    print('ACTUAL %s' % json.dumps(actual, ensure_ascii=False, sort_keys=True, default=str))
    print('TRACE_EVENTS %s' % json.dumps(_trace_names(replay.get('trace')), ensure_ascii=False, sort_keys=True, default=str))
    print('ERRORS %s' % json.dumps(replay.get('errors') or [], ensure_ascii=False, sort_keys=True, default=str))
    print('REPLAY_REPORT %s' % path)
    return 0 if replay.get('passed') else 1


if __name__ == '__main__':
    sys.exit(main())
