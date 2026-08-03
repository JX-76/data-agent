# -*- coding: utf-8 -*-
"""Result cache for the Data Agent.

Provides an LRU cache with TTL and explicit scope validation so identical
queries do not re-execute within the cache window while preserving tenant,
permission, data-version and masking boundaries.
Python 2.7 compatible.
"""

import copy
import hashlib
import json
import threading
import time

try:
    unicode
except NameError:  # Python 3
    unicode = str


_CACHE_CONTRACT = "result_cache_scope_v2"


def _to_unicode(v):
    if v is None:
        return u""
    if isinstance(v, unicode):
        return v
    if isinstance(v, bytes):
        return v.decode("utf-8", "ignore")
    try:
        return unicode(v, errors="ignore")
    except TypeError:
        return unicode(v)


def _stable_json(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=_to_unicode)
    except Exception:
        return _to_unicode(value)


def _hash(value):
    raw = _stable_json(value)
    if isinstance(raw, unicode):
        raw = raw.encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _as_dict(value):
    return value if isinstance(value, dict) else {}


class CacheScope(object):
    """Stable, non-sensitive cache isolation descriptor."""

    def __init__(self, query=None, plan_hash=None, tenant_id=None, user_id=None,
                 role=None, access_scope=None, dataid=None, data_version=None,
                 semantic_version=None, masking_policy=None, evidence_authority=None):
        self.query_hash = _hash(query or u"")
        self.plan_hash = _to_unicode(plan_hash or u"")
        self.tenant_id = _to_unicode(tenant_id or "default")
        self.user_id = _to_unicode(user_id or "anonymous")
        self.role = _to_unicode(role or "unknown")
        self.access_scope_hash = _hash(access_scope or {"tenant_id": self.tenant_id, "role": self.role})
        self.dataid = _to_unicode(dataid or "")
        self.data_version = _to_unicode(data_version or "")
        self.semantic_version = _to_unicode(semantic_version or "")
        self.masking_policy_hash = _hash(masking_policy or {})
        self.evidence_authority = _to_unicode(evidence_authority or "verified_execution")

    @classmethod
    def from_context(cls, query=None, plan_hash=None, access_context=None,
                     provenance=None, result=None, semantic_version=None,
                     masking_policy=None):
        access_context = _as_dict(access_context)
        provenance = _as_dict(provenance)
        result = _as_dict(result)
        execution = _as_dict(provenance.get("execution"))
        semantic = _as_dict(provenance.get("semantic"))
        envelope = _as_dict(result.get("execution_envelope"))
        access_scope = {
            "tenant_id": access_context.get("tenant_id"),
            "user_id": access_context.get("user_id"),
            "role": access_context.get("role"),
            "quota_key": access_context.get("quota_key"),
            "allowed_tables": access_context.get("allowed_tables") or access_context.get("tables"),
            "masked_fields": access_context.get("masked_fields") or (_as_dict(access_context.get("metadata")).get("masked_fields")),
        }
        return cls(
            query=query,
            plan_hash=plan_hash,
            tenant_id=access_context.get("tenant_id"),
            user_id=access_context.get("user_id"),
            role=access_context.get("role"),
            access_scope=access_scope,
            dataid=result.get("dataid") or envelope.get("dataid") or execution.get("dataid") or access_context.get("dataid"),
            data_version=result.get("data_version") or envelope.get("data_version") or execution.get("data_version") or access_context.get("data_version"),
            semantic_version=semantic_version or semantic.get("semantic_version") or result.get("semantic_version") or access_context.get("semantic_version"),
            masking_policy=masking_policy or access_scope.get("masked_fields") or {},
            evidence_authority=result.get("authority") or envelope.get("authority"),
        )

    def to_dict(self):
        return {
            "contract": _CACHE_CONTRACT,
            "query_hash": self.query_hash,
            "plan_hash": self.plan_hash,
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "role": self.role,
            "access_scope_hash": self.access_scope_hash,
            "dataid": self.dataid,
            "data_version": self.data_version,
            "semantic_version": self.semantic_version,
            "masking_policy_hash": self.masking_policy_hash,
            "evidence_authority": self.evidence_authority,
        }

    def key(self):
        data = self.to_dict()
        return hashlib.sha256(_stable_json(data).encode("utf-8")).hexdigest()

    def compatible_with(self, other):
        other = other.to_dict() if hasattr(other, "to_dict") else _as_dict(other)
        current = self.to_dict()
        mismatches = []
        for field in ("query_hash", "plan_hash", "tenant_id", "user_id", "role",
                      "access_scope_hash", "dataid", "data_version",
                      "semantic_version", "masking_policy_hash"):
            if current.get(field) != other.get(field):
                mismatches.append(field)
        if current.get("evidence_authority") != "verified_execution":
            mismatches.append("evidence_authority")
        return {"compatible": not mismatches, "mismatches": mismatches}


class CacheEntry(object):
    def __init__(self, scope, result, created_at=None, ttl_seconds=300):
        self.scope = scope.to_dict() if hasattr(scope, "to_dict") else dict(scope or {})
        self.result = copy.deepcopy(result)
        self.created_at = created_at if created_at is not None else time.time()
        self.ttl_seconds = ttl_seconds

    def expired(self, now=None):
        now = time.time() if now is None else now
        return (now - self.created_at) >= self.ttl_seconds

    def to_dict(self):
        return {"contract": "result_cache_entry_v2", "scope": self.scope,
                "created_at": self.created_at, "ttl_seconds": self.ttl_seconds,
                "result": copy.deepcopy(self.result)}


class CacheDecision(object):
    def __init__(self, action, reason=None, key=None, mismatches=None):
        self.action = action
        self.reason = reason
        self.key = key
        self.mismatches = list(mismatches or [])

    def to_dict(self):
        return {"contract": "result_cache_decision_v2", "action": self.action,
                "reason": self.reason, "key_hash": self.key,
                "mismatches": list(self.mismatches)}


class ResultCache(object):
    """LRU cache with TTL and explicit scope validation for query results."""

    def __init__(self, max_size=100, ttl_seconds=300, clock=None):
        self.max_size = max_size
        self.ttl_seconds = ttl_seconds
        self._cache = {}  # key -> CacheEntry
        self._clock = clock or time.time
        self.last_decision = None
        self._lock = threading.RLock()

    def _make_key(self, query, plan_hash=None):
        raw = _to_unicode(query) + u":" + _to_unicode(plan_hash)
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    def _scope_from_legacy(self, query, plan_hash=None):
        return CacheScope(query=query, plan_hash=plan_hash)

    def get(self, query, plan_hash=None, scope=None, return_decision=False):
        """Return cached result if available, not expired, and scope-compatible."""
        scope_obj = scope if hasattr(scope, "to_dict") else (CacheScope(**scope) if isinstance(scope, dict) and "query" in scope else None)
        if scope_obj is None:
            scope_obj = self._scope_from_legacy(query, plan_hash)
        key = scope_obj.key() if hasattr(scope_obj, "key") else self._make_key(query, plan_hash)
        with self._lock:
            entry = self._cache.get(key)
        if entry is None:
            decision = CacheDecision("miss", "not_found", key)
            self.last_decision = decision.to_dict()
            return (None, self.last_decision) if return_decision else None
        if entry.expired(self._clock()):
            with self._lock:
                if key in self._cache:
                    del self._cache[key]
            decision = CacheDecision("invalidate", "ttl_expired", key)
            self.last_decision = decision.to_dict()
            return (None, self.last_decision) if return_decision else None
        compatibility = scope_obj.compatible_with(entry.scope) if hasattr(scope_obj, "compatible_with") else {"compatible": True, "mismatches": []}
        if not compatibility.get("compatible"):
            decision = CacheDecision("reject", "scope_mismatch", key, compatibility.get("mismatches") or [])
            self.last_decision = decision.to_dict()
            return (None, self.last_decision) if return_decision else None
        result = copy.deepcopy(entry.result)
        decision = CacheDecision("hit", "scope_match", key)
        self.last_decision = decision.to_dict()
        return (result, self.last_decision) if return_decision else result

    def cacheable(self, result):
        result = _as_dict(result)
        if result.get("status") != "ok":
            return False, "non_ok_status"
        if result.get("authority") not in (None, "verified_execution"):
            return False, "unverified_authority"
        envelope = _as_dict(result.get("execution_envelope"))
        if envelope and envelope.get("authority") != "verified_execution":
            return False, "unverified_execution_envelope"
        diagnostics = _as_dict(result.get("diagnostics"))
        if diagnostics.get("evidence_limited") or diagnostics.get("failure_type"):
            return False, "diagnostic_failure"
        return True, "cacheable"

    def set(self, query, result, plan_hash=None, scope=None):
        """Store verified successful result in cache. Returns decision dict."""
        ok, reason = self.cacheable(result)
        scope_obj = scope if hasattr(scope, "to_dict") else None
        if scope_obj is None:
            scope_obj = self._scope_from_legacy(query, plan_hash)
        key = scope_obj.key() if hasattr(scope_obj, "key") else self._make_key(query, plan_hash)
        if not ok:
            decision = CacheDecision("reject", reason, key)
            self.last_decision = decision.to_dict()
            return self.last_decision
        with self._lock:
            self._cache[key] = CacheEntry(scope_obj, result, created_at=self._clock(), ttl_seconds=self.ttl_seconds)
            if len(self._cache) > self.max_size:
                oldest_key = min(self._cache.keys(), key=lambda k: self._cache[k].created_at)
                del self._cache[oldest_key]
        decision = CacheDecision("store", "cacheable", key)
        self.last_decision = decision.to_dict()
        return self.last_decision

    def clear(self):
        with self._lock:
            self._cache.clear()

    def size(self):
        with self._lock:
            return len(self._cache)


__all__ = ["ResultCache", "CacheScope", "CacheEntry", "CacheDecision"]
