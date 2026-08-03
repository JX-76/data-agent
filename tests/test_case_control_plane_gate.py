# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, 'scripts')
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import run_case_control_plane_gate


def test_case_control_plane_gate_has_blocking_thresholds_and_required_suites():
    assert run_case_control_plane_gate.CONTROL_THRESHOLDS == {
        'case_scope_isolation_rate': 1.0,
        'orchestration_safety_rate': 1.0,
        'claim_graduation_safety_rate': 1.0,
        'control_plane_test_pass_rate': 1.0,
    }
    assert run_case_control_plane_gate.CASE_CONTROL_METRICS == [
        'case_scope_isolation_rate',
        'orchestration_safety_rate',
        'claim_graduation_safety_rate',
        'control_plane_test_pass_rate',
    ]
    assert run_case_control_plane_gate.P0_CONTROL_TESTS == [
        'tests/test_evidence_bus_scope_m11.py',
        'tests/test_case_scoped_planning_and_claims.py',
        'tests/test_case_orchestrator.py',
        'tests/test_claim_graduation_policy.py',
    ]


def test_case_control_plane_gate_returns_blocking_report(monkeypatch):
    class Proc(object):
        returncode = 1
        def communicate(self):
            return (b'1 failed',)

    monkeypatch.setattr(run_case_control_plane_gate.subprocess, 'Popen', lambda *args, **kwargs: Proc())
    report, output = run_case_control_plane_gate.run_gate()

    assert output == '1 failed'
    assert report['passed'] is False
    assert report['failed_metrics']['case_scope_isolation_rate'] == 0.0
    assert report['contract'] == 'case_control_plane_gate_v1'
