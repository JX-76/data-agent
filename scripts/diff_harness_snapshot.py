# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.join(ROOT, 'src'))

from agent_harness import AgentHarness
from harness_diff import diff_snapshots
from harness_snapshot import load_json, normalize_report, save_json, snapshot_path

CASES_DIR = os.path.join(ROOT, 'harness', 'cases')
DEFAULT_SUITE_PATHS = {
    'base': os.path.join(CASES_DIR, 'base.jsonl'),
    'phase8': os.path.join(CASES_DIR, 'phase8.jsonl'),
    'ecommerce': os.path.join(CASES_DIR, 'ecommerce_smoke.jsonl'),
    'workflows': os.path.join(CASES_DIR, 'workflows.jsonl'),
}


def _load_suite_cases(harness, suite):
    if os.path.isfile(suite):
        return harness.load_cases(suite)
    if suite == 'all':
        cases = []
        for name in ['base', 'phase8', 'ecommerce']:
            path = DEFAULT_SUITE_PATHS.get(name)
            if path and os.path.exists(path):
                cases.extend(harness.load_cases(path))
        return cases
    path = DEFAULT_SUITE_PATHS.get(suite)
    if path and os.path.exists(path):
        return harness.load_cases(path)
    raise IOError('Unknown suite or missing case file: %s' % suite)


def _summarize_changes(changes, limit=10):
    out = []
    for item in changes[:limit]:
        out.append({
            'id': item.get('id'),
            'change_type': item.get('change_type'),
            'severity': item.get('severity'),
            'field': item.get('field'),
        })
    return out


def main(argv=None):
    argv = argv or sys.argv[1:]
    suite = argv[0] if argv else 'base'
    baseline_path = argv[1] if len(argv) > 1 else snapshot_path(ROOT, suite)
    if not os.path.exists(baseline_path):
        print('Missing baseline snapshot: %s' % baseline_path)
        return 2
    baseline = load_json(baseline_path)
    harness = AgentHarness()
    cases = _load_suite_cases(harness, suite)
    report = harness.run_suite(suite, cases)
    current = normalize_report(report)
    diff = diff_snapshots(baseline, current)
    diff_dir = os.path.join(ROOT, 'harness', 'diffs')
    out_path = os.path.join(diff_dir, '%s_latest_diff.json' % suite)
    save_json(out_path, diff)
    print('HARNESS_DIFF suite=%s changed=%d breaking=%d warning=%d info=%d' % (
        suite, diff.get('changed', 0), diff.get('breaking', 0), diff.get('warning', 0), diff.get('info', 0),
    ))
    print('CHANGES %s' % json.dumps(_summarize_changes(diff.get('changes') or []), ensure_ascii=False, sort_keys=True, default=str))
    print('DIFF_REPORT %s' % out_path)
    if diff.get('breaking', 0) > 0:
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
