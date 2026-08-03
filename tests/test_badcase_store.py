# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'src')
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from badcase_store import (  # noqa: E402
    badcase_from_evaluated,
    extract_badcases_from_harness_report,
    extract_badcases_from_quality_report,
    infer_failure_stage,
    summarize_badcases,
)


def test_infer_failure_stage_maps_core_failure_types():
    assert infer_failure_stage('routing_error') == 'routing'
    assert infer_failure_stage('planning_error') == 'planning'
    assert infer_failure_stage('execution_error') == 'execution'
    assert infer_failure_stage('analysis_error') == 'analysis'
    assert infer_failure_stage('contract_error') == 'report'
    assert infer_failure_stage('governance_error') == 'governance'
    assert infer_failure_stage(None, ['governance']) == 'unknown_after_governance'


def test_badcase_from_evaluated_extracts_stable_fields():
    item = {
        'id': 'case_1',
        'category': 'comparison',
        'expected': {'status': 'ok'},
        'failure_type': 'planning_error',
        'errors': ['task_type expected=comparison got=descriptive'],
        'result': {'status': 'ok', 'task_type': 'descriptive', 'trace_id': 'trace-1', 'task_id': 'task-1', 'session_id': 'session-1'},
        'trace': [{'name': 'plan'}, {'stage': 'governance'}],
    }
    row = badcase_from_evaluated(item, suite='benchmark_r20', created_at=123)
    assert row['case_id'] == 'case_1'
    assert row['suite'] == 'benchmark_r20'
    assert row['stage'] == 'planning'
    assert row['trace_events'] == ['plan', 'governance']
    assert row['replay_hint'].startswith('py -3 scripts/replay_harness_failure.py benchmark_r20 case_1')


def test_extract_badcases_from_harness_report_uses_failed_cases_only():
    report = {
        'suite': 'demo',
        'results': [
            {'id': 'ok_1', 'passed': True, 'result': {'status': 'ok'}},
            {'id': 'bad_1', 'passed': False, 'failure_type': 'routing_error', 'result': {'status': 'blocked'}},
        ],
    }
    rows = extract_badcases_from_harness_report(report)
    assert len(rows) == 1
    assert rows[0]['case_id'] == 'bad_1'
    assert rows[0]['stage'] == 'routing'


def test_extract_badcases_from_quality_report_builds_compact_rows():
    report = {
        'suite': 'benchmark_r20',
        'case_scores': [
            {'id': 'bad_1', 'passed': False, 'raw_failure_type': 'planning_error', 'semantic_failure_type': 'planning_error'},
            {'id': 'ok_1', 'passed': True},
        ],
    }
    rows = extract_badcases_from_quality_report(report)
    assert len(rows) == 1
    assert rows[0]['case_id'] == 'bad_1'
    assert rows[0]['stage'] == 'planning'


def test_summarize_badcases_groups_by_stage_and_failure_type():
    summary = summarize_badcases([
        {'failure_type': 'routing_error', 'stage': 'routing'},
        {'failure_type': 'routing_error', 'stage': 'routing'},
        {'failure_type': 'planning_error', 'stage': 'planning'},
    ])
    assert summary['total'] == 3
    assert summary['by_failure_type']['routing_error'] == 2
    assert summary['by_stage']['routing'] == 2
