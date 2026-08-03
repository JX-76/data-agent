# -*- coding: utf-8 -*-
"""Unified governance facade.

This module keeps security / compliance / tenancy controls behind a small
stable API so future product features can extend policies without rewriting
call sites.

The repository still contains some newer modules that are not Python-2 syntax
compatible, so this facade loads them defensively and falls back to small local
implementations when needed.
"""

try:
    from audit_logger import audit_logger
except Exception:
    class _AuditLogger(object):
        def log(self, *args, **kwargs):
            return None

        def log_query(self, *args, **kwargs):
            return None

        def log_event(self, *args, **kwargs):
            return None

    audit_logger = _AuditLogger()

try:
    from masking import get_masker
except Exception:
    class _FallbackMasker(object):
        def mask_rows(self, rows):
            safe_rows = []
            for row in rows or []:
                if not isinstance(row, dict):
                    safe_rows.append(row)
                    continue
                safe_row = dict(row)
                for key in list(safe_row.keys()):
                    low = str(key).lower()
                    if low in ("user_id", "email", "phone", "mobile", "ip_address", "client_ip", "password", "secret", "token", "api_key") or "email" in low:
                        safe_row[key] = self._mask_value(safe_row[key], key)
                safe_rows.append(safe_row)
            return safe_rows

        def mask_dict(self, data, columns=None):
            if not isinstance(data, dict):
                return data
            result = dict(data)
            cols = columns or list(result.keys())
            for col in cols:
                low = str(col).lower()
                if low in ("user_id", "email", "phone", "mobile", "ip_address", "client_ip", "password", "secret", "token", "api_key") or "email" in low:
                    result[col] = self._mask_value(result[col], col)
            return result

        def _mask_value(self, value, column=None):
            if value is None:
                return None
            s = str(value)
            low = str(column).lower() if column is not None else ""
            if "email" in low or "@" in s:
                if "@" in s:
                    local, domain = s.split("@", 1)
                    if len(local) <= 2:
                        masked_local = "*" * len(local)
                    else:
                        masked_local = local[:1] + "*" * max(1, len(local) - 2) + local[-1:]
                    return masked_local + "@" + domain
            if low in ("phone", "mobile"):
                return s[:3] + "*" * max(1, len(s) - 5) + s[-2:]
            if low in ("ip_address", "client_ip") and s.count(".") == 3:
                return s.split(".", 1)[0] + ".*.*.*"
            if len(s) <= 3:
                return "*" * len(s)
            return s[:3] + "*" * max(1, len(s) - 3)

        @property
        def active_rules(self):
            return ["fallback_rules"]

    def get_masker():
        return _FallbackMasker()

try:
    from multi_tenant import TenantManager
except Exception:
    class TenantManager(object):
        def __init__(self, config_path="tenants.json"):
            self.config_path = config_path

        def authorize(self, api_key):
            return None

        def record_query(self, tenant_id):
            return None

try:
    from identity import resolve_identity
except Exception:
    resolve_identity = None

try:
    from permission_policy import AccessContext, PermissionPolicy
except Exception:
    AccessContext = None
    PermissionPolicy = None

try:
    from ratelimit import RateLimiter
except Exception:
    class RateLimiter(object):
        def __init__(self, max_requests=100, window_seconds=60, block_seconds=30):
            self.max_requests = max_requests
            self.window_seconds = window_seconds
            self.block_seconds = block_seconds

        def check(self, key):
            return True, None


class GovernanceDecision(object):
    def __init__(self, allowed, reason="", action="allow", metadata=None,
                 severity="info", policy_id="governance.default", decision_type="allow"):
        self.allowed = allowed
        self.reason = reason
        self.action = action
        self.metadata = metadata or {}
        self.severity = severity
        self.policy_id = policy_id
        self.decision_type = decision_type

    def to_dict(self):
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "action": self.action,
            "severity": self.severity,
            "policy_id": self.policy_id,
            "decision_type": self.decision_type,
            "metadata": dict(self.metadata),
        }


class _InMemoryQuota(object):
    """Simple in-process per-user daily query quota."""

    def __init__(self, daily_limit=1000):
        self._counts = {}
        self._daily_limit = daily_limit

    def check(self, user_id):
        count = self._counts.get(user_id, 0)
        if count >= self._daily_limit:
            return False, "quota_exceeded: daily_limit=%d used=%d" % (self._daily_limit, count)
        return True, None

    def increment(self, user_id):
        self._counts[user_id] = self._counts.get(user_id, 0) + 1

    def reset(self, user_id=None):
        if user_id is not None:
            self._counts.pop(user_id, None)
        else:
            self._counts.clear()


class _TablePermissions(object):
    """Minimal table-level allow/deny permission store."""

    def __init__(self, denied_models=None, denied_fields=None):
        self._denied_models = set(denied_models or [])
        self._denied_fields = set(denied_fields or [])

    def check_model(self, model_id):
        if model_id and model_id in self._denied_models:
            return False, "model_access_denied: %s" % model_id
        return True, None

    def check_field(self, field_id):
        if field_id and field_id in self._denied_fields:
            return False, "field_access_denied: %s" % field_id
        return True, None


class GovernanceFacade(object):
    def __init__(self, tenant_manager=None, rate_limiter=None, quota=None, table_permissions=None,
                 permission_policy=None):
        self.tenant_manager = tenant_manager or TenantManager(config_path="tenants.json")
        self.rate_limiter = rate_limiter or RateLimiter(max_requests=100, window_seconds=60, block_seconds=30)
        self.quota = quota if quota is not None else _InMemoryQuota(daily_limit=1000)
        self.table_permissions = table_permissions if table_permissions is not None else _TablePermissions()
        self.permission_policy = permission_policy or (PermissionPolicy() if PermissionPolicy is not None else None)
        self.masker = get_masker()
        self.audit = audit_logger

    def resolve_identity(self, headers=None, fallback="unknown"):
        if resolve_identity is None:
            return {"user_id": fallback, "tenant_id": "default", "quota_key": "default:%s" % fallback, "authenticated": False}
        return resolve_identity(headers, fallback=fallback).to_dict()

    def check_query(self, query, identity="unknown", trace_id="", plan=None, headers=None):
        identity_ctx = self.resolve_identity(headers, fallback=identity) if headers else None
        effective_identity = identity_ctx.get("quota_key") if identity_ctx else identity
        lowered = (query or "").lower()
        if any(token in lowered for token in ["drop table", "delete from", "truncate table"]):
            if hasattr(self.audit, "log_query"):
                self.audit.log_query(effective_identity, query, status="blocked", blocked_reason="dangerous_query", trace_id=trace_id, details={"identity": identity_ctx or {}})
            return GovernanceDecision(
                False,
                reason="dangerous query",
                action="block",
                metadata={"trace_id": trace_id, "identity": identity_ctx or {}},
                severity="high",
                policy_id="governance.dangerous_query",
                decision_type="dangerous_query",
            )

        # quota check
        if self.quota is not None:
            quota_ok, quota_reason = self.quota.check(effective_identity)
            if not quota_ok:
                if hasattr(self.audit, "log_query"):
                    self.audit.log_query(effective_identity, query, status="blocked", blocked_reason="quota_exceeded", trace_id=trace_id, details={"identity": identity_ctx or {}})
                return GovernanceDecision(
                    False,
                    reason="quota exceeded",
                    action="quota_exceeded",
                    metadata={"trace_id": trace_id, "quota_reason": quota_reason, "identity": identity_ctx or {}},
                    severity="medium",
                    policy_id="governance.quota",
                    decision_type="quota_exceeded",
                )

        # table/model permissions (when plan is provided)
        if plan and self.table_permissions is not None:
            model_ok, model_reason = self.table_permissions.check_model(plan.get("model"))
            if not model_ok:
                if hasattr(self.audit, "log_query"):
                    self.audit.log_query(effective_identity, query, status="blocked", blocked_reason=model_reason, trace_id=trace_id, details={"identity": identity_ctx or {}})
                return GovernanceDecision(
                    False,
                    reason=model_reason,
                    action="block",
                    metadata={"trace_id": trace_id, "identity": identity_ctx or {}},
                    severity="high",
                    policy_id="governance.table_permissions",
                    decision_type="model_access_denied",
                )

        allowed, retry_after = self.rate_limiter.check(effective_identity)
        if not allowed:
            if hasattr(self.audit, "log_query"):
                self.audit.log_query(effective_identity, query, status="blocked", blocked_reason="rate_limited", trace_id=trace_id, details={"identity": identity_ctx or {}})
            return GovernanceDecision(
                False,
                reason="rate limited",
                action="rate_limited",
                metadata={"retry_after": retry_after, "trace_id": trace_id, "identity": identity_ctx or {}},
                severity="medium",
                policy_id="governance.rate_limit",
                decision_type="quota_exceeded",
            )

        if self.quota is not None:
            self.quota.increment(effective_identity)
        if hasattr(self.audit, "log_query"):
            self.audit.log_query(effective_identity, query, status="allow", trace_id=trace_id, details={"identity": identity_ctx or {}})
        return GovernanceDecision(
            True,
            action="allow",
            metadata={"trace_id": trace_id, "identity": identity_ctx or {}},
            severity="info",
            policy_id="governance.allow",
            decision_type="allow",
        )

    def check_access(self, access_context=None, plan=None, query=""):
        """R23 optional role/tenant/table/field policy; legacy calls remain valid."""
        if self.permission_policy is None:
            return GovernanceDecision(True, action="allow", policy_id="governance.compat")
        permission = self.permission_policy.evaluate(access_context, plan=plan, query=query)
        data = permission.to_dict()
        return GovernanceDecision(permission.allowed, reason=permission.reason,
                                  action=permission.decision, metadata=data,
                                  severity="high" if permission.requires_human_review else "info",
                                  policy_id=data.get("policy_id"), decision_type=permission.decision)

    def inject_tenant_filter(self, plan, access_context=None):
        if self.permission_policy is None:
            return plan
        return self.permission_policy.inject_tenant_filter(plan, access_context)

    def redact_rows(self, rows):
        return self.masker.mask_rows(rows)

    def redact_row(self, row, columns=None):
        return self.masker.mask_dict(row, columns=columns)

    def tenant_context(self, api_key):
        return self.tenant_manager.authorize(api_key)

    def record_tenant_query(self, tenant_id):
        self.tenant_manager.record_query(tenant_id)

    def audit_event(self, event_type, user_id, action, resource, details=None):
        if hasattr(self.audit, "log"):
            return self.audit.log(event_type, user_id, action, resource, details=details)
        return None


DEFAULT_GOVERNANCE = GovernanceFacade()


def get_governance():
    return DEFAULT_GOVERNANCE


# Backward-compatible alias for agent_facade.py
Governance = GovernanceFacade

__all__ = ["Governance", "GovernanceDecision", "GovernanceFacade", "DEFAULT_GOVERNANCE", "get_governance",
           "_InMemoryQuota", "_TablePermissions"]


