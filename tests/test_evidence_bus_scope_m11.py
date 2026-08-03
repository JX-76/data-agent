# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'src')
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from contracts import build_execution_envelope
from evidence_bus import EvidenceBus


def _recorded_bus(**overrides):
    metadata = {
        'metric': 'gmv',
        'dimensions': ['channel'],
        'filters': {'region': 'cn'},
    }
    metadata.update(overrides.pop('metadata', {}) or {})
    envelope = build_execution_envelope(
        status='ok', stage='db_execute', query_id='q_scope', evidence_id='ev_scope',
        dataid='orders', data_version='v1', row_count=1, time_range='last_7_days',
        authority='verified_execution', metadata=metadata)
    bus = EvidenceBus()
    record = bus.record_envelope(envelope, producer_task_id='data_analyst', trace_id='trace-scope', graph_type='metric_query')
    if 'recorded_at' in overrides:
        record['recorded_at'] = overrides['recorded_at']
    return bus


def test_evidence_bus_validate_scope_accepts_matching_current_evidence():
    bus = _recorded_bus(recorded_at=100.0)

    valid, rejected = bus.validate_scope(['ev_scope'], expected_scope={
        'metric': 'gmv',
        'allowed_time_ranges': ['last_7_days'],
        'dimensions': ['channel'],
        'filters': {'region': 'cn'},
        'dataid': 'orders',
        'data_version': 'v1',
    }, ttl_seconds=60, now=120.0)

    assert valid == ['ev_scope']
    assert rejected == []


def test_evidence_bus_v2_serialization_preserves_case_namespace_and_rejections():
    bus = _recorded_bus(recorded_at=100.0)
    view_a = bus.case_view('case-a')
    view_b = bus.case_view('case-b')
    view_a.link(['ev_scope'], expected_scope={
        'metric': 'gmv',
        'allowed_time_ranges': ['last_7_days'],
        'dimensions': ['channel'],
        'filters': {'region': 'cn'},
        'dataid': 'orders',
        'data_version': 'v1',
    }, now=120.0)
    view_b.link(['ev_scope'], expected_scope={'metric': 'orders'}, now=120.0)

    restored = EvidenceBus.from_dict(bus.to_dict())
    restored_a = restored.case_view('case-a')
    restored_b = restored.case_view('case-b')

    assert restored_a.to_dict()['accepted_evidence_ids'] == ['ev_scope']
    assert restored_a.records()[0]['linked_case_ids'] == ['case-a']
    assert restored_b.to_dict()['accepted_evidence_ids'] == []
    assert restored_b.to_dict()['rejections'][0]['reason'] == 'rejected_scope_mismatch'
    assert 'ev_scope' in restored.records


def test_evidence_bus_validate_scope_rejects_missing_refs_and_mismatched_scope():
    bus = _recorded_bus()

    valid, rejected = bus.validate_scope(['missing', 'ev_scope'], expected_scope={
        'metric': 'orders',
        'allowed_time_ranges': ['last_7_days'],
        'dimensions': ['channel'],
        'filters': {'region': 'cn'},
        'dataid': 'orders',
        'data_version': 'v1',
    })

    assert valid == []
    assert rejected[0]['error'] == 'missing_evidence_ref'
    assert rejected[1]['error'] == 'evidence_scope_mismatch'
    assert rejected[1]['fields'] == ['metric']


def test_evidence_bus_validate_scope_rejects_ttl_expired_evidence():
    bus = _recorded_bus(recorded_at=10.0)

    valid, rejected = bus.validate_scope(['ev_scope'], expected_scope={
        'metric': 'gmv',
        'allowed_time_ranges': ['last_7_days'],
    }, ttl_seconds=30, now=100.0)

    assert valid == []
    assert rejected == [{'evidence_id': 'ev_scope', 'error': 'evidence_ttl_expired'}]


def test_evidence_bus_validate_scope_rejects_time_dimension_filter_and_version_drift():
    bus = _recorded_bus()

    valid, rejected = bus.validate_scope(['ev_scope'], expected_scope={
        'metric': 'gmv',
        'allowed_time_ranges': ['previous_7_days'],
        'dimensions': ['category'],
        'filters': {'region': 'us'},
        'dataid': 'orders_archive',
        'data_version': 'v2',
    })

    assert valid == []
    assert rejected[0]['error'] == 'evidence_scope_mismatch'
    assert rejected[0]['fields'] == ['time_range', 'dataid', 'data_version', 'dimensions', 'filters']


def test_evidence_bus_does_not_record_unverified_or_failed_execution():
    bus = EvidenceBus()
    failed = build_execution_envelope(
        status='error', stage='db_execute', query_id='q_failed', evidence_id='ev_failed',
        dataid='orders', data_version='v1', row_count=0, time_range='last_7_days',
        authority='unverified')

    assert bus.record_envelope(failed) is None
    valid, rejected = bus.validate_scope(['ev_failed'], expected_scope={'metric': 'gmv'})

    assert valid == []
    assert rejected == [{'evidence_id': 'ev_failed', 'error': 'missing_evidence_ref'}]


def test_evidence_bus_validate_scope_rejects_tenant_user_permission_drift():
    bus = _recorded_bus(metadata={
        'tenant_id': 'tenant_a',
        'user_id': 'user_a',
        'permission_scope': {'regions': ['cn'], 'role': 'analyst'},
    })

    valid, rejected = bus.validate_scope(['ev_scope'], expected_scope={
        'metric': 'gmv',
        'allowed_time_ranges': ['last_7_days'],
        'tenant_id': 'tenant_b',
        'user_id': 'user_b',
        'permission_scope': {'regions': ['us'], 'role': 'analyst'},
    })

    assert valid == []
    assert rejected[0]['error'] == 'evidence_scope_mismatch'
    assert rejected[0]['fields'] == ['tenant_id', 'user_id', 'permission_scope']


def test_evidence_bus_validate_scope_accepts_matching_security_scope():
    bus = _recorded_bus(metadata={
        'tenant_id': 'tenant_a',
        'user_id': 'user_a',
        'permission_scope': {'regions': ['cn'], 'role': 'analyst'},
    })

    valid, rejected = bus.validate_scope(['ev_scope'], expected_scope={
        'metric': 'gmv',
        'allowed_time_ranges': ['last_7_days'],
        'tenant_id': 'tenant_a',
        'user_id': 'user_a',
        'permission_scope': {'regions': ['cn'], 'role': 'analyst'},
    })

    assert valid == ['ev_scope']
    assert rejected == []
