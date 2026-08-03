# -*- coding: utf-8 -*-
"""Snapshot diff utilities for Agent Harness regression checks."""
from __future__ import unicode_literals

BREAKING_TYPES = set([
    'case_removed', 'status_changed', 'intent_changed', 'task_type_changed',
    'metric_changed', 'dimensions_changed', 'contract_changed', 'trace_changed',
    'pass_state_changed',
])
WARNING_TYPES = set([
    'case_added', 'sql_changed', 'result_shape_changed', 'analysis_changed',
    'report_changed', 'prompt_chain_changed', 'risk_changed',
])


def _case_map(snapshot):
    out = {}
    for item in snapshot.get('cases') or []:
        cid = item.get('id')
        if cid:
            out[cid] = item
    return out


def _severity(change_type):
    if change_type in BREAKING_TYPES:
        return 'breaking'
    if change_type in WARNING_TYPES:
        return 'warning'
    return 'info'


def _change(case_id, change_type, before=None, after=None, field=None):
    return {
        'id': case_id,
        'change_type': change_type,
        'severity': _severity(change_type),
        'field': field,
        'before': before,
        'after': after,
    }


def _compare_field(changes, case_id, before, after, field, change_type):
    bv = before.get(field)
    av = after.get(field)
    if bv != av:
        changes.append(_change(case_id, change_type, bv, av, field))


def _compare_actual(changes, case_id, before, after):
    ba = before.get('actual') or {}
    aa = after.get('actual') or {}
    checks = [
        ('status', 'status_changed'),
        ('intent', 'intent_changed'),
        ('task_type', 'task_type_changed'),
        ('metric', 'metric_changed'),
        ('dimensions', 'dimensions_changed'),
        ('sql_shape', 'sql_changed'),
        ('has_results', 'result_shape_changed'),
        ('has_analysis', 'analysis_changed'),
        ('has_report', 'report_changed'),
        ('contract_version', 'contract_changed'),
        ('prompt_chain', 'prompt_chain_changed'),
        ('requires_human_review', 'risk_changed'),
        ('approval_status', 'risk_changed'),
        ('risk_level', 'risk_changed'),
    ]
    for field, change_type in checks:
        _compare_field(changes, case_id, ba, aa, field, change_type)


def diff_snapshots(baseline, current):
    changes = []
    bmap = _case_map(baseline)
    cmap = _case_map(current)
    for case_id in sorted(bmap.keys()):
        if case_id not in cmap:
            changes.append(_change(case_id, 'case_removed', True, False, 'case'))
            continue
        before = bmap[case_id]
        after = cmap[case_id]
        if bool(before.get('passed')) != bool(after.get('passed')):
            changes.append(_change(case_id, 'pass_state_changed', before.get('passed'), after.get('passed'), 'passed'))
        _compare_actual(changes, case_id, before, after)
        if before.get('trace_events') != after.get('trace_events'):
            changes.append(_change(case_id, 'trace_changed', before.get('trace_events'), after.get('trace_events'), 'trace_events'))
        if before.get('failure_type') != after.get('failure_type'):
            changes.append(_change(case_id, 'failure_type_changed', before.get('failure_type'), after.get('failure_type'), 'failure_type'))
    for case_id in sorted(cmap.keys()):
        if case_id not in bmap:
            changes.append(_change(case_id, 'case_added', False, True, 'case'))
    breaking = [c for c in changes if c.get('severity') == 'breaking']
    warnings = [c for c in changes if c.get('severity') == 'warning']
    info = [c for c in changes if c.get('severity') == 'info']
    return {
        'baseline_suite': baseline.get('suite'),
        'current_suite': current.get('suite'),
        'changed': len(changes),
        'breaking': len(breaking),
        'warning': len(warnings),
        'info': len(info),
        'changes': changes,
    }


__all__ = ['diff_snapshots']
