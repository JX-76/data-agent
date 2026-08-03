# -*- coding: utf-8 -*-
"""Identity extraction helpers for production governance.

The agent core is framework-agnostic, so this module accepts a plain headers dict
and resolves a stable identity used by governance, quota and audit logging.
"""

import hashlib


TRUSTED_IDENTITY_HEADERS = [
    "X-User-ID",
    "X-User-Id",
    "X-Authenticated-User",
    "X-Forwarded-User",
]
API_KEY_HEADERS = ["X-API-Key", "Authorization"]


class IdentityContext(object):
    def __init__(self, user_id="anonymous", tenant_id="default", source="anonymous", authenticated=False, metadata=None):
        self.user_id = user_id or "anonymous"
        self.tenant_id = tenant_id or "default"
        self.source = source or "anonymous"
        self.authenticated = bool(authenticated)
        self.metadata = metadata or {}

    @property
    def quota_key(self):
        return "%s:%s" % (self.tenant_id, self.user_id)

    def to_dict(self):
        return {
            "user_id": self.user_id,
            "tenant_id": self.tenant_id,
            "source": self.source,
            "authenticated": self.authenticated,
            "quota_key": self.quota_key,
            "metadata": dict(self.metadata),
        }


def _header(headers, name):
    headers = headers or {}
    if name in headers:
        return headers.get(name)
    low = name.lower()
    for key, value in headers.items():
        if str(key).lower() == low:
            return value
    return None


def _hash_token(token):
    if not token:
        return "anonymous"
    text = token.encode("utf-8") if isinstance(token, unicode) else str(token).encode("utf-8")
    return hashlib.sha256(text).hexdigest()[:16]


try:
    unicode
except NameError:
    unicode = str


def resolve_identity(headers=None, fallback="anonymous"):
    headers = headers or {}
    tenant_id = _header(headers, "X-Tenant-ID") or _header(headers, "X-Tenant-Id") or "default"
    for name in TRUSTED_IDENTITY_HEADERS:
        value = _header(headers, name)
        if value:
            return IdentityContext(
                user_id=str(value),
                tenant_id=str(tenant_id),
                source=name,
                authenticated=True,
                metadata={"identity_header": name},
            )
    for name in API_KEY_HEADERS:
        value = _header(headers, name)
        if value:
            token = str(value).replace("Bearer ", "", 1).strip()
            return IdentityContext(
                user_id="api_key:%s" % _hash_token(token),
                tenant_id=str(tenant_id),
                source=name,
                authenticated=True,
                metadata={"identity_header": name, "hashed": True},
            )
    return IdentityContext(user_id=fallback or "anonymous", tenant_id=str(tenant_id), source="fallback", authenticated=False)


__all__ = ["IdentityContext", "resolve_identity", "TRUSTED_IDENTITY_HEADERS", "API_KEY_HEADERS"]
