# -*- coding: utf-8 -*-
"""R23 access-control policy: role, tenant, table and field decisions.

The policy is deliberately small and deterministic.  It accepts plain dicts so
HTTP/API adapters can pass identity claims without coupling the agent core to an
identity provider.  Missing claims are safe-by-default for sensitive access.
"""
from __future__ import unicode_literals

SENSITIVE_FIELDS = frozenset((
    "user_id", "email", "phone", "mobile", "address", "ip_address", "client_ip",
    "password", "secret", "token", "api_key", "id_card", "salary",
))

# Query language may use product-facing Chinese labels rather than semantic
# column names. Map only known PII aliases to stable policy field identifiers.
# This keeps the policy deterministic and safe-by-default without introducing
# query understanding/business routing into AgentFacade.
SENSITIVE_FIELD_ALIASES = {
    "id_card": ("id_card", "idcard", "身份证", "身份证号", "身份号码"),
    "phone": ("phone", "mobile", "手机号", "手机号码", "电话", "联系电话"),
    "email": ("email", "e-mail", "邮箱", "电子邮箱", "邮件地址"),
    "address": ("address", "住址", "地址", "家庭地址", "联系地址"),
}


def detect_sensitive_fields(query="", fields=None):
    """Return canonical sensitive fields explicitly requested by a query/plan."""
    requested = set(fields or []).intersection(SENSITIVE_FIELDS)
    text = (query or "").lower()
    for canonical, aliases in SENSITIVE_FIELD_ALIASES.items():
        if any(alias.lower() in text for alias in aliases):
            requested.add(canonical)
    return requested


class AccessContext(object):
    def __init__(self, user_id="anonymous", role="anonymous", tenant_id="default",
                 permissions=None, authenticated=False, metadata=None):
        self.user_id = user_id or "anonymous"
        self.role = role or "anonymous"
        self.tenant_id = tenant_id or "default"
        self.permissions = permissions or {}
        self.authenticated = bool(authenticated)
        self.metadata = metadata or {}

    @property
    def quota_key(self):
        return "%s:%s" % (self.tenant_id, self.user_id)

    @classmethod
    def from_value(cls, value=None, fallback="anonymous"):
        if isinstance(value, cls):
            return value
        value = value or {}
        if not isinstance(value, dict):
            return cls(user_id=fallback)
        return cls(user_id=value.get("user_id") or fallback,
                   role=value.get("role") or "anonymous",
                   tenant_id=value.get("tenant_id") or "default",
                   permissions=value.get("permissions") or {},
                   authenticated=value.get("authenticated", False),
                   metadata=value.get("metadata") or {})

    def to_dict(self):
        return {"user_id": self.user_id, "role": self.role,
                "tenant_id": self.tenant_id, "permissions": dict(self.permissions),
                "authenticated": self.authenticated, "quota_key": self.quota_key,
                "metadata": dict(self.metadata)}


class PermissionDecision(object):
    def __init__(self, allowed, decision="allowed", reason="", tables=None,
                 fields=None, masked_fields=None, requires_human_review=False):
        self.allowed = bool(allowed)
        self.decision = decision
        self.reason = reason
        self.tables = list(tables or [])
        self.fields = list(fields or [])
        self.masked_fields = list(masked_fields or [])
        self.requires_human_review = bool(requires_human_review)

    def to_dict(self):
        return {"allowed": self.allowed, "decision": self.decision,
                "reason": self.reason, "tables": list(self.tables),
                "fields": list(self.fields), "masked_fields": list(self.masked_fields),
                "requires_human_review": self.requires_human_review,
                "policy_id": "permission_policy_v1"}


class PermissionPolicy(object):
    """Role policy with conservative treatment of explicit sensitive access."""
    def _allowed(self, configured, value):
        return configured in (None, "*") or value in configured

    def evaluate(self, access_context=None, plan=None, query=""):
        ctx = AccessContext.from_value(access_context)
        plan = plan or {}
        permissions = ctx.permissions or {}
        tables = list(plan.get("tables") or ([] if not plan.get("model") else [plan.get("model")]))
        fields = list(plan.get("fields") or plan.get("dimensions") or [])
        # metric expression access is managed by semantic catalog, but record it.
        if plan.get("metric"):
            fields.append(plan.get("metric"))
        explicit = bool(permissions)
        denied_tables = set(permissions.get("denied_tables") or [])
        denied_fields = set(permissions.get("denied_fields") or [])
        allowed_tables = permissions.get("allowed_tables")
        allowed_fields = permissions.get("allowed_fields")
        for table in tables:
            if table in denied_tables or (explicit and not self._allowed(allowed_tables, table)):
                return PermissionDecision(False, "blocked", "table_access_denied: %s" % table, tables, fields)
        for field in fields:
            if field in denied_fields:
                return PermissionDecision(False, "blocked", "field_access_denied: %s" % field, tables, fields)
            if explicit and not self._allowed(allowed_fields, field):
                return PermissionDecision(False, "blocked", "field_access_denied: %s" % field, tables, fields)
        query_low = (query or "").lower()
        requested_sensitive = detect_sensitive_fields(query, fields)
        # Sensitive source data is never returned raw. Explicit privileged roles
        # may request review, but the output boundary remains masked.
        if requested_sensitive:
            if ctx.role not in ("admin", "security_admin"):
                return PermissionDecision(False, "pending_human_review", "sensitive_field_review_required",
                                          tables, fields, sorted(requested_sensitive), True)
            return PermissionDecision(True, "masked", "sensitive_fields_masked", tables, fields,
                                      sorted(requested_sensitive))
        if "导出" in query or "export" in query_low or "明细" in query:
            if ctx.role not in ("admin", "analyst", "data_steward"):
                return PermissionDecision(False, "pending_human_review", "export_or_detail_review_required",
                                          tables, fields, [], True)
        return PermissionDecision(True, "allowed", "role_policy_allowed", tables, fields)

    def inject_tenant_filter(self, plan, access_context=None):
        """Return a copy of plan with an auditable, non-overridable tenant filter."""
        ctx = AccessContext.from_value(access_context)
        if not ctx.tenant_id or ctx.tenant_id == "default":
            return plan
        data = dict(plan or {})
        filters = list(data.get("filters") or [])
        predicate = {"field": "tenant_id", "operator": "=", "value": ctx.tenant_id,
                     "source": "governance.tenant_filter"}
        filters = [item for item in filters if not (isinstance(item, dict) and item.get("field") == "tenant_id")]
        filters.append(predicate)
        data["filters"] = filters
        diagnostics = dict(data.get("diagnostics") or {})
        diagnostics["tenant_filter"] = predicate
        data["diagnostics"] = diagnostics
        return data


__all__ = ["AccessContext", "PermissionDecision", "PermissionPolicy", "SENSITIVE_FIELDS",
           "SENSITIVE_FIELD_ALIASES", "detect_sensitive_fields"]
