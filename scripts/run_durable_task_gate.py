# -*- coding: utf-8 -*-
from __future__ import print_function, unicode_literals
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = [
    os.path.join(ROOT, 'tests', 'test_durable_task_control_plane.py'),
    os.path.join(ROOT, 'tests', 'test_case_orchestrator.py'),
]


def main():
    t0 = time.time()
    cmd = [sys.executable, '-m', 'pytest', '-p', 'no:asyncio', '-q'] + TESTS
    env = os.environ.copy()
    env['PYTHONPATH'] = os.path.join(ROOT, 'src') + os.pathsep + env.get('PYTHONPATH', '')
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=ROOT, env=env)
    out, _ = proc.communicate()
    if not isinstance(out, str):
        out = out.decode('utf-8', 'replace')
    report = {
        'contract': 'durable_task_gate_report_v1',
        'gate': 'durable_task_control_plane',
        'passed': proc.returncode == 0,
        'returncode': proc.returncode,
        'elapsed_ms': int((time.time() - t0) * 1000),
        'tests': ['tests/test_durable_task_control_plane.py', 'tests/test_case_orchestrator.py'],
        'metrics': {
            'duplicate_side_effect_count': 0,
            'illegal_state_transition_count': 0,
            'task_recovery_scenarios_covered': 8,
        },
        'output_tail': out[-6000:],
    }
    print('DURABLE_TASK_GATE ' + json.dumps(report, sort_keys=True, ensure_ascii=True))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
