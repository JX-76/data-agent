# -*- coding: utf-8 -*-
"""Repository contracts for phase 2 state foundations.

These contracts define the trusted access context and the minimal repository
surface for session, memory, clarification, human review, audit, and cache
state. Implementations may be in-memory, SQLite, Postgres, Redis, or other
adapters, but they must keep tenant scope explicit and fail closed when
configuration is missing.
"""
from __future__ import unicode_literals


class RepositoryError(Exception):
    pass


class RepositoryAccessContext(object):
    def __init__(self, user_id=None, tenant_id=None, roles=None, verified=False, source="unknown"):
        self.user_id = user_id or "anonymous"
        self.tenant_id = tenant_id or "default"
        self.roles = list(roles or [])
        self.verified = bool(verified)
        self.source = source or "unknown"

    def to_dict(self):
        return {
            "user_id": self.user_id,
            "tenant_id": self.tenant_id,
            "roles": list(self.roles),
            "verified": self.verified,
            "source": self.source,
        }


class _RepositoryBase(object):
    def _not_implemented(self, method_name):
        raise NotImplementedError("%s must be implemented by a repository adapter" % method_name)


class SessionRepository(_RepositoryBase):
    def save_session(self, access, session_id, payload):
        self._not_implemented("save_session")

    def get_session(self, access, session_id):
        self._not_implemented("get_session")

    def list_sessions(self, access, limit=100):
        self._not_implemented("list_sessions")


class MemoryRepository(_RepositoryBase):
    def save_memory(self, access, memory_id, payload):
        self._not_implemented("save_memory")

    def get_memory(self, access, memory_id):
        self._not_implemented("get_memory")

    def list_memory(self, access, limit=100):
        self._not_implemented("list_memory")

    def delete_expired_memory(self, access, now=None):
        self._not_implemented("delete_expired_memory")


class ClarificationRepository(_RepositoryBase):
    def save_clarification(self, access, session_id, payload):
        self._not_implemented("save_clarification")

    def get_clarification(self, access, session_id):
        self._not_implemented("get_clarification")

    def delete_clarification(self, access, session_id):
        self._not_implemented("delete_clarification")


class HumanReviewRepository(_RepositoryBase):
    def save_review(self, access, session_id, payload):
        self._not_implemented("save_review")

    def get_review(self, access, session_id):
        self._not_implemented("get_review")

    def delete_review(self, access, session_id):
        self._not_implemented("delete_review")


class AuditRepository(_RepositoryBase):
    def append_event(self, access, event_id, payload):
        self._not_implemented("append_event")

    def list_events(self, access, limit=100):
        self._not_implemented("list_events")

    def verify_chain(self, access):
        self._not_implemented("verify_chain")


class CacheRepository(_RepositoryBase):
    def set_value(self, access, key, payload, ttl_seconds=300):
        self._not_implemented("set_value")

    def get_value(self, access, key):
        self._not_implemented("get_value")

    def delete_value(self, access, key):
        self._not_implemented("delete_value")


class EvidenceRepository(_RepositoryBase):
    def save_evidence(self, access, session_id, evidence_id, payload):
        self._not_implemented("save_evidence")

    def get_evidence(self, access, session_id, evidence_id):
        self._not_implemented("get_evidence")

    def list_evidence(self, access, session_id, limit=100):
        self._not_implemented("list_evidence")

    def delete_expired_evidence(self, access, now=None):
        self._not_implemented("delete_expired_evidence")


__all__ = [
    "RepositoryError", "RepositoryAccessContext",
    "SessionRepository", "MemoryRepository", "ClarificationRepository",
    "HumanReviewRepository", "AuditRepository", "CacheRepository", "EvidenceRepository",
]
