# -*- coding: utf-8 -*-
"""Deterministic ecommerce multi-agent workers.

These workers are intentionally local and contract-first.  They do not call an
LLM and they do not invent facts; Data Analyst accepts an execution result or
execution envelope supplied by the graph/test harness, Diagnosis only derives
bounded findings from verified evidence, and Auditor can veto unsupported facts.
"""
from __future__ import unicode_literals

from contracts import build_execution_envelope
from evidence_bus import EvidenceBus, is_verified_execution_envelope
from multi_agent_contracts import (
    AgentResult, RESULT_OK, RESULT_ERROR, RESULT_BLOCKED,
    WORKER_DATA_ANALYST, WORKER_DIAGNOSIS, WORKER_AUDITOR,
)


VERIFIED_AUTHORITY = 'verified_execution'


def _as_dict(value):
    if isinstance(value, dict):
        return value
    if hasattr(value, 'to_dict'):
        try:
            return value.to_dict()
        except Exception:
            return {}
    return {}


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _first_dependency_output(task, dag_state):
    state = dag_state or {}
    results = state.get('results') or {}
    for dep in task.dependencies:
        result = results.get(dep) or {}
        output = result.get('output') or {}
        if output:
            return output
    return {}


def _is_verified_envelope(envelope):
    return is_verified_execution_envelope(envelope)


def _as_evidence_bus(value):
    if isinstance(value, EvidenceBus):
        return value
    data = _as_dict(value)
    if data.get('contract') == 'evidence_bus_v1':
        return EvidenceBus(data.get('records') or [])
    return None


def _safe_float(value):
    try:
        if value is None or value == '':
            return None
        return float(value)
    except Exception:
        return None


def _first_present(row, keys):
    row = _as_dict(row)
    for key in keys:
        if key in row and row.get(key) is not None:
            return row.get(key)
    return None


def _root_cause_contribution_candidates(rows, dimensions, metric):
    """Derive deterministic contribution candidates from verified rows only.

    This is contribution/ranking math, not causal inference.  Preferred input is
    comparison-shaped rows containing current/previous metric values.  Fixture
    rows with only the metric column are still ranked as bounded contribution
    observations, but are flagged as lacking baseline comparison.
    """
    rows = _as_list(rows)
    dimensions = list(dimensions or [])
    metric = metric or 'metric'
    candidates = []
    has_baseline = False
    for idx, raw in enumerate(rows):
        row = _as_dict(raw)
        current = _safe_float(_first_present(row, [
            'current_%s' % metric, '%s_current' % metric, 'current_value',
            'current', metric, 'value']))
        previous = _safe_float(_first_present(row, [
            'previous_%s' % metric, '%s_previous' % metric, 'previous_value',
            'baseline_%s' % metric, '%s_baseline' % metric, 'baseline',
            'previous']))
        if current is None:
            continue
        if previous is not None:
            has_baseline = True
            delta = current - previous
            score = abs(delta)
        else:
            delta = None
            score = abs(current)
        label_parts = []
        for dim in dimensions:
            if dim in row:
                label_parts.append('%s=%s' % (dim, row.get(dim)))
        label = ','.join(label_parts) if label_parts else 'row_%s' % (idx + 1)
        candidates.append({
            'dimension_values': dict([(dim, row.get(dim)) for dim in dimensions if dim in row]),
            'label': label,
            'current_value': current,
            'previous_value': previous,
            'delta': delta,
            'abs_delta': score,
            'contribution_share': 0.0,
        })
    total = sum([item.get('abs_delta') or 0.0 for item in candidates])
    for item in candidates:
        item['contribution_share'] = (item.get('abs_delta') or 0.0) / total if total else 0.0
    candidates.sort(key=lambda item: item.get('abs_delta') or 0.0, reverse=True)
    return candidates, has_baseline


def _normalise_scope_value(value):
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        return tuple([_normalise_scope_value(x) for x in value])
    if isinstance(value, dict):
        return tuple(sorted([(k, _normalise_scope_value(v)) for k, v in value.items()]))
    return value


def _scope_mismatches(envelope, expected_scope):
    envelope = _as_dict(envelope)
    expected_scope = _as_dict(expected_scope)
    if not expected_scope:
        return []
    metadata = _as_dict(envelope.get('metadata'))
    mismatches = []
    expected_metric = expected_scope.get('metric')
    actual_metric = metadata.get('metric') or metadata.get('source_metric')
    if expected_metric is not None and actual_metric is not None and expected_metric != actual_metric:
        mismatches.append('metric')
    allowed_time_ranges = expected_scope.get('allowed_time_ranges') or []
    actual_time_range = envelope.get('time_range')
    if allowed_time_ranges and actual_time_range is not None and actual_time_range not in allowed_time_ranges:
        mismatches.append('time_range')
    expected_dataid = expected_scope.get('dataid')
    if expected_dataid is not None and envelope.get('dataid') is not None and expected_dataid != envelope.get('dataid'):
        mismatches.append('dataid')
    expected_data_version = expected_scope.get('data_version')
    if expected_data_version is not None and envelope.get('data_version') is not None and expected_data_version != envelope.get('data_version'):
        mismatches.append('data_version')
    expected_dimensions = expected_scope.get('dimensions')
    actual_dimensions = metadata.get('dimensions')
    if expected_dimensions is not None and actual_dimensions is not None:
        if list(expected_dimensions or []) != list(actual_dimensions or []):
            mismatches.append('dimensions')
    expected_filters = expected_scope.get('filters')
    actual_filters = metadata.get('filters')
    if expected_filters not in (None, {}, []) and actual_filters not in (None, {}, []):
        if _normalise_scope_value(expected_filters) != _normalise_scope_value(actual_filters):
            mismatches.append('filters')
    provenance = _as_dict(envelope.get('provenance'))
    expected_tenant_id = expected_scope.get('tenant_id')
    actual_tenant_id = metadata.get('tenant_id') or provenance.get('tenant_id')
    if expected_tenant_id is not None and actual_tenant_id is not None and expected_tenant_id != actual_tenant_id:
        mismatches.append('tenant_id')
    expected_user_id = expected_scope.get('user_id')
    actual_user_id = metadata.get('user_id') or provenance.get('user_id')
    if expected_user_id is not None and actual_user_id is not None and expected_user_id != actual_user_id:
        mismatches.append('user_id')
    expected_permission_scope = expected_scope.get('permission_scope')
    actual_permission_scope = metadata.get('permission_scope') or provenance.get('permission_scope')
    if expected_permission_scope not in (None, {}, []) and actual_permission_scope not in (None, {}, []):
        if _normalise_scope_value(expected_permission_scope) != _normalise_scope_value(actual_permission_scope):
            mismatches.append('permission_scope')
    return mismatches


def _format_candidate_value(value):
    if value is None:
        return 'NA'
    try:
        if float(value).is_integer():
            return str(int(value))
    except Exception:
        pass
    return '%.4f' % float(value)


def _build_synthetic_verified_envelope(task):
    """Build a deterministic local envelope from explicit fixture input only."""
    rows = _as_list(task.input.get('rows'))
    metric = task.input.get('metric')
    time_range = task.input.get('time_range')
    query_id = task.input.get('query_id') or 'q_%s' % task.task_id
    evidence_id = task.input.get('evidence_id') or 'ev_%s' % task.task_id
    return build_execution_envelope(
        status='ok', stage='execute', query_id=query_id, evidence_id=evidence_id,
        dataid=task.input.get('dataid') or task.input.get('model') or 'ecommerce_orders',
        data_version=task.input.get('data_version') or 'test_version',
        row_count=task.input.get('row_count', len(rows)),
        time_range=time_range, authority=VERIFIED_AUTHORITY,
        provenance={'trace_id': (dag_state_trace_id(task) or None), 'task_id': task.task_id,
                    'tenant_id': task.input.get('tenant_id'), 'user_id': task.input.get('user_id'),
                    'permission_scope': task.input.get('permission_scope')},
        metadata={'metric': metric, 'dimensions': list(task.input.get('dimensions') or []),
                  'filters': task.input.get('filters') or {},
                  'tenant_id': task.input.get('tenant_id'), 'user_id': task.input.get('user_id'),
                  'permission_scope': task.input.get('permission_scope')},
    )


def dag_state_trace_id(task):
    return task.metadata.get('trace_id') if getattr(task, 'metadata', None) else None


def data_analyst_worker(task, dag_state=None):
    """Return a verified ExecutionEnvelope only from explicit execution input.

    Accepted inputs:
    - execution_result with execution_envelope
    - execution_envelope directly
    - rows + metric fixture data for deterministic tests
    """
    execution_result = _as_dict(task.input.get('execution_result'))
    execution_engine = task.input.get('execution_engine')
    execution_plan = _as_dict(task.input.get('execution_plan'))
    if not execution_result and execution_engine is not None and execution_plan:
        try:
            execution_result = _as_dict(execution_engine.execute(
                execution_plan,
                trace_id=(dag_state or {}).get('trace_id'),
                task_id=task.task_id))
        except Exception as exc:
            execution_result = {
                'status': 'error',
                'execution_envelope': build_execution_envelope(
                    status='error', stage='execute', error_code='execution_engine_error',
                    message=str(exc), row_count=0, authority='unverified',
                    provenance={'task_id': task.task_id, 'trace_id': (dag_state or {}).get('trace_id')})}
    envelope = _as_dict(task.input.get('execution_envelope') or execution_result.get('execution_envelope'))
    if not envelope and task.input.get('simulate_error'):
        envelope = build_execution_envelope(
            status='error', stage='execute', error_code='simulated_execution_error',
            message='simulated execution error', row_count=0, authority='unverified',
            provenance={'task_id': task.task_id})
    if not envelope and (task.input.get('rows') is not None or task.input.get('metric')):
        envelope = _build_synthetic_verified_envelope(task)

    evidence_bus = _as_evidence_bus(task.input.get('evidence_bus'))
    if evidence_bus is not None:
        evidence_bus.record_envelope(envelope, producer_task_id=task.task_id,
                                     trace_id=(dag_state or {}).get('trace_id'),
                                     graph_type=task.input.get('graph_type'))

    if not _is_verified_envelope(envelope):
        return AgentResult(
            task.task_id,
            status=RESULT_ERROR if envelope.get('status') == 'error' else RESULT_BLOCKED,
            output={
                'execution_envelope': envelope or build_execution_envelope(
                    status='blocked', stage='execute', error_code='missing_verified_execution',
                    message='verified execution evidence is required', row_count=0,
                    authority='unverified', provenance={'task_id': task.task_id}),
                'authority': 'unverified',
                'evidence_refs': [],
                'limitations': ['missing_verified_execution'],
            },
            errors=[{'error': 'missing_verified_execution'}],
        )

    rows = _as_list(task.input.get('rows') or execution_result.get('results') or execution_result.get('rows'))
    evidence_id = envelope.get('evidence_id')
    output = {
        'metric': task.input.get('metric') or envelope.get('metadata', {}).get('metric'),
        'dimensions': list(task.input.get('dimensions') or envelope.get('metadata', {}).get('dimensions') or []),
        'time_range': task.input.get('time_range') or envelope.get('time_range'),
        'filters': task.input.get('filters') or {},
        'rows': rows,
        'execution_envelope': envelope,
        'evidence_id': evidence_id,
        'evidence_refs': [evidence_id],
        'evidence_ids': [evidence_id],
        'authority': VERIFIED_AUTHORITY,
        'facts': [{'text': 'execution verified', 'evidence_ids': [evidence_id]}],
        'limitations': [],
    }
    if evidence_bus is not None:
        output['evidence_bus'] = evidence_bus.to_dict()
    return AgentResult(task.task_id, status=RESULT_OK, output=output, citations=[evidence_id])


def diagnosis_worker(task, dag_state=None):
    """Create bounded findings from verified upstream execution only."""
    upstream = _first_dependency_output(task, dag_state)
    envelope = _as_dict(upstream.get('execution_envelope') or task.input.get('execution_envelope'))
    if not _is_verified_envelope(envelope):
        return AgentResult(
            task.task_id,
            status=RESULT_BLOCKED,
            output={
                'findings': [],
                'hypotheses': [],
                'limitations': ['diagnosis_requires_verified_execution'],
                'evidence_refs': [],
            },
            errors=[{'error': 'diagnosis_requires_verified_execution'}],
        )
    evidence_id = envelope.get('evidence_id')
    dimensions = list(upstream.get('dimensions') or task.input.get('dimensions') or [])
    rows = _as_list(upstream.get('rows') or task.input.get('rows'))
    graph_type = task.input.get('graph_type') or upstream.get('graph_type')
    findings = []
    if graph_type == 'root_cause':
        if not rows:
            return AgentResult(
                task.task_id,
                status=RESULT_BLOCKED,
                output={
                    'findings': [],
                    'hypotheses': [],
                    'limitations': ['root_cause_requires_non_empty_verified_rows'],
                    'evidence_refs': [evidence_id] if evidence_id else [],
                },
                errors=[{'error': 'root_cause_requires_non_empty_verified_rows'}],
            )
        if not dimensions:
            return AgentResult(
                task.task_id,
                status=RESULT_BLOCKED,
                output={
                    'findings': [],
                    'hypotheses': [],
                    'limitations': ['root_cause_requires_dimensions'],
                    'evidence_refs': [evidence_id] if evidence_id else [],
                },
                errors=[{'error': 'root_cause_requires_dimensions'}],
            )
        candidates, has_baseline = _root_cause_contribution_candidates(
            rows, dimensions, upstream.get('metric') or envelope.get('metadata', {}).get('metric'))
        if not candidates:
            return AgentResult(
                task.task_id,
                status=RESULT_BLOCKED,
                output={
                    'findings': [],
                    'hypotheses': [],
                    'limitations': ['root_cause_requires_numeric_metric_values'],
                    'evidence_refs': [evidence_id] if evidence_id else [],
                },
                errors=[{'error': 'root_cause_requires_numeric_metric_values'}],
            )
        top = candidates[0]
        text = ('root cause candidates require validation; top contribution candidate: %s, '
                'current=%s, previous=%s, delta=%s, contribution_share=%.2f%%') % (
            top.get('label'), _format_candidate_value(top.get('current_value')),
            _format_candidate_value(top.get('previous_value')), _format_candidate_value(top.get('delta')),
            (top.get('contribution_share') or 0.0) * 100.0)
        findings.append({
            'text': text,
            'kind': 'contribution_candidate_not_causal',
            'evidence_ids': [evidence_id],
            'contribution_candidates': candidates,
            'top_candidate': top,
            'claim_scope': 'contribution_not_causal',
        })
        if not has_baseline:
            findings[-1]['baseline_warning'] = 'baseline_missing_metric_values'
    elif graph_type == 'report':
        findings.append({
            'text': 'evidence-only report section compiled from verified execution evidence',
            'kind': 'verified_report_section',
            'evidence_ids': [evidence_id],
        })
    elif dimensions:
        findings.append({
            'text': 'breakdown prepared for dimensions: %s' % ','.join(dimensions),
            'kind': 'contribution_observation',
            'evidence_ids': [evidence_id],
        })
    elif rows:
        findings.append({
            'text': 'metric result has %s verified row(s)' % len(rows),
            'kind': 'observation',
            'evidence_ids': [evidence_id],
        })
    return AgentResult(task.task_id, status=RESULT_OK, output={
        'findings': findings,
        'hypotheses': [],
        'limitations': ['diagnosis_is_not_causal_proof'],
        'evidence_refs': [evidence_id],
        'evidence_ids': [evidence_id],
    }, citations=[evidence_id])


def auditor_worker(task, dag_state=None):
    """Veto unsupported facts and non-ok upstream execution."""
    state = dag_state or {}
    results = state.get('results') or {}
    evidence_bus = _as_evidence_bus(task.input.get('evidence_bus'))
    expected_scope = _as_dict(task.input.get('expected_scope'))
    evidence_ids = set()
    unsupported = []
    blocked_errors = []
    for dep in task.dependencies:
        result = results.get(dep) or {}
        if result.get('status') != RESULT_OK:
            blocked_errors.append({'dependency': dep, 'error': 'dependency_not_ok', 'status': result.get('status')})
        output = result.get('output') or {}
        envelope = _as_dict(output.get('execution_envelope'))
        if _is_verified_envelope(envelope):
            evidence_ids.add(envelope.get('evidence_id'))
            mismatches = _scope_mismatches(envelope, expected_scope)
            for item in mismatches:
                unsupported.append('scope_mismatch:%s:%s' % (dep, item))
        for item in _as_list(output.get('evidence_refs')):
            if item:
                evidence_ids.add(item)
        if evidence_bus is not None:
            valid_ids, missing_ids = evidence_bus.validate_ids(output.get('evidence_refs') or [])
            for item in valid_ids:
                evidence_ids.add(item)
            for item in missing_ids:
                unsupported.append('missing_evidence_ref:%s' % item)
        for fact in _as_list(output.get('facts')):
            ids = [x for x in _as_list(_as_dict(fact).get('evidence_ids')) if x in evidence_ids]
            if _as_dict(fact).get('text') and not ids:
                unsupported.append(_as_dict(fact).get('text'))
        for finding in _as_list(output.get('findings')):
            ids = [x for x in _as_list(_as_dict(finding).get('evidence_ids')) if x in evidence_ids]
            if _as_dict(finding).get('text') and not ids:
                unsupported.append(_as_dict(finding).get('text'))

    if blocked_errors or unsupported:
        return AgentResult(task.task_id, status=RESULT_BLOCKED, output={
            'audit_status': 'blocked',
            'evidence_refs': sorted(evidence_ids),
            'unsupported_claims': unsupported,
            'scope_mismatches': [x for x in unsupported if str(x).startswith('scope_mismatch:')],
            'limitations': ['audit_blocked_unsupported_or_failed_dependency'],
        }, errors=blocked_errors + [{'error': 'unsupported_claims', 'claims': unsupported}] if unsupported else blocked_errors)

    resolved_evidence_ids = sorted(evidence_ids)
    return AgentResult(task.task_id, status=RESULT_OK, output={
        'audit_status': 'ok',
        'evidence_refs': resolved_evidence_ids,
        'evidence_ids': resolved_evidence_ids,
        'unsupported_claims': [],
        'scope_mismatches': [],
        'limitations': [],
    }, citations=resolved_evidence_ids)


def _evidence_bound_schema(required_fields):
    properties = {
        'evidence_ids': {'type': 'array'},
        'evidence_refs': {'type': 'array'},
        'execution_envelope': {'type': 'object'},
        'findings': {'type': 'array'},
        'facts': {'type': 'array'},
        'limitations': {'type': 'array'},
        'authority': {'type': 'string'},
        'audit_status': {'type': 'string'},
        'unsupported_claims': {'type': 'array'},
        'scope_mismatches': {'type': 'array'},
    }
    return {
        'required': list(required_fields or []),
        'properties': properties,
        'evidence_ids_must_resolve': True,
        'evidence_scope_must_match': True,
    }


def register_ecommerce_workers(registry):
    from worker_registry import WorkerSpec
    registry.register(WorkerSpec(
        WORKER_DATA_ANALYST,
        data_analyst_worker,
        description='deterministic ecommerce data analyst worker',
        output_schema=_evidence_bound_schema(['execution_envelope', 'evidence_ids', 'authority']),
    ))
    registry.register(WorkerSpec(
        WORKER_DIAGNOSIS,
        diagnosis_worker,
        description='deterministic ecommerce diagnosis worker',
        output_schema=_evidence_bound_schema(['findings', 'evidence_ids', 'limitations']),
    ))
    registry.register(WorkerSpec(
        WORKER_AUDITOR,
        auditor_worker,
        description='deterministic ecommerce auditor worker',
        output_schema=_evidence_bound_schema(['audit_status', 'evidence_ids', 'unsupported_claims', 'scope_mismatches']),
    ))
    return registry


__all__ = [
    'data_analyst_worker', 'diagnosis_worker', 'auditor_worker',
    'register_ecommerce_workers', 'VERIFIED_AUTHORITY'
]
