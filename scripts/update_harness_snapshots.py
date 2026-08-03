# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.join(ROOT, 'src'))

from agent_harness import AgentHarness
from harness_snapshot import load_json, normalize_report, save_json, snapshot_path, report_path

CASES_DIR = os.path.join(ROOT, 'harness', 'cases')
DEFAULT_SUITE_PATHS = {
    'base': os.path.join(CASES_DIR, 'base.jsonl'),
    'phase8': os.path.join(CASES_DIR, 'phase8.jsonl'),
    'ecommerce': os.path.join(CASES_DIR, 'ecommerce_smoke.jsonl'),
    'workflows': os.path.join(CASES_DIR, 'workflows.jsonl'),
    'react_controlled': os.path.join(CASES_DIR, 'react_controlled.jsonl'),
}


def _load_suite_cases(harness, suite):
    if os.path.isfile(suite):
        return harness.load_cases(suite)
    if suite == 'all':
        cases = []
        for name in ['base', 'phase8', 'ecommerce', 'react_controlled']:

            path = DEFAULT_SUITE_PATHS.get(name)
            if path and os.path.exists(path):
                cases.extend(harness.load_cases(path))
        return cases
    path = DEFAULT_SUITE_PATHS.get(suite)
    if path and os.path.exists(path):
        return harness.load_cases(path)
    raise IOError('Unknown suite or missing case file: %s' % suite)


def main(argv=None):
    argv = argv or sys.argv[1:]
    suite = argv[0] if argv else 'base'
    harness = AgentHarness()
    cases = _load_suite_cases(harness, suite)
    report = harness.run_suite(suite, cases)
    snapshot = normalize_report(report)
    out_path = snapshot_path(ROOT, suite)
    save_json(out_path, snapshot)
    print('HARNESS_SNAPSHOT suite=%s cases=%d path=%s' % (suite, len(snapshot.get('cases') or []), out_path))
    print('METRICS %s' % json.dumps(snapshot.get('metrics') or {}, ensure_ascii=False, sort_keys=True, default=str))
    return 0


if __name__ == '__main__':
    sys.exit(main())
