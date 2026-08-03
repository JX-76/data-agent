# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'src')
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from benchmark_scorer import score_suite  # noqa: E402


def _item(case_id, category, passed, failure_type=None):
    return {
        'id': case_id,
        'category': category,
        'passed': passed,
        'failure_type': failure_type,
        'expected': {'status': 'ok', 'task_type': 'descriptive'},
        'result': {'status': 'ok', 'task_type': 'descriptive'},
    }


def test_score_suite_aggregates_category_metrics_and_failure_taxonomy():
    report = score_suite([
        {'case': {'id': 'a', 'category': 'comparison'}, **_item('a', 'comparison', False, 'task_type_mismatch')},
        {'case': {'id': 'b', 'category': 'comparison'}, **_item('b', 'comparison', True)},
        {'case': {'id': 'c', 'category': 'anomaly'}, **_item('c', 'anomaly', False, 'status_mismatch')},
    ])

    comparison = report['category_metrics']['comparison']
    assert comparison['total'] == 2
    assert comparison['passed'] == 1
    assert comparison['failed'] == 1
    assert comparison['pass_rate'] == 0.5
    assert comparison['failure_breakdown'] == {'planning_error': 1}

    anomaly = report['category_metrics']['anomaly']
    assert anomaly['pass_rate'] == 0.0
    assert anomaly['failure_breakdown'] == {'routing_error': 1}
    assert report['failure_stage_breakdown'] == {'planning': 1, 'routing': 1}


def test_score_suite_adds_optional_quality_metrics_without_changing_core_scores():
    result = {
        'id': 'tool-1', 'category': 'follow_up', 'passed': True,
        'duration_ms': 42,
        'case': {
            'id': 'tool-1', 'category': 'follow_up',
            'tags': ['multiturn'], 'tool_id': 'warehouse.query_sql',
            'expected': {'status': 'ok', 'intent': 'metric_query',
                         'metric': 'gmv', 'dimensions': []},
        },
        'expected': {'status': 'ok', 'intent': 'metric_query',
                     'metric': 'gmv', 'dimensions': []},
        'result': {
            'status': 'ok', 'intent': 'metric_query', 'metric': 'gmv',
            'dimensions': [], 'tool_id': 'warehouse.query_sql',
            'trace_id': 'trace-1', 'task_id': 'task-1', 'session_id': 'session-1',
        },
        'trace': [{'name': 'route'}, {'name': 'complete'}],
    }
    report = score_suite([result])
    assert report['terminal_status_accuracy'] == 1.0
    assert report['intent_accuracy'] == 1.0
    assert report['slot_accuracy'] == 1.0
    assert report['tool_call_accuracy'] == 1.0
    assert report['resume_success_rate'] == 1.0
    assert report['multiturn_completion_rate'] == 1.0
    assert report['avg_latency_ms'] == 42.0
    assert report['p95_latency_ms'] == 42
    case_score = report['case_scores'][0]
    assert case_score['trace_events'] == ['route', 'complete']
    assert case_score['trace_id'] == 'trace-1'
