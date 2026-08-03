# -*- coding: utf-8 -*-
"""Blocking P1 gate for external tool governance and execution envelopes."""
from __future__ import print_function

import json
import os
import subprocess
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
PY = sys.executable or 'py'
EXTERNAL_TOOL_TESTS = [
    'tests/test_external_tools.py',
    'tests/test_external_tool_governance_p1.py',
]
EXTERNAL_TOOL_THRESHOLDS = {
    'tool_contract_pass_rate': 1.0,
    'tool_error_envelope_safety_rate': 1.0,
    'tool_sensitive_data_guard_rate': 1.0,
    'tool_idempotency_retry_rate': 1.0,
}


def run_gate():
    started = time.time()
    env = os.environ.copy()
    env['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] = '1'
    env['PYTHONPATH'] = os.pathsep.join(filter(None, [os.path.join(ROOT, 'src'), env.get('PYTHONPATH')]))
    cmd = [PY, '-m', 'pytest', '-q'] + list(EXTERNAL_TOOL_TESTS)
    proc = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    output = proc.communicate()[0]
    if isinstance(output, bytes):
        output = output.decode('utf-8', 'replace')
    passed = proc.returncode == 0
    metrics = {
        'tool_contract_pass_rate': 1.0 if passed else 0.0,
        'tool_error_envelope_safety_rate': 1.0 if passed else 0.0,
        'tool_sensitive_data_guard_rate': 1.0 if passed else 0.0,
        'tool_idempotency_retry_rate': 1.0 if passed else 0.0,
    }
    failed = dict((name, value) for name, value in metrics.items()
                  if value < EXTERNAL_TOOL_THRESHOLDS[name])
    report = {
        'contract': 'external_tool_governance_gate_v1',
        'passed': not failed,
        'returncode': proc.returncode,
        'tests': list(EXTERNAL_TOOL_TESTS),
        'thresholds': dict(EXTERNAL_TOOL_THRESHOLDS),
        'metrics': metrics,
        'failed_metrics': failed,
        'elapsed_ms': int((time.time() - started) * 1000),
        'pytest_summary': (output or '').splitlines()[-1] if output else '',
    }
    return report, output


def main():
    report, output = run_gate()
    print('EXTERNAL_TOOL_GOVERNANCE_GATE %s' % json.dumps(report, ensure_ascii=False, sort_keys=True))
    if not report['passed']:
        print(output)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
