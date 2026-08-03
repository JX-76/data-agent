# -*- coding: utf-8 -*-
"""Blocking P0 control-plane gate for case evidence and claim governance."""
from __future__ import print_function

import json
import os
import subprocess
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
PY = sys.executable or 'py'
P0_CONTROL_TESTS = [
    'tests/test_evidence_bus_scope_m11.py',
    'tests/test_case_scoped_planning_and_claims.py',
    'tests/test_case_orchestrator.py',
    'tests/test_claim_graduation_policy.py',
]
CASE_CONTROL_METRICS = [
    'case_scope_isolation_rate',
    'orchestration_safety_rate',
    'claim_graduation_safety_rate',
    'control_plane_test_pass_rate',
]
CONTROL_THRESHOLDS = dict((name, 1.0) for name in CASE_CONTROL_METRICS)


def run_gate():
    started = time.time()
    env = os.environ.copy()
    env['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] = '1'
    env['PYTHONPATH'] = os.pathsep.join(filter(None, [os.path.join(ROOT, 'src'), env.get('PYTHONPATH')]))
    cmd = [PY, '-m', 'pytest', '-q'] + list(P0_CONTROL_TESTS)
    proc = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    output = proc.communicate()[0]
    if isinstance(output, bytes):
        output = output.decode('utf-8', 'replace')
    passed = proc.returncode == 0
    metrics = dict((name, 1.0 if passed else 0.0) for name in CASE_CONTROL_METRICS)
    failed = dict((name, value) for name, value in metrics.items()
                  if value < CONTROL_THRESHOLDS[name])
    report = {
        'contract': 'case_control_plane_gate_v1',
        'passed': not failed,
        'returncode': proc.returncode,
        'tests': list(P0_CONTROL_TESTS),
        'thresholds': dict(CONTROL_THRESHOLDS),
        'metric_contract': list(CASE_CONTROL_METRICS),
        'metrics': metrics,
        'failed_metrics': failed,
        'elapsed_ms': int((time.time() - started) * 1000),
        'pytest_summary': (output or '').splitlines()[-1] if output else '',
    }
    return report, output


def main():
    report, output = run_gate()
    print('CASE_CONTROL_PLANE_GATE %s' % json.dumps(report, ensure_ascii=False, sort_keys=True))
    if not report['passed']:
        print(output)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
