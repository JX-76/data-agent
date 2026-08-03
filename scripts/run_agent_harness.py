# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import codecs
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.join(ROOT, 'src'))

from agent_harness import AgentHarness, _sanitize
from eval_baseline import EvalBaseline, evaluate_gate

CASES_DIR = os.path.join(ROOT, 'harness', 'cases')
DEFAULT_SUITE_PATHS = {
    'base': os.path.join(CASES_DIR, 'base.jsonl'),
    'phase8': os.path.join(CASES_DIR, 'phase8.jsonl'),
    'phase11': os.path.join(CASES_DIR, 'phase11_runtime.jsonl'),
    'external_tools': os.path.join(CASES_DIR, 'external_tools.jsonl'),
    'mcp_adapter': os.path.join(CASES_DIR, 'mcp_adapter.jsonl'),
    'ecommerce': os.path.join(CASES_DIR, 'ecommerce_smoke.jsonl'),
    'governance': os.path.join(CASES_DIR, 'ecommerce_governance.jsonl'),
    'unsupported': os.path.join(CASES_DIR, 'ecommerce_unsupported.jsonl'),
    'execution_modes': os.path.join(CASES_DIR, 'execution_modes.jsonl'),
    'react_controlled': os.path.join(CASES_DIR, 'react_controlled.jsonl'),
    'regression_core': os.path.join(CASES_DIR, 'regression_core.jsonl'),
    'multiturn_core': os.path.join(CASES_DIR, 'multiturn_core.jsonl'),
    'tool_calling_core': os.path.join(CASES_DIR, 'tool_calling_core.jsonl'),
    'security_governance_core': os.path.join(CASES_DIR, 'security_governance_core.jsonl'),
}


def _print_report(report):
    metrics = _sanitize(report.get('metrics', {}))
    failures = _sanitize(report.get('failures', []))
    print('AGENT_HARNESS suite=%s total=%d passed=%d failed=%d' % (
        report.get('suite'), metrics.get('total', 0), metrics.get('passed', 0), metrics.get('failed', 0),
    ))
    print('METRICS %s' % json.dumps(metrics, ensure_ascii=False, sort_keys=True, default=str))
    # Only print failure IDs and types to keep output readable
    failure_summary = []
    for f in (failures or [])[:10]:
        if isinstance(f, dict):
            failure_summary.append({
                'id': f.get('id', ''),
                'failure_type': f.get('failure_type', ''),
                'errors': f.get('errors', [])[:3],
            })
        else:
            failure_summary.append(f)
    print('FAILURES %s' % json.dumps(_sanitize(failure_summary), ensure_ascii=False, sort_keys=True, default=str))


def _load_suite_cases(harness, suite):
    if os.path.isfile(suite):
        return harness.load_cases(suite)
    if suite == 'all':
        cases = []
        for name in ['base', 'phase8', 'phase11', 'external_tools', 'mcp_adapter', 'ecommerce', 'governance', 'unsupported', 'execution_modes', 'react_controlled', 'regression_core', 'multiturn_core', 'tool_calling_core', 'security_governance_core']:
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
    _print_report(report)

    baseline = EvalBaseline(
        name='agent-harness',
        metrics={
            'pass_rate_min': 0.90,
            'contract_pass_rate_min': 0.98,
            'route_accuracy_min': 0.80,
            'task_type_accuracy_min': 0.80,
            'trace_complete_rate_min': 0.80,
        },
        metadata={'suite': suite},
    )
    gate = evaluate_gate(report, baseline).to_dict()
    print('GATE %s' % json.dumps(gate, ensure_ascii=False, sort_keys=True, default=str))
    if not gate['passed']:
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
