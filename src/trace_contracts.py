# -*- coding: utf-8 -*-
"""P2 trace envelope and replay contracts.

The helpers in this module are additive and Python 2 compatible.  They turn the
existing observer event stream into a stable, replayable trace envelope without
changing the runtime control flow.
"""
from __future__ import unicode_literals

import time

try:
    from claim_graduation import (audit_answer_contract_with_provenance,
                                  DEFAULT_FINAL_EVIDENCE_TTL_SECONDS)
except Exception:  # pragma: no cover - keep trace helpers dependency-light
    audit_answer_contract_with_provenance = None
    DEFAULT_FINAL_EVIDENCE_TTL_SECONDS = 300

try:
    from masking_policy import sanitize_agent_payload, sanitize_text
except Exception:  # pragma: no cover - keep trace helpers dependency-light
    def sanitize_agent_payload(value, masked_fields=None):
        return value

    def sanitize_text(value):
        return value


_TRACE_EVENT_CONTRACT = 'trace_event_v2'
_TRACE_ENVELOPE_CONTRACT = 'trace_envelope_v2'
_TRACE_REPLAY_CONTRACT = 'trace_replay_v2'
_REEXECUTION_REPLAY_CONTRACT = 'reexecution_replay_validation_v1'

_NON_DATA_TERMINAL_STATUSES = set([
    'blocked', 'need_clarification', 'no_answer', 'error', 'unsupported',
    'pending_human_review', 'fallback', 'degraded'
])

_REQUIRED_BY_STATUS = {
    # Legacy ok/template answers can be replayed by precheck -> plan -> complete.
    # Data-bearing ok answers are strengthened in _required_stages() below with
    # execute + answer_audit before complete.
    'ok': ['precheck', 'plan', 'complete'],
    'blocked': ['precheck', 'complete'],
    'need_clarification': ['precheck', 'plan', 'complete'],
    'no_answer': ['precheck', 'complete'],
    'error': ['precheck', 'complete'],
    'degraded': ['precheck', 'plan', 'complete'],
}

_STAGE_ALIASES = {
    'governance': 'precheck',
    'permission_policy': 'precheck',
    'human_gate': 'precheck',
    'route': 'route',
    'routing': 'route',
    'plan': 'plan',
    'planning': 'plan',
    'dag_node': 'dag_node',
    'execution': 'execute',
    'execute': 'execute',
    'sql_execute': 'execute',
    'tool_execute': 'execute',
    'analysis': 'analyze',
    'analyze': 'analyze',
    'reporting': 'report',
    'report': 'report',
    'answer_audit': 'answer_audit',
    'claim_audit': 'answer_audit',
    'evidence_store': 'evidence_store',
    'evidence_store_load': 'evidence_store',
    'evidence_store_persist': 'evidence_store',
    'complete': 'complete',
}


def _as_dict(value):
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, 'to_dict'):
        try:
            return dict(value.to_dict())
        except Exception:
            return {}
    return {}


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _event_name(event):
    event = _as_dict(event)
    return event.get('name') or event.get('stage') or 'event'


def canonical_stage(event):
    """Return the replay-facing canonical stage for an observer event."""
    event = _as_dict(event)
    name = _event_name(event)
    metadata = _as_dict(event.get('metadata'))
    if name == 'dag_node':
        node = metadata.get('node') or event.get('stage')
        if node:
            return node
    stage = event.get('stage') or event.get('phase') or name
    return _STAGE_ALIASES.get(stage, _STAGE_ALIASES.get(name, stage))


def normalize_trace_event(event, index=0):
    """Normalize one observer event into the P2 trace_event_v2 contract."""
    event = _as_dict(event)
    payload = _as_dict(event.get('payload'))
    metadata = _as_dict(event.get('metadata'))
    stage = canonical_stage(event)
    evidence_ids = []
    for key in ('evidence_id', 'evidence_ids', 'citations'):
        for item in _as_list(event.get(key) or payload.get(key) or metadata.get(key)):
            if item is not None and item not in evidence_ids:
                evidence_ids.append(item)
    error = event.get('error') or payload.get('error') or metadata.get('error')
    if error is not None:
        error = sanitize_text(error)
    normalized = {
        'contract': _TRACE_EVENT_CONTRACT,
        'event_id': event.get('event_id') or 'event-%04d' % index,
        'parent_event_id': event.get('parent_event_id'),
        'trace_id': event.get('trace_id'),
        'task_id': event.get('task_id') or payload.get('task_id'),
        'session_id': event.get('session_id') or payload.get('session_id'),
        'name': _event_name(event),
        'stage': stage,
        'status': event.get('status') or 'ok',
        'timestamp': event.get('timestamp'),
        'elapsed_ms': event.get('elapsed_ms') if event.get('elapsed_ms') is not None else event.get('latency_ms'),
        'failure_type': event.get('failure_type') or payload.get('failure_type') or metadata.get('failure_type'),
        'error': error,
        'query_id': event.get('query_id') or payload.get('query_id') or metadata.get('query_id'),
        'tool_call_id': event.get('tool_call_id') or payload.get('tool_call_id') or metadata.get('tool_call_id'),
        'dataid': event.get('dataid') or payload.get('dataid') or metadata.get('dataid'),
        'data_version': event.get('data_version') or payload.get('data_version') or metadata.get('data_version'),
        'row_count': event.get('row_count') if event.get('row_count') is not None else payload.get('row_count'),
        'evidence_ids': evidence_ids,
        'metadata': sanitize_agent_payload(metadata),
    }
    return sanitize_agent_payload(normalized)


def _required_stages(status, result):
    status = status or 'ok'
    required = list(_REQUIRED_BY_STATUS.get(status, ['precheck', 'complete']))
    result = _as_dict(result)
    if status in _NON_DATA_TERMINAL_STATUSES:
        return required
    # Data-bearing successful answers must be replayable through execution and
    # evidence/audit stages.  If a legacy answer is purely template/report-only,
    # the caller can still pass by emitting answer_audit + complete.
    if result.get('evidence_refs') or result.get('facts') or result.get('provenance'):
        for stage in ('execute', 'answer_audit'):
            if stage not in required:
                if 'complete' in required:
                    required.insert(required.index('complete'), stage)
                else:
                    required.append(stage)
    return required


def build_trace_envelope(trace, result=None, case=None):
    """Build a stable trace envelope for replay, quality gates and reports."""
    result = _as_dict(result)
    case = _as_dict(case)
    events = [normalize_trace_event(item, idx) for idx, item in enumerate(trace or [])]
    stage_order = [item.get('stage') for item in events if item.get('stage')]
    observed = set(stage_order)
    status = result.get('status') or case.get('status') or 'unknown'
    required = _required_stages(status, result)
    missing = [stage for stage in required if stage not in observed]
    unexpected_execute = status in _NON_DATA_TERMINAL_STATUSES and 'execute' in observed
    failures = [item for item in events if item.get('status') in ('error', 'failed', 'blocked')]
    first_failure = failures[0].get('stage') if failures else (missing[0] if missing else None)
    trace_id = result.get('trace_id') or (events[0].get('trace_id') if events else None)
    envelope = {
        'contract': _TRACE_ENVELOPE_CONTRACT,
        'trace_id': trace_id,
        'task_id': result.get('task_id') or (events[0].get('task_id') if events else None),
        'session_id': result.get('session_id') or (events[0].get('session_id') if events else None),
        'case_id': case.get('id') or case.get('case_id'),
        'status': status,
        'event_count': len(events),
        'events': events,
        'stage_order': stage_order,
        'required_stages': required,
        'missing_required_stages': missing,
        'unexpected_execute': unexpected_execute,
        'first_failure_stage': first_failure,
        'complete': (not missing) and (not unexpected_execute),
        'created_at': int(time.time() * 1000),
    }
    return sanitize_agent_payload(envelope)


def validate_trace_envelope(envelope):
    envelope = _as_dict(envelope)
    errors = []
    if envelope.get('contract') != _TRACE_ENVELOPE_CONTRACT:
        errors.append('contract_mismatch')
    if not envelope.get('trace_id'):
        errors.append('missing_trace_id')
    if envelope.get('complete') is not True:
        for stage in envelope.get('missing_required_stages') or []:
            errors.append('missing_stage:%s' % stage)
        if envelope.get('unexpected_execute'):
            errors.append('unexpected_execute_for_terminal_status')
    for idx, event in enumerate(envelope.get('events') or []):
        event = _as_dict(event)
        if event.get('contract') != _TRACE_EVENT_CONTRACT:
            errors.append('event_%d_contract_mismatch' % idx)
        if not event.get('stage'):
            errors.append('event_%d_missing_stage' % idx)
        if event.get('status') in ('error', 'failed') and not (event.get('failure_type') or event.get('error')):
            errors.append('event_%d_missing_error_envelope' % idx)
    return {
        'contract': 'trace_envelope_validation_v1',
        'valid': len(errors) == 0,
        'errors': errors,
    }


def validate_replay_evidence_freshness(result, now=None, ttl_seconds=None):
    """Replay-time check that final facts were backed by non-expired evidence.

    This is intentionally read-only: it reuses the serialized EvidenceBus and
    Claim Graduation scope captured by the final answer boundary.  If those
    artifacts are absent, the replay package marks the freshness audit as
    skipped rather than inventing evidence from trace text.
    """
    result = _as_dict(result)
    final_answer = _as_dict(result.get('final_answer')) or result
    provenance = _as_dict(result.get('provenance')) or _as_dict(final_answer.get('provenance'))
    claim_graduation = _as_dict(result.get('claim_graduation'))
    scope = _as_dict(claim_graduation.get('expected_scope'))
    if ttl_seconds is None:
        ttl_seconds = claim_graduation.get('ttl_seconds')
    if ttl_seconds is None:
        ttl_seconds = DEFAULT_FINAL_EVIDENCE_TTL_SECONDS
    if audit_answer_contract_with_provenance is None:
        return {'contract': 'replay_evidence_freshness_v1', 'audited': False,
                'valid': False, 'errors': ['claim_graduation_unavailable']}
    if not provenance.get('evidence_bus'):
        return {'contract': 'replay_evidence_freshness_v1', 'audited': False,
                'valid': False, 'errors': ['missing_serialized_evidence_bus']}
    audited, findings, was_audited = audit_answer_contract_with_provenance(
        final_answer, provenance=provenance, scope=scope, ttl_seconds=ttl_seconds,
        now=now, require_evidence_bus=True)
    errors = []
    for finding in findings or []:
        for rejected in finding.get('rejected') or []:
            if rejected.get('error') == 'evidence_ttl_expired':
                errors.append('evidence_ttl_expired:%s' % rejected.get('evidence_id'))
            elif rejected.get('error'):
                errors.append('%s:%s' % (rejected.get('error'), rejected.get('evidence_id')))
        if finding.get('code') == 'evidence_bus_missing':
            errors.append('missing_serialized_evidence_bus')
        elif finding.get('code') and not finding.get('rejected'):
            errors.append(finding.get('code'))
    return sanitize_agent_payload({
        'contract': 'replay_evidence_freshness_v1',
        'audited': was_audited,
        'valid': was_audited and not errors and audited.get('status') == final_answer.get('status'),
        'errors': errors,
        'ttl_seconds': ttl_seconds,
        'checked_at': now,
        'findings': findings,
        'audited_status': audited.get('status'),
        'original_status': final_answer.get('status'),
    })


def _case_events_for_replay(case, result):
    case = _as_dict(case)
    result = _as_dict(result)
    context = _as_dict(result.get('case_context'))
    events = []
    for item in case.get('events') or []:
        events.append(_as_dict(item))
    for item in context.get('events') or []:
        events.append(_as_dict(item))
    # De-duplicate by event id while preserving order.  This supports callers
    # that pass both a raw case dict and an embedded case_context.
    seen = set()
    deduped = []
    for event in events:
        event_id = event.get('event_id') or id(event)
        if event_id in seen:
            continue
        seen.add(event_id)
        deduped.append(event)
    return deduped


def validate_reexecution_replay(case, result=None):
    """Replay-time proof that scheduled re-execution tasks reached a terminal event.

    The validator is intentionally strict: if a re-execution was scheduled, replay
    must show a matching completed/failed terminal transition for the same
    idempotency key.  A failed re-execution is not silently accepted because it
    cannot support fresh claims.
    """
    result = _as_dict(result)
    scheduled = {}
    terminal = {}
    errors = []
    for event in _case_events_for_replay(case, result):
        event_type = event.get('event_type') or event.get('name')
        payload = _as_dict(event.get('payload'))
        if event_type == 'reexecution.scheduled':
            key = payload.get('idempotency_key')
            task = _as_dict(payload.get('task'))
            metadata = _as_dict(task.get('metadata'))
            if not key:
                errors.append('reexecution_scheduled_missing_idempotency_key')
                key = 'missing:%s' % (event.get('event_id') or len(scheduled))
            if not metadata.get('expected_scope'):
                errors.append('reexecution_scheduled_missing_expected_scope:%s' % key)
            scheduled[key] = event
        elif event_type in ('reexecution.completed', 'reexecution.failed'):
            key = payload.get('idempotency_key')
            if not key:
                errors.append('%s_missing_idempotency_key' % event_type.replace('.', '_'))
                continue
            terminal[key] = event
    for key, event in scheduled.items():
        final = terminal.get(key)
        if not final:
            errors.append('reexecution_missing_terminal_event:%s' % key)
            continue
        if (final.get('event_type') or final.get('name')) == 'reexecution.failed':
            errors.append('reexecution_failed:%s' % key)
        payload = _as_dict(final.get('payload'))
        if (final.get('event_type') or final.get('name')) == 'reexecution.completed' and not payload.get('evidence_id'):
            errors.append('reexecution_completed_missing_evidence:%s' % key)
    # Idempotency keys are control-plane correlation identifiers.  They must
    # remain byte-for-byte comparable to the key recorded in the scheduled
    # event; generic output masking can mistake embedded digits for PII and
    # would otherwise make replay diagnostics non-reproducible.  No user input
    # is emitted here: the values originate from the internal task dispatcher.
    return {
        'contract': _REEXECUTION_REPLAY_CONTRACT,
        'valid': len(errors) == 0,
        'errors': errors,
        'scheduled_count': len(scheduled),
        'terminal_count': len(terminal),
        'scheduled_idempotency_keys': sorted(scheduled.keys()),
    }


def build_replay_package(case, evaluated):
    evaluated = _as_dict(evaluated)
    case = _as_dict(case)
    result = _as_dict(evaluated.get('result'))
    trace = evaluated.get('trace') or []
    envelope = evaluated.get('trace_envelope') or build_trace_envelope(trace, result=result, case=case)
    return sanitize_agent_payload({
        'contract': _TRACE_REPLAY_CONTRACT,
        'id': evaluated.get('id') or case.get('id') or case.get('case_id'),
        'query': evaluated.get('query') or case.get('query'),
        'expected': evaluated.get('expected') or case.get('expected') or {},
        'passed': evaluated.get('passed'),
        'failure_type': evaluated.get('failure_type'),
        'errors': evaluated.get('errors') or [],
        'result': result,
        'trace_envelope': envelope,
        'trace_validation': validate_trace_envelope(envelope),
        'evidence_freshness_validation': validate_replay_evidence_freshness(
            result, now=evaluated.get('replay_now')),
        'reexecution_validation': validate_reexecution_replay(case, result=result),
    })


__all__ = [
    'build_replay_package', 'build_trace_envelope', 'canonical_stage',
    'normalize_trace_event', 'validate_trace_envelope', 'validate_replay_evidence_freshness',
    'validate_reexecution_replay'
]
