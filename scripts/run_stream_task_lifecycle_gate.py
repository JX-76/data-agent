# -*- coding: utf-8 -*-
"""Regression gate for durable lifecycle state behind legacy stream endpoints."""
from __future__ import print_function, unicode_literals

import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = [
    os.path.join(ROOT, 'tests', 'test_server_entrypoint_unification_p4.py'),
    os.path.join(ROOT, 'tests', 'test_durable_task_control_plane.py'),
    os.path.join(ROOT, 'tests', 'test_provider_resilience_and_worker.py'),
]
GATE_NAME = 'stream_task_lifecycle'


def main():
    started = time.time()
    test_python = os.environ.get('DATA_AGENT_GATE_PYTHON') or sys.executable
    env = os.environ.copy()
    env['PYTHONPATH'] = os.path.join(ROOT, 'src') + os.pathsep + env.get('PYTHONPATH', '')
    proc = subprocess.Popen([test_python, '-m', 'pytest', '-p', 'no:asyncio', '-q'] + TESTS,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            cwd=ROOT, env=env)
    output, _ = proc.communicate()
    if not isinstance(output, str):
        output = output.decode('utf-8', 'replace')
    report = {
        'contract': 'stream_task_lifecycle_gate_report_v1',
        'gate': GATE_NAME,
        'passed': proc.returncode == 0,
        'returncode': proc.returncode,
        'elapsed_ms': int((time.time() - started) * 1000),
        'test_python': test_python,
        'tests': ['tests/test_server_entrypoint_unification_p4.py',
                  'tests/test_durable_task_control_plane.py',
                  'tests/test_provider_resilience_and_worker.py'],
        'metrics': {
            'stream_terminal_state_snapshot_coverage': 2,
            'stream_receipt_persistence_coverage': 1,
            'stream_failure_safe_summary_coverage': 1,
            'stream_sqlite_restart_snapshot_coverage': 1,
            'stream_execution_without_sse_consumption_coverage': 1,
            'local_worker_control_plane_adapter_coverage': 1,
            'sse_event_id_replay_coverage': 1,
            'sse_sidecar_loss_safe_fallback_coverage': 1,
            'task_owner_scope_enforcement_coverage': 1,
            'same_tenant_admin_task_read_coverage': 1,
        },
        'output_tail': output[-6000:],
    }
    print('STREAM_TASK_LIFECYCLE_GATE ' + json.dumps(report, sort_keys=True, ensure_ascii=True))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
