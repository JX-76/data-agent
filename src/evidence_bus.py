# -*- coding: utf-8 -*-
"""Verified evidence store and case-scoped evidence views.

The underlying EvidenceBus is append-only for the lifetime of a process.  A case
never removes a shared record merely because it is not usable in that case;
CaseEvidenceView records a local rejection decision instead.  This lets several
cases safely use the same execution-evidence store.
"""
from __future__ import unicode_literals

import time

VERIFIED_AUTHORITY = 'verified_execution'
EVIDENCE_RECORD_CONTRACT = 'verified_evidence_record_v2'
CASE_EVIDENCE_VIEW_CONTRACT = 'case_evidence_view_v1'


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


def is_verified_execution_envelope(envelope):
    envelope = _as_dict(envelope)
    return bool(envelope.get('status') == 'ok' and
                envelope.get('authority') == VERIFIED_AUTHORITY and
                envelope.get('evidence_id'))


class CaseEvidenceView(object):
    """A non-destructive, auditable evidence projection for one BusinessCase."""

    def __init__(self, evidence_bus, case_id, expected_scope=None, ttl_seconds=None):
        self.evidence_bus = evidence_bus
        self.case_id = case_id
        self.expected_scope = _as_dict(expected_scope)
        self.ttl_seconds = ttl_seconds
        # A view may be reconstructed after evidence has already been linked to
        # the case.  Preserve those links as accepted case-local references; the
        # caller can refresh() to hide records that have since become invalid.
        self.accepted_ids = list(evidence_bus.case_links.get(case_id, []))
        self.rejections = list(evidence_bus.get_case_rejections(case_id))
        self.version = 0

    def link(self, evidence_ids, expected_scope=None, ttl_seconds=None, now=None):
        expected_scope = _as_dict(expected_scope) or self.expected_scope
        ttl_seconds = self.ttl_seconds if ttl_seconds is None else ttl_seconds
        valid, rejected = self.evidence_bus.validate_scope(
            evidence_ids, expected_scope=expected_scope,
            ttl_seconds=ttl_seconds, now=now)
        for evidence_id in valid:
            if evidence_id not in self.accepted_ids:
                self.accepted_ids.append(evidence_id)
                self.evidence_bus.link_case_evidence(self.case_id, evidence_id)
        self._record_rejections(rejected, expected_scope, now)
        if valid or rejected:
            self.version += 1
        return valid, rejected

    def refresh(self, evidence_ids=None, expected_scope=None, ttl_seconds=None, now=None):
        ids = evidence_ids if evidence_ids is not None else list(self.accepted_ids)
        valid, rejected = self.link(ids, expected_scope=expected_scope,
                                    ttl_seconds=ttl_seconds, now=now)
        # A record which became stale/scope-invalid is hidden only in this view.
        rejected_ids = set([item.get('evidence_id') for item in rejected])
        if rejected_ids:
            self.accepted_ids = [x for x in self.accepted_ids if x not in rejected_ids]
        return valid, rejected

    def _record_rejections(self, rejected, expected_scope, now):
        for item in rejected:
            item = dict(_as_dict(item))
            error = item.get('error') or 'evidence_rejected'
            reason = {
                'evidence_ttl_expired': 'rejected_ttl_expired',
                'evidence_scope_mismatch': self._scope_reason(item.get('fields') or []),
                'missing_evidence_ref': 'rejected_missing_evidence_ref',
                'evidence_not_linked_to_case': 'rejected_case_scope_mismatch',
            }.get(error, 'rejected_evidence')
            audit = {
                'contract': 'case_evidence_rejection_v1', 'case_id': self.case_id,
                'evidence_id': item.get('evidence_id'), 'reason': reason,
                'error': error, 'fields': list(item.get('fields') or []),
                'expected_scope': dict(expected_scope or {}),
                'timestamp': time.time() if now is None else float(now),
            }
            if audit not in self.rejections:
                self.rejections.append(audit)
                self.evidence_bus.record_case_rejection(audit)

    def _scope_reason(self, fields):
        fields = set(fields or [])
        if 'permission_scope' in fields or 'tenant_id' in fields or 'user_id' in fields:
            return 'rejected_permission_mismatch'
        if 'data_version' in fields:
            return 'rejected_data_version_mismatch'
        return 'rejected_scope_mismatch'

    def records(self):
        return [self.evidence_bus.get(evidence_id) for evidence_id in self.accepted_ids
                if self.evidence_bus.get(evidence_id) is not None]

    def to_dict(self):
        return {
            'contract': CASE_EVIDENCE_VIEW_CONTRACT, 'case_id': self.case_id,
            'version': self.version, 'accepted_evidence_ids': list(self.accepted_ids),
            'records': self.records(), 'rejections': list(self.rejections),
        }


class EvidenceBus(object):
    """Append-only in-memory map of verified execution evidence records."""

    def __init__(self, records=None):
        # Accept both the legacy record list and a serialized v2 bus.  The latter
        # retains case links/rejections across snapshot/replay boundaries.
        serialized = _as_dict(records)
        source_records = serialized.get('records') if serialized else records
        self.records = {}
        self.case_links = {}
        self.case_rejections = {}
        for record in _as_list(source_records):
            record = _as_dict(record)
            evidence_id = record.get('evidence_id')
            if evidence_id:
                self.records[evidence_id] = record
                for case_id in _as_list(record.get('linked_case_ids')):
                    self.link_case_evidence(case_id, evidence_id)
        for case_id, evidence_ids in _as_dict(serialized.get('case_links')).items():
            for evidence_id in _as_list(evidence_ids):
                self.link_case_evidence(case_id, evidence_id)
        for case_id, audits in _as_dict(serialized.get('case_rejections')).items():
            for audit in _as_list(audits):
                copied = dict(_as_dict(audit))
                copied.setdefault('case_id', case_id)
                self.record_case_rejection(copied)

    @classmethod
    def from_dict(cls, payload):
        return cls(payload)

    def record_envelope(self, envelope, producer_task_id=None, trace_id=None, graph_type=None,
                        case_id=None, case_namespace=None):
        envelope = _as_dict(envelope)
        if not is_verified_execution_envelope(envelope):
            return None
        evidence_id = envelope.get('evidence_id')
        provenance = _as_dict(envelope.get('provenance'))
        metadata = _as_dict(envelope.get('metadata'))
        compiled_sql = _as_dict(metadata.get('compiled_sql'))
        metric = (metadata.get('metric') or metadata.get('source_metric') or compiled_sql.get('metric'))
        dimensions = metadata.get('dimensions') if metadata.get('dimensions') is not None else compiled_sql.get('dimensions')
        record = dict(self.records.get(evidence_id) or {})
        record.update({
            'contract': EVIDENCE_RECORD_CONTRACT, 'evidence_id': evidence_id,
            'authority': envelope.get('authority'), 'status': envelope.get('status'),
            'query_id': envelope.get('query_id'), 'tool_call_id': envelope.get('tool_call_id'),
            'dataid': envelope.get('dataid'), 'data_version': envelope.get('data_version'),
            'row_count': envelope.get('row_count'), 'time_range': envelope.get('time_range'),
            'producer_task_id': producer_task_id or provenance.get('task_id'),
            'trace_id': trace_id or provenance.get('trace_id'), 'graph_type': graph_type,
            'metric': metric, 'dimensions': list(dimensions or []), 'filters': metadata.get('filters') or {},
            'tenant_id': metadata.get('tenant_id') or provenance.get('tenant_id'),
            'user_id': metadata.get('user_id') or provenance.get('user_id'),
            'permission_scope': metadata.get('permission_scope') or provenance.get('permission_scope'),
            'recorded_at': record.get('recorded_at') or time.time(), 'envelope': envelope,
            'case_namespace': case_namespace or record.get('case_namespace'),
            'linked_case_ids': list(record.get('linked_case_ids') or []),
        })
        self.records[evidence_id] = record
        if case_id:
            self.link_case_evidence(case_id, evidence_id)
        return record

    def case_view(self, case_id, expected_scope=None, ttl_seconds=None):
        return CaseEvidenceView(self, case_id, expected_scope=expected_scope, ttl_seconds=ttl_seconds)

    def link_case_evidence(self, case_id, evidence_id):
        if not case_id or evidence_id not in self.records:
            return False
        linked = self.case_links.setdefault(case_id, [])
        if evidence_id not in linked:
            linked.append(evidence_id)
        record = self.records[evidence_id]
        if case_id not in record.setdefault('linked_case_ids', []):
            record['linked_case_ids'].append(case_id)
        return True

    def record_case_rejection(self, audit):
        audit = _as_dict(audit)
        case_id = audit.get('case_id')
        if not case_id:
            return None
        items = self.case_rejections.setdefault(case_id, [])
        if audit not in items:
            items.append(dict(audit))
        return audit

    def get_case_rejections(self, case_id):
        return list(self.case_rejections.get(case_id, []))

    def get(self, evidence_id):
        return self.records.get(evidence_id)

    def has(self, evidence_id):
        return evidence_id in self.records

    def validate_ids(self, evidence_ids):
        valid, missing = [], []
        for evidence_id in _as_list(evidence_ids):
            (valid if evidence_id in self.records else missing).append(evidence_id)
        return valid, missing

    def validate_scope(self, evidence_ids, expected_scope=None, ttl_seconds=None, now=None):
        expected_scope = _as_dict(expected_scope)
        now = time.time() if now is None else float(now)
        valid, rejected = [], []
        for evidence_id in _as_list(evidence_ids):
            record = self.records.get(evidence_id)
            if not record:
                rejected.append({'evidence_id': evidence_id, 'error': 'missing_evidence_ref'})
                continue
            if ttl_seconds is not None:
                try:
                    age = now - float(record.get('recorded_at') or 0)
                except Exception:
                    age = None
                if age is None or age > float(ttl_seconds):
                    rejected.append({'evidence_id': evidence_id, 'error': 'evidence_ttl_expired'})
                    continue
            mismatches = _record_scope_mismatches(record, expected_scope)
            if mismatches:
                rejected.append({'evidence_id': evidence_id, 'error': 'evidence_scope_mismatch', 'fields': mismatches})
                continue
            valid.append(evidence_id)
        return valid, rejected

    def to_list(self):
        return [self.records[k] for k in sorted(self.records.keys())]

    def to_dict(self):
        return {'contract': 'evidence_bus_v2', 'records': self.to_list(),
                'case_links': dict((k, list(v)) for k, v in self.case_links.items()),
                'case_rejections': dict((k, list(v)) for k, v in self.case_rejections.items())}


def _normalise_scope_value(value):
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        return tuple([_normalise_scope_value(x) for x in value])
    if isinstance(value, dict):
        return tuple(sorted([(k, _normalise_scope_value(v)) for k, v in value.items()]))
    return value


def _record_scope_mismatches(record, expected_scope):
    record, expected_scope = _as_dict(record), _as_dict(expected_scope)
    if not expected_scope:
        return []
    mismatches = []
    # Keep this stable ordering because callers expose the rejected fields in
    # audit/replay payloads.
    # Keep this order stable for audit/replay assertions: metric scope is the
    # primary analytical guard, then time, then data/permission boundaries.
    expected = expected_scope.get('metric')
    if expected is not None and (record.get('metric') is None or expected != record.get('metric')):
        mismatches.append('metric')
    allowed_time_ranges = expected_scope.get('allowed_time_ranges') or []
    if allowed_time_ranges:
        actual = _normalise_scope_value(record.get('time_range'))
        allowed = [_normalise_scope_value(v) for v in allowed_time_ranges]
        if actual is None or actual not in allowed:
            mismatches.append('time_range')
    for field in ('dataid', 'data_version', 'tenant_id', 'user_id'):
        expected = expected_scope.get(field)
        if expected is not None and (record.get(field) is None or expected != record.get(field)):
            mismatches.append(field)
    for field in ('dimensions', 'filters', 'permission_scope'):
        expected, actual = expected_scope.get(field), record.get(field)
        if expected not in (None, {}, []):
            if actual in (None, {}, []) or _normalise_scope_value(expected) != _normalise_scope_value(actual):
                mismatches.append(field)
    return mismatches


def collect_evidence_from_graph_result(graph_result):
    graph_result, graph = _as_dict(graph_result), _as_dict(_as_dict(graph_result).get('graph'))
    bus = EvidenceBus()
    for task_id, result in _as_dict(graph_result.get('results')).items():
        output = _as_dict(_as_dict(result).get('output'))
        bus.record_envelope(output.get('execution_envelope'), producer_task_id=task_id,
                            trace_id=graph_result.get('trace_id'), graph_type=graph.get('graph_type'))
    return bus


__all__ = ['EvidenceBus', 'CaseEvidenceView', 'is_verified_execution_envelope',
           'collect_evidence_from_graph_result', 'VERIFIED_AUTHORITY']
