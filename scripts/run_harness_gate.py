# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.join(ROOT, 'src'))

from agent_harness import AgentHarness
from eval_baseline import EvalBaseline, evaluate_gate
from harness_diff import diff_snapshots
from harness_snapshot import load_json, normalize_report, save_json, snapshot_path
from run_context_budget_harness import main as run_context_budget_harness
from check_py27_compat import DEFAULT_PATHS as PY27_COMPAT_PATHS
from check_py27_compat import scan as scan_py27_compat

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
    suite = argv[0] if argv else 'all'
    baseline_path = argv[1] if len(argv) > 1 else snapshot_path(ROOT, suite)
    harness = AgentHarness()
    cases = _load_suite_cases(harness, suite)
    report = harness.run_suite(suite, cases)
    metrics = report.get('metrics') or {}
    baseline = EvalBaseline(
        name='agent-harness-regression-gate',
        metrics={
            'pass_rate_min': 0.90,
            'contract_pass_rate_min': 0.98,
            'route_accuracy_min': 0.80,
            'task_type_accuracy_min': 0.80,
            'trace_complete_rate_min': 0.80,
        },
        metadata={'suite': suite},
    )
    # Phase 21-D: react governance thresholds apply only to suites that
    # actually exercise the react branch (avoid null-metric false failures).
    if metrics.get('react_observation_total'):
        baseline.metrics['react_observation_coverage_rate_min'] = 0.95
        baseline.metrics['react_action_accuracy_min'] = 0.90
        baseline.metrics['react_quarantine_rate_max'] = 0.30
    gate = evaluate_gate(report, baseline).to_dict()

    context_harness_code = run_context_budget_harness([])
    if context_harness_code:
        gate['passed'] = False
        gate.setdefault('failures', []).append({
            'metric': 'context_budget_harness',
            'actual': context_harness_code,
            'threshold': 0,
            'operator': '==',
        })

    py27_findings = scan_py27_compat(PY27_COMPAT_PATHS)
    if py27_findings:
        gate['passed'] = False
        for item in py27_findings[:20]:
            gate.setdefault('failures', []).append({
                'metric': 'py27_compat',
                'path': os.path.relpath(item.get('path'), ROOT),
                'line': item.get('line'),
                'rule': item.get('rule'),
                'text': item.get('text'),
            })

    diff = None
    if os.path.exists(baseline_path):
        current = normalize_report(report)
        snapshot = load_json(baseline_path)
        diff = diff_snapshots(snapshot, current)
        save_json(os.path.join(ROOT, 'harness', 'diffs', '%s_gate_diff.json' % suite), diff)
        if diff.get('breaking', 0) > 0:
            gate['passed'] = False
            gate.setdefault('failures', []).append({
                'metric': 'breaking_diff',
                'actual': diff.get('breaking', 0),
                'threshold': 0,
                'operator': '==',
            })
    else:
        gate['passed'] = False
        gate.setdefault('failures', []).append({'metric': 'snapshot', 'error': 'missing baseline snapshot: %s' % baseline_path})

    print('HARNESS_GATE suite=%s passed=%s total=%s pass_rate=%s' % (
        suite, gate.get('passed'), metrics.get('total'), metrics.get('pass_rate'),
    ))
    if diff is not None:
        print('DIFF changed=%d breaking=%d warning=%d info=%d' % (
            diff.get('changed', 0), diff.get('breaking', 0), diff.get('warning', 0), diff.get('info', 0),
        ))
    print('CONTEXT_BUDGET_HARNESS passed=%s' % (context_harness_code == 0))
    print('PY27_COMPAT checked=%d findings=%d' % (len(PY27_COMPAT_PATHS), len(py27_findings)))
    print('GATE %s' % json.dumps(gate, ensure_ascii=False, sort_keys=True, default=str))
    return 0 if gate.get('passed') else 1


if __name__ == '__main__':
    sys.exit(main())
