# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import argparse
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
SRC = os.path.join(ROOT, 'src')
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from agent_harness import AgentHarness, summarize_trace_quality
from release_dashboard import compute_dashboard, format_dashboard_text


def _load_cases(path):
    harness = AgentHarness()
    return harness.load_cases(path)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Run trace completeness gate')
    parser.add_argument('--cases', default=os.path.join(ROOT, 'harness', 'cases', 'release_v1.jsonl'))
    parser.add_argument('--min-complete-rate', type=float, default=0.90)
    parser.add_argument('--critical-only', action='store_true')
    parser.add_argument('--json', dest='json_output', action='store_true')
    args = parser.parse_args(argv)

    harness = AgentHarness()
    cases = _load_cases(args.cases)
    results = []
    for case in cases:
        case_type = (case.get('type') or case.get('case_type') or '').lower()
        if args.critical_only and case_type and case_type not in ('', 'analysis', 'agent'):
            continue
        results.append(harness.run_case(case))

    trace_quality = summarize_trace_quality(results)
    report = {
        'contract': 'trace_completeness_gate_v1',
        'cases_path': args.cases,
        'results_count': len(results),
        'trace_quality': trace_quality,
        'dashboard': compute_dashboard(results, {'total': len(results)}, trace_quality=trace_quality),
        'passed': trace_quality.get('complete_rate', 0) >= args.min_complete_rate,
        'min_complete_rate': args.min_complete_rate,
    }

    if args.json_output:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    else:
        print('Trace completeness gate')
        print('=' * 80)
        print(format_dashboard_text(report['dashboard']))
        print('Trace completeness: %(complete_count)s/%(evaluated_count)s complete (%(complete_rate).1f%%)' % {
            'complete_count': trace_quality.get('complete_count', 0),
            'evaluated_count': trace_quality.get('evaluated_count', 0),
            'complete_rate': trace_quality.get('complete_rate', 0) * 100,
        })
        if trace_quality.get('missing_node_breakdown'):
            print('Missing nodes: %s' % json.dumps(trace_quality.get('missing_node_breakdown'), ensure_ascii=False, sort_keys=True))
        if trace_quality.get('first_failure_breakdown'):
            print('First failures: %s' % json.dumps(trace_quality.get('first_failure_breakdown'), ensure_ascii=False, sort_keys=True))

    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
