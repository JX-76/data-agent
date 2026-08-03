# -*- coding: utf-8 -*-
"""Stable snapshot normalization for Agent Harness reports.

Python 2.7 compatible. Removes volatile runtime fields and keeps behaviorally
meaningful fields for regression comparison.
"""
from __future__ import unicode_literals

import codecs
import json
import os

from trace_completeness import validate_trace_completeness

VOLATILE_KEYS = set([
    'timestamp_ms', 'elapsed_ms', 'duration_ms', 'trace_id', 'task_id',
    'parent_task_id', 'session_id', 'started_at', 'finished_at', 'created_at',
])


def ensure_dir(path):
    if path and not os.path.isdir(path):
        os.makedirs(path)


def load_json(path):
    with codecs.open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def _to_unicode(value):
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            out[_to_unicode(key)] = _to_unicode(item)
        return out
    if isinstance(value, list):
        return [_to_unicode(item) for item in value]
    if isinstance(value, tuple):
        return [_to_unicode(item) for item in value]
    try:
        if isinstance(value, bytes):
            return value.decode('utf-8')
    except Exception:
        pass
    return value


def save_json(path, data):
    ensure_dir(os.path.dirname(path))
    safe_data = _to_unicode(data)
    text = json.dumps(safe_data, ensure_ascii=False, indent=2, sort_keys=True, default=str)
    if isinstance(text, bytes):
        try:
            text = text.decode('utf-8')
        except Exception:
            text = text.decode('utf-8', 'ignore')
    with codecs.open(path, 'w', encoding='utf-8') as f:
        f.write(text)


def _event_names(trace):
    names = []
    for item in trace or []:
        if isinstance(item, dict):
            name = item.get('name') or item.get('stage')
            if name:
                names.append(name)
    return names


def _strip_volatile(value):
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if key in VOLATILE_KEYS:
                continue
            out[key] = _strip_volatile(item)
        return out
    if isinstance(value, list):
        return [_strip_volatile(item) for item in value]
    if isinstance(value, tuple):
        return [_strip_volatile(item) for item in value]
    return value


def _sql_shape(sql):
    if not sql:
        return None
    text = ' '.join(str(sql).replace('\n', ' ').split()).lower()
    # Keep a stable compact representation without exact spacing.
    return text


def normalize_case_result(item):
    result = item.get('result') or {}
    expected = item.get('expected') or {}
    diagnostics = result.get('diagnostics') or {}
    analysis = result.get('analysis') or {}
    report = result.get('report')
    trace = item.get('trace') or []
    normalized = {
        'id': item.get('id') or (item.get('case') or {}).get('id'),
        'query': item.get('query') or result.get('query') or (item.get('case') or {}).get('query'),
        'category': item.get('category') or (item.get('case') or {}).get('category'),
        'passed': bool(item.get('passed')),
        'failure_type': item.get('failure_type'),
        'errors': item.get('errors') or [],
        'expected': _strip_volatile(expected),
        'actual': {
            'status': result.get('status'),
            'intent': result.get('intent'),
            'task_type': result.get('task_type'),
            'metric': result.get('metric'),
            'dimensions': result.get('dimensions') or [],
            'sql_shape': _sql_shape(result.get('sql')),
            'has_results': result.get('results') is not None,
            'has_analysis': bool(analysis),
            'has_report': bool(report),
            'contract_version': diagnostics.get('response_contract'),
            'failure_stage': diagnostics.get('failure_stage'),
            'status_diagnostic': diagnostics.get('status'),
            'prompt_chain': result.get('prompt_chain') or diagnostics.get('prompt_chain') or [],
            'requires_human_review': result.get('requires_human_review'),
            'approval_status': result.get('approval_status'),
            'risk_level': result.get('risk_level'),
        },
        'trace_events': _event_names(trace),
        'dag_trace': validate_trace_completeness(result.get('status'), trace),
    }
    return _strip_volatile(normalized)


def normalize_report(report):
    metrics = report.get('metrics') or {}
    results = report.get('results') or []
    snapshot = {
        'suite': report.get('suite'),
        'metrics': _strip_volatile(metrics),
        'cases': [normalize_case_result(item) for item in results],
    }
    snapshot['cases'] = sorted(snapshot['cases'], key=lambda x: x.get('id') or '')
    return snapshot


def snapshot_path(root, suite):
    return os.path.join(root, 'harness', 'snapshots', '%s_latest.json' % suite)


def report_path(root, suite):
    return os.path.join(root, 'harness', 'reports', '%s_latest.json' % suite)


__all__ = ['normalize_report', 'normalize_case_result', 'load_json', 'save_json', 'snapshot_path', 'report_path']
