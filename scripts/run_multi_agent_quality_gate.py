# -*- coding: utf-8 -*-
from __future__ import print_function, unicode_literals

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'src')
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from supervisor_runtime import SupervisorRuntime


def load_cases(path):
    cases = []
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    return cases


def evaluate_case(case):
    runtime = SupervisorRuntime(max_nodes=20, max_steps=50, semaphore_limit=3, retry_limit=1)
    result = runtime.run(case.get('tasks') or [], trace_id='multi-agent-%s' % case.get('id'))
    failures = []
    if result.get('status') != case.get('expected_status'):
        failures.append({'field': 'status', 'expected': case.get('expected_status'), 'actual': result.get('status')})
    for node_id, expected_state in (case.get('expected_node_states') or {}).items():
        actual_state = (result.get('node_states') or {}).get(node_id)
        if actual_state != expected_state:
            failures.append({'field': 'node_state.%s' % node_id, 'expected': expected_state, 'actual': actual_state})
    if case.get('expect_trace_complete') is True and not (result.get('metrics') or {}).get('trace_complete'):
        failures.append({'field': 'trace_complete', 'expected': True, 'actual': (result.get('metrics') or {}).get('trace_complete')})
    expected_error = case.get('expected_error')
    if expected_error:
        found = any(e.get('error') == expected_error for e in (result.get('errors') or []))
        if not found:
            failures.append({'field': 'errors', 'expected_error': expected_error, 'actual': result.get('errors')})
    return {'id': case.get('id'), 'passed': not failures, 'failures': failures, 'result': result}


def summarize(rows):
    total = len(rows)
    passed = len([r for r in rows if r.get('passed')])
    trace_complete = 0
    invalid_dag = 0
    infinite_loop = 0
    worker_selection_ok = 0
    for row in rows:
        result = row.get('result') or {}
        if (result.get('metrics') or {}).get('trace_complete'):
            trace_complete += 1
        if any((e.get('error') or '').startswith('dag_') for e in (result.get('errors') or [])):
            invalid_dag += 1
        if any(e.get('error') == 'max_steps_exceeded' for e in (result.get('errors') or [])):
            infinite_loop += 1
        if not any(f.get('field', '').startswith('node_state.') for f in row.get('failures') or []):
            worker_selection_ok += 1
    return {
        'case_count': total,
        'passed': passed,
        'failed': total - passed,
        'pass_rate': float(passed) / float(total or 1),
        'trace_complete_rate': float(trace_complete) / float(total or 1),
        'invalid_dag_case_count': invalid_dag,
        'infinite_loop_case_count': infinite_loop,
        'worker_selection_accuracy': float(worker_selection_ok) / float(total or 1),
    }


def main():
    case_path = os.path.join(ROOT, 'harness', 'cases', 'multi_agent_core.jsonl')
    report_path = os.path.join(ROOT, 'harness', 'reports', 'multi_agent_quality_report.json')
    rows = [evaluate_case(c) for c in load_cases(case_path)]
    metrics = summarize(rows)
    failures = [r for r in rows if not r.get('passed')]
    status = 'passed' if not failures and metrics['pass_rate'] >= 1.0 and metrics['infinite_loop_case_count'] == 0 else 'failed'
    report = {'status': status, 'metrics': metrics, 'failures': failures, 'case_path': case_path}
    parent = os.path.dirname(report_path)
    if not os.path.isdir(parent):
        os.makedirs(parent)
    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2, sort_keys=True)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if status == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
