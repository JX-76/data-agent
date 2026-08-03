# -*- coding: utf-8 -*-
"""Redis-backed tenant-scoped cache/session adapter.

Requires explicit enablement and a Redis URL/client. Tests may inject a small
client object that implements get/setex/delete/keys.
"""
from __future__ import unicode_literals

import json
import time

from repository_contracts import RepositoryError, CacheRepository, SessionRepository


class RedisSessionCacheError(RepositoryError):
    pass


class RedisSessionCacheAdapter(CacheRepository, SessionRepository):
    def __init__(self, url=None, enabled=False, client=None, prefix="agent"):
        self.url = url or ""
        self.enabled = bool(enabled)
        self.prefix = prefix or "agent"
        if not self.enabled or (not self.url and client is None):
            raise RedisSessionCacheError("redis session cache is not configured; adapter fails closed")
        self.client = client or self._connect(self.url)

    def _connect(self, url):
        try:
            import redis
            return redis.Redis.from_url(url)
        except Exception as exc:
            raise RedisSessionCacheError("redis connection failed: %s" % exc)

    def _tenant(self, access):
        tenant = getattr(access, "tenant_id", None)
        if not tenant:
            raise RedisSessionCacheError("tenant_id is required")
        if not getattr(access, "verified", False):
            raise RedisSessionCacheError("verified access context is required")
        return tenant

    def _key(self, access, namespace, key):
        return "%s:%s:%s:%s" % (self.prefix, self._tenant(access), namespace, str(key))

    def set_value(self, access, key, payload, ttl_seconds=300):
        self.client.setex(self._key(access, "cache", key), int(ttl_seconds), json.dumps(payload or {}, sort_keys=True))

    def get_value(self, access, key):
        raw = self.client.get(self._key(access, "cache", key))
        if raw is None:
            return None
        if not isinstance(raw, str):
            raw = raw.decode("utf-8")
        return json.loads(raw)

    def delete_value(self, access, key):
        return bool(self.client.delete(self._key(access, "cache", key)))

    def save_session(self, access, session_id, payload):
        body = dict(payload or {})
        body.setdefault("updated_at", time.time())
        self.client.setex(self._key(access, "session", session_id), int(body.get("ttl_seconds") or 2592000), json.dumps(body, sort_keys=True))

    def get_session(self, access, session_id):
        raw = self.client.get(self._key(access, "session", session_id))
        if raw is None:
            return None
        if not isinstance(raw, str):
            raw = raw.decode("utf-8")
        return json.loads(raw)

    def list_sessions(self, access, limit=100):
        pattern = "%s:%s:session:*" % (self.prefix, self._tenant(access))
        keys = list(self.client.keys(pattern))[:int(limit)]
        result = []
        for key in keys:
            raw = self.client.get(key)
            if raw is not None:
                if not isinstance(raw, str):
                    raw = raw.decode("utf-8")
                result.append(json.loads(raw))
        return result


__all__ = ["RedisSessionCacheError", "RedisSessionCacheAdapter"]
