# -*- coding: utf-8 -*-
"""Badcase extraction and storage helpers for quality gates.

Pure, file-backed utilities. They intentionally do not execute AgentFacade;
callers pass already-produced harness or quality reports.
"""
from __future__ import unicode_literals

import time


def _as_dict(value):
    return value if isinstance(value, dict) else {}


def _event_names(trace):
    names = []
    for item in trace or []:
        item = _as_dict(item)
        name = item.get('name') or item.get('stage')
        if name:
            names.append(name)
    return names


def infer_failure_stage(failure_type, trace_events=None):
    """Map semantic failure classes to stable stage labels."""
    if failure_type in ('routing_error',):
        return 'routing'
    if failure_type in ('planning_error',):
        return 'planning'
    if failure_type in ('execution_error',):
        return 'execution'
    if failure_type in ('analysis_error',):
        return 'analysis'
    if failure_type in ('report_error', 'contract_error'):
        return 'report'
    if failure_type in ('governance_error',):
        return 'governance'
    events = trace_events or []
    if 'governance' in events:
        return 'unknown_after_governance'
    return 'unknown'


def badcase_from_evaluated(item, suite=None, created_at=None):
    """Build a stable badcase record from an AgentHarness evaluated result."""
    item = _as_dict(item)
    case = _as_dict(item.get('case'))
    result = _as_dict(item.get('result'))
    expected = _as_dict(item.get('expected') or case.get('expected'))
    trace = item.get('trace') or []
    trace_events = _event_names(trace)
    failure_type = item.get('failure_type')
    return {
        'case_id': item.get('id') or case.get('id'),
        'suite': suite or case.get('suite'),
        'category': item.get('category') or case.get('category'),
        'query': item.get('query') or case.get('query') or result.get('query'),
        'expected': expected,
        'actual': {
            'status': result.get('status'),
            'intent': result.get('intent'),
            'task_type': result.get('task_type'),
            'metric': result.get('metric'),
            'dimensions': result.get('dimensions') or [],
        },
        'failure_type': failure_type,
        'stage': infer_failure_stage(failure_type, trace_events),
        'errors': item.get('errors') or [],
        'trace_id': result.get('trace_id'),
        'task_id': result.get('task_id'),
        'session_id': result.get('session_id'),
        'trace_events': trace_events,
        'created_at': created_at or int(time.time()),
        'replay_hint': 'py -3 scripts/replay_harness_failure.py %s %s' % (
            suite or 'benchmark_r20', item.get('id') or case.get('id')),
    }


def extract_badcases_from_harness_report(report):
    report = _as_dict(report)
    suite = report.get('suite')
    rows = []
    for item in report.get('results') or []:
        if not _as_dict(item).get('passed'):
            rows.append(badcase_from_evaluated(item, suite=suite))
    return rows


def extract_badcases_from_quality_report(report):
    """Build compact badcases from benchmark quality report case_scores.

    Quality reports do not include full actual responses, so this produces a
    compact replay-oriented record.
    """
    report = _as_dict(report)
    suite = report.get('suite') or 'benchmark_r20'
    rows = []
    for item in report.get('case_scores') or []:
        item = _as_dict(item)
        if item.get('passed'):
            continue
        failure_type = item.get('semantic_failure_type') or item.get('raw_failure_type')
        rows.append({
            'case_id': item.get('id'),
            'suite': suite,
            'category': None,
            'query': None,
            'expected': {},
            'actual': {
                'status_accuracy': item.get('status_accuracy'),
                'task_type_accuracy': item.get('task_type_accuracy'),
                'metric_accuracy': item.get('metric_accuracy'),
                'dimension_accuracy': item.get('dimension_accuracy'),
            },
            'failure_type': failure_type,
            'stage': infer_failure_stage(failure_type),
            'errors': [],
            'trace_id': None,
            'task_id': None,
            'session_id': None,
            'trace_events': [],
            'created_at': int(time.time()),
            'replay_hint': 'py -3 scripts/replay_harness_failure.py %s %s' % (suite, item.get('id')),
        })
    return rows


def summarize_badcases(rows):
    rows = rows or []
    by_failure_type = {}
    by_stage = {}
    for row in rows:
        ft = _as_dict(row).get('failure_type') or 'unknown'
        st = _as_dict(row).get('stage') or 'unknown'
        by_failure_type[ft] = by_failure_type.get(ft, 0) + 1
        by_stage[st] = by_stage.get(st, 0) + 1
    return {'total': len(rows), 'by_failure_type': by_failure_type, 'by_stage': by_stage}


__all__ = [
    'infer_failure_stage', 'badcase_from_evaluated',
    'extract_badcases_from_harness_report', 'extract_badcases_from_quality_report',
    'summarize_badcases',
]
