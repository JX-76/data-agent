# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, 'scripts')
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import run_full_quality_gate  # noqa: E402


def test_full_quality_gate_configuration_covers_core_gates():
    names = [gate['name'] for gate in run_full_quality_gate.GATES]
    assert names == ['core_pytest', 'release_v1_gate', 'agent_quality_gate', 'case_control_plane_gate', 'external_tool_governance_gate', 'multiturn_stress_50', 'release_100_gate']
    assert 'tests/test_production_runtime_readiness_s3.py' in run_full_quality_gate.CORE_PYTEST_ARGS


def test_full_quality_gate_extract_summary_covers_external_tool_governance_gate():
    text = 'hello\nEXTERNAL_TOOL_GOVERNANCE_GATE {"passed": true}\nREPORT x'
    assert run_full_quality_gate._extract_summary(text) == 'EXTERNAL_TOOL_GOVERNANCE_GATE {"passed": true}'


def test_full_quality_gate_extract_summary_covers_case_control_plane_gate():
    text = 'hello\nCASE_CONTROL_PLANE_GATE {"passed": true}\nREPORT x'
    assert run_full_quality_gate._extract_summary(text) == 'CASE_CONTROL_PLANE_GATE {"passed": true}'


def test_full_quality_gate_extract_summary_covers_multiturn_stress():
    text = 'hello\nMULTITURN_STRESS total=50 passed=50 failed=0 pass_rate=1.0000\nREPORT x'
    assert run_full_quality_gate._extract_summary(text) == 'MULTITURN_STRESS total=50 passed=50 failed=0 pass_rate=1.0000'


def test_full_quality_gate_extract_summary_prefers_quality_lines():
    text = 'hello\nAGENT_QUALITY_GATE total=3 passed=2 failed=1 score=0.8 pass_rate=0.6\nbye'
    assert run_full_quality_gate._extract_summary(text) == 'AGENT_QUALITY_GATE total=3 passed=2 failed=1 score=0.8 pass_rate=0.6'


def test_full_quality_gate_extract_summary_covers_release_100():
    text = 'hello\nRELEASE_100_GATE total=100 passed=99 failed=1 score=0.98 pass_rate=0.99\nREPORT x'
    assert run_full_quality_gate._extract_summary(text) == 'RELEASE_100_GATE total=100 passed=99 failed=1 score=0.98 pass_rate=0.99'


def test_full_quality_gate_extract_summary_falls_back_to_last_line():
    assert run_full_quality_gate._extract_summary('a\nb\nc') == 'c'


def _python_gate(tmpdir, name, statements):
    script = tmpdir.join(name)
    script.write('\n'.join(statements))
    return {
        'name': name,
        'cmd': [sys.executable, str(script)],
        'cwd': str(tmpdir),
        'timeout_seconds': 2,
    }


def test_full_quality_gate_reports_success_and_keeps_output_tail(tmpdir):
    spec = _python_gate(tmpdir, 'success_gate.py', [
        'from __future__ import print_function',
        'print("gate-ready")',
    ])
    result = run_full_quality_gate._run_gate(spec, forward_output=False)
    assert result['passed'] is True
    assert result['timed_out'] is False
    assert result['failure_reason'] is None
    assert 'gate-ready' in result['output']
    assert 'gate-ready' in result['last_output']
    assert result['started_ms'] <= result['ended_ms']


def test_full_quality_gate_times_out_and_records_last_output(tmpdir):
    spec = _python_gate(tmpdir, 'slow_gate.py', [
        'from __future__ import print_function',
        'import sys, time',
        'print("before-hang")',
        'sys.stdout.flush()',
        'time.sleep(10)',
    ])
    result = run_full_quality_gate._run_gate(spec, timeout_seconds=0.2, forward_output=False)
    assert result['passed'] is False
    assert result['timed_out'] is True
    assert result['failure_reason'] == 'timeout'
    assert result['termination_method'] is not None
    assert 'before-hang' in result['last_output']


def test_full_quality_gate_main_reports_timeout_fields(monkeypatch, tmpdir, capsys):
    spec = _python_gate(tmpdir, 'timeout_gate.py', [
        'import time',
        'print("timeout-marker")',
        'time.sleep(10)',
    ])
    monkeypatch.setattr(run_full_quality_gate, 'GATES', [spec])
    assert run_full_quality_gate.main(['--timeout-seconds', '0.2', '--no-forward-output']) == 1
    output = capsys.readouterr().out
    assert 'FULL_QUALITY_GATE_REPORT ' in output
    assert '"timed_out": true' in output


def test_full_quality_gate_accepts_only_filter():
    opts = run_full_quality_gate._parse_args(['--only', 'core_pytest,release_v1_gate', '--timeout-seconds', '3'])
    assert opts['only'] == 'core_pytest,release_v1_gate'
    assert opts['timeout_seconds'] == 3.0
