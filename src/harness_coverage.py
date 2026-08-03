# -*- coding: utf-8 -*-
"""Coverage metrics for harness case suites.

Python 2.7 compatible. Coverage is computed from case definitions and expected
contract fields so the suite can reveal missing scenario space before runtime.
"""
from __future__ import unicode_literals

import codecs
import json


def load_jsonl(path):
    cases = []
    with codecs.open(path, 'r', encoding='utf-8') as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            cases.append(json.loads(raw))
    return cases


def _unique_values(cases, field):
    values = []
    seen = set()
    for case in cases or []:
        expected = case.get('expected') or {}
        value = expected.get(field)
        if value is None:
            continue
        if isinstance(value, list):
            for item in value:
                if item not in seen:
                    seen.add(item)
                    values.append(item)
        else:
            if value not in seen:
                seen.add(value)
                values.append(value)
    return values


def _count_present(cases, field):
    total = len(cases or [])
    count = 0
    for case in cases or []:
        expected = case.get('expected') or {}
        if expected.get(field) is not None:
            count += 1
    return count, total


def _rate(hit, total):
    if not total:
        return 0.0
    return hit * 1.0 / total


def calculate_coverage(cases):
    cases = cases or []
    total = len(cases)

    status_counts = {}
    intent_counts = {}
    task_type_counts = {}
    category_counts = {}

    metric_total = 0
    dimension_total = 0
    trace_event_total = 0
    governance_total = 0
    clarification_total = 0
    unsupported_total = 0
    blocked_total = 0
    comparison_total = 0

    for case in cases:
        expected = case.get('expected') or {}
        category = case.get('category') or 'unknown'
        category_counts[category] = category_counts.get(category, 0) + 1

        status = expected.get('status')
        intent = expected.get('intent')
        task_type = expected.get('task_type')
        if status is not None:
            status_counts[status] = status_counts.get(status, 0) + 1
        if intent is not None:
            intent_counts[intent] = intent_counts.get(intent, 0) + 1
        if task_type is not None:
            task_type_counts[task_type] = task_type_counts.get(task_type, 0) + 1

        if expected.get('metric') is not None:
            metric_total += 1
        if expected.get('dimensions') is not None:
            dimension_total += 1
        trace_events = expected.get('trace_events') or []
        if trace_events:
            trace_event_total += 1
            if 'governance' in trace_events:
                governance_total += 1
        if intent == 'clarification' or status == 'need_clarification':
            clarification_total += 1
        if intent == 'unsupported' or status == 'unsupported':
            unsupported_total += 1
        if status == 'blocked':
            blocked_total += 1
        if intent == 'comparison' or category == 'comparison':
            comparison_total += 1

    coverage = {
        'total': total,
        'status_coverage': _rate(len(status_counts), total),
        'intent_coverage': _rate(len(intent_counts), total),
        'task_type_coverage': _rate(len(task_type_counts), total),
        'metric_coverage': _rate(metric_total, total),
        'dimension_coverage': _rate(dimension_total, total),
        'trace_event_coverage': _rate(trace_event_total, total),
        'governance_coverage': _rate(governance_total, total),
        'clarification_coverage': _rate(clarification_total, total),
        'unsupported_coverage': _rate(unsupported_total, total),
        'blocked_coverage': _rate(blocked_total, total),
        'comparison_coverage': _rate(comparison_total, total),
        'status_values': sorted(status_counts.keys()),
        'intent_values': sorted(intent_counts.keys()),
        'task_type_values': sorted(task_type_counts.keys()),
        'category_values': sorted(category_counts.keys()),
    }
    return coverage


__all__ = ['load_jsonl', 'calculate_coverage']
