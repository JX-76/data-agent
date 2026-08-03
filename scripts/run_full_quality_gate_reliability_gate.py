# -*- coding: utf-8 -*-
"""Reliability contract gate for scripts/run_full_quality_gate.py.

This script uses deterministic child commands to verify normal, failing and
hanging child-gate behavior without running the full product quality suite.
"""
from __future__ import print_function

import json
import os
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
SCRIPTS = os.path.join(ROOT, 'scripts')
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import run_full_quality_gate  # noqa: E402


def _write_script(tmpdir, name, lines):
    path = os.path.join(tmpdir, name)
    with open(path, 'w') as fh:
        fh.write('\n'.join(lines))
    return path


def _spec(tmpdir, name, lines):
    return {
        'name': name,
        'cmd': [sys.executable, _write_script(tmpdir, name + '.py', lines)],
        'cwd': tmpdir,
        'timeout_seconds': 2,
    }


def _check(condition, failures, code, detail):
    if not condition:
        failures.append({'code': code, 'detail': detail})


def main():
    tmpdir = tempfile.mkdtemp(prefix='full_quality_gate_reliability_')
    specs = [
        _spec(tmpdir, 'ok_gate', [
            'from __future__ import print_function',
            'print("OK_GATE_SUMMARY passed")',
        ]),
        _spec(tmpdir, 'fail_gate', [
            'from __future__ import print_function',
            'import sys',
            'print("FAIL_GATE_SUMMARY failed")',
            'sys.exit(7)',
        ]),
        _spec(tmpdir, 'timeout_gate', [
            'from __future__ import print_function',
            'import sys, time',
            'print("TIMEOUT_GATE_BEFORE_SLEEP")',
            'sys.stdout.flush()',
            'time.sleep(10)',
        ]),
    ]
    results = [
        run_full_quality_gate._run_gate(specs[0], forward_output=False),
        run_full_quality_gate._run_gate(specs[1], forward_output=False),
        run_full_quality_gate._run_gate(specs[2], timeout_seconds=0.2, forward_output=False),
    ]
    by_name = dict((item['name'], item) for item in results)
    failures = []
    _check(by_name['ok_gate']['passed'] is True, failures, 'ok_gate_not_passed', by_name['ok_gate'])
    _check(by_name['fail_gate']['passed'] is False and by_name['fail_gate']['failure_reason'] == 'nonzero_exit',
           failures, 'fail_gate_not_classified', by_name['fail_gate'])
    _check(by_name['timeout_gate']['timed_out'] is True and by_name['timeout_gate']['failure_reason'] == 'timeout',
           failures, 'timeout_gate_not_classified', by_name['timeout_gate'])
    _check('TIMEOUT_GATE_BEFORE_SLEEP' in by_name['timeout_gate'].get('last_output', ''),
           failures, 'timeout_tail_missing', by_name['timeout_gate'].get('last_output'))
    _check(by_name['timeout_gate'].get('termination_method') is not None,
           failures, 'termination_method_missing', by_name['timeout_gate'])

    report = {
        'suite': 'full_quality_gate_reliability_gate',
        'total': len(results),
        'passed': len(results) - len(failures),
        'failed': len(failures),
        'failures': failures,
        'results': [
            {
                'name': item['name'],
                'passed': item['passed'],
                'returncode': item['returncode'],
                'timed_out': item['timed_out'],
                'failure_reason': item['failure_reason'],
                'elapsed_ms': item['elapsed_ms'],
                'last_output': item.get('last_output') or '',
                'termination_method': item.get('termination_method'),
            }
            for item in results
        ],
    }
    print('FULL_QUALITY_GATE_RELIABILITY_GATE %s' % json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
