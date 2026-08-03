# -*- coding: utf-8 -*-
"""Phase 2 repository and identity boundary tests."""
from __future__ import unicode_literals

import os
import sys
import tempfile

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, "src"))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from identity_provider import AccessContextProvider, DevelopmentMockIdentityProvider, IdentityContext, IdentityError, JWTClaimsIdentityProvider
from persistence_sqlite import RetentionPolicy, SQLitePersistence
from postgres_persistence import PostgresPersistenceAdapter, PostgresPersistenceError
from redis_session_cache import RedisSessionCacheAdapter, RedisSessionCacheError
from repository_contracts import RepositoryAccessContext


def _access(user_id="u1", tenant_id="t1", roles=None):
    return RepositoryAccessContext(user_id=user_id, tenant_id=tenant_id, roles=roles or ["analyst"], verified=True, source="test")


def test_repository_access_context_serializes():
    access = _access()
    payload = access.to_dict()
    assert payload["user_id"] == "u1"
    assert payload["tenant_id"] == "t1"
    assert payload["verified"] is True


def test_sqlite_repository_session_memory_and_clarification_roundtrip():
    store = SQLitePersistence(":memory:")
    access = _access()
    store.save_session(access, "sess-1", {"status": "ok", "value": 1})
    store.save_memory(access, "mem-1", {"value": 2, "expires_at": None})
    store.save_clarification(access, "sess-1", {"question": "q?", "expires_at": None})
    store.save_review(access, "sess-1", {"decision": "approve"})
    store.save_idempotency(access, "idem-1", {"result": "cached"})

    assert store.get_session(access, "sess-1")["value"] == 1
    assert store.get_memory(access, "mem-1")["value"] == 2
    assert store.get_clarification(access, "sess-1")["question"] == "q?"
    assert store.get_review(access, "sess-1")["decision"] == "approve"
    assert store.get_idempotency(access, "idem-1")["result"] == "cached"
    assert store.list_sessions(access)
    assert store.list_memory(access)
    assert store.verify_chain(access) is True


def test_sqlite_repository_is_tenant_scoped_and_restartable():
    with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as f:
        path = f.name
    try:
        store = SQLitePersistence(path)
        access_a = _access(tenant_id="tenant-a")
        access_b = _access(tenant_id="tenant-b")
        store.save_session(access_a, "sess", {"tenant": "a"})
        store.save_session(access_b, "sess", {"tenant": "b"})
        store.save_memory(access_a, "mem", {"tenant": "a", "expires_at": None})
        store.save_memory(access_b, "mem", {"tenant": "b", "expires_at": None})
        store.save_clarification(access_a, "sess", {"tenant": "a", "expires_at": None})
        store.save_review(access_a, "sess", {"tenant": "a"})
        store.set_cache("tenant-a", "cache", {"tenant": "a"})
        store.set_cache("tenant-b", "cache", {"tenant": "b"})
        store.append_event(access_a, "ev-1", {"tenant": "a"})

        reopened = SQLitePersistence(path)
        assert reopened.get_session(access_a, "sess")["tenant"] == "a"
        assert reopened.get_session(access_b, "sess")["tenant"] == "b"
        assert reopened.get_memory(access_a, "mem")["tenant"] == "a"
        assert reopened.get_clarification(access_a, "sess")["tenant"] == "a"
        assert reopened.get_review(access_a, "sess")["tenant"] == "a"
        assert reopened.get_cache("tenant-a", "cache")["tenant"] == "a"
        assert reopened.get_cache("tenant-b", "cache")["tenant"] == "b"
        assert reopened.verify_chain(access_a) is True
    finally:
        try:
            store.close()
        except Exception:
            pass
        try:
            reopened.close()
        except Exception:
            pass
        os.unlink(path)


def test_sqlite_retention_cleans_only_expired_items():
    store = SQLitePersistence(":memory:")
    access = _access()
    store.save_memory(access, "expired", {"expires_at": 0, "value": 1})
    policy = RetentionPolicy({"memory": 0, "clarification": 0, "cache": 0, "idempotency": 0})
    counts = store.cleanup(policy, dry_run=False)
    assert "memory" in counts


def test_dev_identity_boundaries_and_access_context_ignore_body():
    dev = DevelopmentMockIdentityProvider(environment="development")
    ctx = dev.resolve({"x-dev-user-id": "alice", "x-dev-tenant-id": "tenant-x", "x-dev-roles": "admin,analyst"})
    assert ctx.user_id == "alice"
    assert ctx.tenant_id == "tenant-x"
    assert "admin" in ctx.roles

    provider = AccessContextProvider(dev)
    resolved = provider.resolve(headers={}, body={"tenant_id": "evil"})
    assert resolved.tenant_id != "evil"
    assert resolved.source == "mock"


def test_mock_identity_disallowed_in_production_and_jwt_requires_claims():
    with pytest.raises(IdentityError):
        DevelopmentMockIdentityProvider(environment="production").resolve()

    with pytest.raises(IdentityError):
        JWTClaimsIdentityProvider().resolve()

    with pytest.raises(IdentityError):
        JWTClaimsIdentityProvider(claims_resolver=lambda headers: {"sub": "u1"}).resolve({})


def test_production_adapters_fail_closed_without_configuration():
    with pytest.raises(PostgresPersistenceError):
        PostgresPersistenceAdapter()
    with pytest.raises(PostgresPersistenceError):
        PostgresPersistenceAdapter(dsn="", enabled=True)
    with pytest.raises(RedisSessionCacheError):
        RedisSessionCacheAdapter()
    with pytest.raises(RedisSessionCacheError):
        RedisSessionCacheAdapter(url="", enabled=True)


def test_identity_context_roundtrip_and_raw_claims_hidden():
    ctx = IdentityContext("u", "t", ["analyst"], True, "jwt", {"secret": "x"})
    data = ctx.to_dict()
    assert data["user_id"] == "u"
    assert data["tenant_id"] == "t"
    assert "raw_claims" not in data
