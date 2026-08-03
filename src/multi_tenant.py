"""Multi-tenancy: namespace isolation with per-tenant quotas and resource limits.

Supports:
- Per-tenant API keys (tenant → key mapping)
- Per-tenant rate limits (independent of global limit)
- Per-tenant query quotas (daily/monthly)
- Tenant-scoped sessions and cache
- Admin endpoints for tenant management

Usage:
    from multi_tenant import TenantManager
    tm = TenantManager()
    tm.create_tenant("team-analytics", daily_quota=1000)
    tm.authorize("team-analytics", api_key)  # → TenantContext
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import structlog

logger = structlog.get_logger("multi-tenant")


# ── Models ──

@dataclass
class TenantConfig:
    id: str
    name: str
    api_keys: list[str]
    daily_quota: int = 1000       # Max queries per day
    monthly_quota: int = 30000    # Max queries per month
    rate_limit_rpm: int = 60      # Per-tenant rate limit
    max_concurrent: int = 5       # Max concurrent queries
    allowed_models: list[str] = field(default_factory=lambda: ["order_detail", "user_summary", "product_analysis"])
    enabled: bool = True
    created_at: str = ""
    metadata: dict = field(default_factory=dict)


@dataclass
class TenantContext:
    tenant_id: str
    tenant_name: str
    key_hash: str
    quotas: dict  # daily_used, daily_limit, monthly_used, monthly_limit
    rate_limit_remaining: int


@dataclass
class TenantUsage:
    daily_queries: int = 0
    monthly_queries: int = 0
    daily_reset: str = ""  # YYYY-MM-DD
    monthly_reset: str = ""  # YYYY-MM


# ── Manager ──

class TenantManager:
    """Multi-tenant management with quotas and isolation."""

    def __init__(self, config_path: str = "tenants.json"):
        self.config_path = Path(config_path)
        self._tenants: dict[str, TenantConfig] = {}
        self._key_map: dict[str, str] = {}  # key_hash → tenant_id
        self._usage: dict[str, dict[str, TenantUsage]] = {}  # tenant_id → {day_key: usage, "current": usage}
        self._concurrent: dict[str, int] = {}  # tenant_id → current concurrent count
        self._lock = threading.Lock()
        self._restore()

    def _restore(self):
        if self.config_path.exists():
            try:
                data = json.loads(self.config_path.read_text())
                for raw in data.get("tenants", []):
                    tc = TenantConfig(**raw)
                    self._tenants[tc.id] = tc
                    for key in tc.api_keys:
                        self._key_map[self._hash_key(key)] = tc.id
                self._usage = data.get("usage", {})
            except Exception as e:
                logger.warning("tenant_restore_failed", error=str(e))

    def _save(self):
        data = {
            "tenants": [t.__dict__ for t in self._tenants.values()],
            "usage": self._usage,
        }
        self.config_path.write_text(json.dumps(data, ensure_ascii=False, indent=2))

    @staticmethod
    def _hash_key(key: str) -> str:
        return hashlib.sha256(key.encode()).hexdigest()[:16]

    def _today_key(self) -> str:
        return time.strftime("%Y-%m-%d", time.localtime())

    def _month_key(self) -> str:
        return time.strftime("%Y-%m", time.localtime())

    # ── Tenant CRUD ──

    def create_tenant(
        self,
        tenant_id: str,
        name: str,
        api_keys: list[str] = None,
        daily_quota: int = 1000,
        monthly_quota: int = 30000,
        rate_limit_rpm: int = 60,
        max_concurrent: int = 5,
    ) -> TenantConfig:
        """Create a new tenant."""
        if tenant_id in self._tenants:
            raise ValueError(f"Tenant {tenant_id} already exists")

        tc = TenantConfig(
            id=tenant_id, name=name,
            api_keys=api_keys or [],
            daily_quota=daily_quota, monthly_quota=monthly_quota,
            rate_limit_rpm=rate_limit_rpm, max_concurrent=max_concurrent,
            created_at=time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
        )

        with self._lock:
            self._tenants[tenant_id] = tc
            for key in tc.api_keys:
                self._key_map[self._hash_key(key)] = tenant_id
            self._usage[tenant_id] = {"current": TenantUsage().__dict__}
            self._concurrent[tenant_id] = 0
            self._save()

        logger.info("tenant_created", tenant_id=tenant_id, name=name,
                    daily_quota=daily_quota)
        return tc

    def add_key(self, tenant_id: str, api_key: str):
        """Add an API key to a tenant."""
        tc = self._tenants.get(tenant_id)
        if not tc:
            raise ValueError(f"Tenant {tenant_id} not found")
        with self._lock:
            tc.api_keys.append(api_key)
            self._key_map[self._hash_key(api_key)] = tenant_id
            self._save()
        logger.info("tenant_key_added", tenant_id=tenant_id)

    def get_tenant(self, tenant_id: str) -> Optional[TenantConfig]:
        return self._tenants.get(tenant_id)

    def list_tenants(self) -> list[dict]:
        return [
            {"id": t.id, "name": t.name, "enabled": t.enabled,
             "api_keys_count": len(t.api_keys),
             "daily_quota": t.daily_quota, "monthly_quota": t.monthly_quota}
            for t in self._tenants.values()
        ]

    # ── Authorization ──

    def authorize(self, api_key: str) -> Optional[TenantContext]:
        """Authorize an API key, returning tenant context with quotas.

        Returns None if key is invalid or quota exceeded.
        """
        key_hash = self._hash_key(api_key)
        tenant_id = self._key_map.get(key_hash)
        if not tenant_id:
            return None

        tc = self._tenants.get(tenant_id)
        if not tc or not tc.enabled:
            return None

        with self._lock:
            # Check concurrent limit
            if self._concurrent.get(tenant_id, 0) >= tc.max_concurrent:
                logger.warning("tenant_concurrent_limit", tenant_id=tenant_id,
                              current=self._concurrent[tenant_id], max=tc.max_concurrent)
                return None

            # Check daily quota
            today = self._today_key()
            usage_entry = self._usage.get(tenant_id, {})
            today_usage = usage_entry.get(today, {})
            daily_used = today_usage.get("queries", 0) if isinstance(today_usage, dict) else 0

            if daily_used >= tc.daily_quota:
                logger.warning("tenant_daily_quota_exceeded",
                             tenant_id=tenant_id, used=daily_used, limit=tc.daily_quota)
                return None

            # Increment
            self._concurrent[tenant_id] = self._concurrent.get(tenant_id, 0) + 1

            return TenantContext(
                tenant_id=tenant_id,
                tenant_name=tc.name,
                key_hash=key_hash,
                quotas={
                    "daily_used": daily_used,
                    "daily_limit": tc.daily_quota,
                    "monthly_limit": tc.monthly_quota,
                },
                rate_limit_remaining=tc.rate_limit_rpm,
            )

    def record_query(self, tenant_id: str):
        """Record a completed query for quota tracking."""
        with self._lock:
            self._concurrent[tenant_id] = max(0, self._concurrent.get(tenant_id, 1) - 1)

            today = self._today_key()
            if tenant_id not in self._usage:
                self._usage[tenant_id] = {}
            if today not in self._usage[tenant_id]:
                self._usage[tenant_id][today] = {"queries": 0}
            if not isinstance(self._usage[tenant_id][today], dict):
                self._usage[tenant_id][today] = {"queries": 0}
            self._usage[tenant_id][today]["queries"] += 1

            self._save()

    def release(self, tenant_id: str):
        """Release a concurrent slot (e.g., on error)."""
        with self._lock:
            self._concurrent[tenant_id] = max(0, self._concurrent.get(tenant_id, 1) - 1)

    def usage_report(self, tenant_id: str) -> dict:
        """Get usage report for a tenant."""
        tc = self._tenants.get(tenant_id)
        if not tc:
            return {}

        today = self._today_key()
        usage_entry = self._usage.get(tenant_id, {})
        today_usage = usage_entry.get(today, {"queries": 0})
        daily_used = today_usage.get("queries", 0) if isinstance(today_usage, dict) else 0

        return {
            "tenant_id": tenant_id,
            "tenant_name": tc.name,
            "enabled": tc.enabled,
            "daily_used": daily_used,
            "daily_limit": tc.daily_quota,
            "daily_remaining": max(0, tc.daily_quota - daily_used),
            "rate_limit_rpm": tc.rate_limit_rpm,
            "max_concurrent": tc.max_concurrent,
            "current_concurrent": self._concurrent.get(tenant_id, 0),
        }

    def disable_tenant(self, tenant_id: str):
        """Disable a tenant."""
        tc = self._tenants.get(tenant_id)
        if tc:
            tc.enabled = False
            self._save()

    def enable_tenant(self, tenant_id: str):
        """Enable a tenant."""
        tc = self._tenants.get(tenant_id)
        if tc:
            tc.enabled = True
            self._save()


# ── Global ──

_tm: TenantManager | None = None


def get_tenant_manager() -> TenantManager:
    global _tm
    if _tm is None:
        _tm = TenantManager()
    return _tm
