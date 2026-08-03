# -*- coding: utf-8 -*-
"""Persistent verified evidence store adapters.

The store is intentionally narrow: it persists only EvidenceBus verified records
and reloads them by tenant/session scope.  It does not validate claims itself;
EvidenceBus remains the runtime validator for existence, TTL and scope.
"""
from __future__ import unicode_literals

import time


def _as_dict(value):
    if isinstance(value, dict):
        return value
    if hasattr(value, 'to_dict'):
        try:
            return value.to_dict()
        except Exception:
            return {}
    return {}


class InMemoryEvidenceStore(object):
    """Small tenant/session scoped evidence store for tests and local runtime."""

    def __init__(self):
        self.records = {}

    def _tenant(self, tenant_id):
        return tenant_id or 'default'

    def _scope_key(self, tenant_id, session_id, evidence_id):
        return (self._tenant(tenant_id), session_id or 'default', evidence_id)

    def save_record(self, tenant_id, session_id, record):
        record = dict(_as_dict(record))
        evidence_id = record.get('evidence_id')
        if not evidence_id:
            return None
        record.setdefault('persisted_at', time.time())
        self.records[self._scope_key(tenant_id, session_id, evidence_id)] = record
        return record

    def get_record(self, tenant_id, session_id, evidence_id):
        return self.records.get(self._scope_key(tenant_id, session_id, evidence_id))

    def list_records(self, tenant_id, session_id, limit=100):
        tenant = self._tenant(tenant_id)
        session = session_id or 'default'
        values = [record for (t, s, unused_eid), record in self.records.items()
                  if t == tenant and s == session]
        values.sort(key=lambda item: item.get('recorded_at') or item.get('persisted_at') or 0, reverse=True)
        return values[:int(limit)]


__all__ = ['InMemoryEvidenceStore']
