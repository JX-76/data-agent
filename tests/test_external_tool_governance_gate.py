# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, 'scripts')
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import run_external_tool_governance_gate


def test_external_tool_governance_gate_has_blocking_thresholds_and_suites():
    assert run_external_tool_governance_gate.EXTERNAL_TOOL_THRESHOLDS == {
        'tool_contract_pass_rate': 1.0,
        'tool_error_envelope_safety_rate': 1.0,
        'tool_sensitive_data_guard_rate': 1.0,
        'tool_idempotency_retry_rate': 1.0,
    }
    assert run_external_tool_governance_gate.EXTERNAL_TOOL_TESTS == [
        'tests/test_external_tools.py',
        'tests/test_external_tool_governance_p1.py',
    ]


def test_external_tool_governance_gate_fails_closed_when_a_suite_fails(monkeypatch):
    class Proc(object):
        returncode = 1
        def communicate(self):
            return (b'1 failed',)

    monkeypatch.setattr(run_external_tool_governance_gate.subprocess, 'Popen', lambda *args, **kwargs: Proc())
    report, output = run_external_tool_governance_gate.run_gate()

    assert output == '1 failed'
    assert report['passed'] is False
    assert set(report['failed_metrics']) == set(run_external_tool_governance_gate.EXTERNAL_TOOL_THRESHOLDS)
    assert report['contract'] == 'external_tool_governance_gate_v1'
