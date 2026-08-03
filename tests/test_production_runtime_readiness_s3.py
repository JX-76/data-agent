# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import release_api
from release_api import release_health


_PROD_ENV_KEYS = [
    "AGENT_ENV",
    "DATA_AGENT_ENV",
    "DATA_AGENT_AUTH_MODE",
    "DATA_AGENT_POSTGRES_ENABLED",
    "DATABASE_URL",
    "DATA_AGENT_RLS_CONFIRMED",
    "DATA_AGENT_AUDIT_SINK",
    "DATA_AGENT_METRICS_SINK",
    "DATA_AGENT_BACKUP_CONFIRMED",
]


def _clear_prod_env(monkeypatch):
    for key in _PROD_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_development_profile_remains_ready_for_local_release_gate(monkeypatch):
    _clear_prod_env(monkeypatch)
    health = release_health()
    assert health["contract"] == "release_v1_health"
    assert health["ready"] is True
    assert health["production_readiness"]["profile"] == "development"
    assert health["components"]["production_runtime"] == "ready"


def test_production_profile_fails_closed_without_external_dependencies(monkeypatch):
    _clear_prod_env(monkeypatch)
    monkeypatch.setenv("AGENT_ENV", "production")
    health = release_health()
    readiness = health["production_readiness"]
    assert health["ready"] is False
    assert health["status"] == "blocked"
    assert readiness["contract"] == "production_runtime_readiness_v1"
    assert readiness["profile"] == "production"
    failed = [item["name"] for item in readiness["checks"] if not item["passed"]]
    assert "auth_mode" in failed
    assert "postgres_enabled" in failed
    assert "rls_confirmed" in failed
    assert "audit_sink" in failed
    assert "metrics_sink" in failed
    assert "backup_confirmed" in failed
    # No DSN/secret value should be reflected in the health body.
    assert "DATABASE_URL" not in repr(health)


def test_production_profile_requires_non_mock_auth(monkeypatch):
    _clear_prod_env(monkeypatch)
    monkeypatch.setenv("AGENT_ENV", "production")
    monkeypatch.setenv("DATA_AGENT_AUTH_MODE", "mock")
    monkeypatch.setenv("DATA_AGENT_POSTGRES_ENABLED", "true")
    monkeypatch.setenv("DATABASE_URL", "postgres://user:secret@example/db")
    monkeypatch.setenv("DATA_AGENT_RLS_CONFIRMED", "true")
    monkeypatch.setenv("DATA_AGENT_AUDIT_SINK", "external")
    monkeypatch.setenv("DATA_AGENT_METRICS_SINK", "prometheus")
    monkeypatch.setenv("DATA_AGENT_BACKUP_CONFIRMED", "true")
    health = release_health()
    checks = dict((item["name"], item) for item in health["production_readiness"]["checks"])
    assert health["ready"] is False
    assert checks["auth_mode"]["passed"] is False
    assert checks["dev_mock_disabled"]["passed"] is False
    assert "secret" not in repr(health)


def test_production_profile_ready_when_all_external_controls_confirmed(monkeypatch):
    _clear_prod_env(monkeypatch)
    monkeypatch.setenv("AGENT_ENV", "production")
    monkeypatch.setenv("DATA_AGENT_AUTH_MODE", "oidc")
    monkeypatch.setenv("DATA_AGENT_POSTGRES_ENABLED", "true")
    monkeypatch.setenv("DATABASE_URL", "postgres://user:secret@example/db")
    monkeypatch.setenv("DATA_AGENT_RLS_CONFIRMED", "true")
    monkeypatch.setenv("DATA_AGENT_AUDIT_SINK", "external")
    monkeypatch.setenv("DATA_AGENT_METRICS_SINK", "otel")
    monkeypatch.setenv("DATA_AGENT_BACKUP_CONFIRMED", "true")
    health = release_health()
    assert health["ready"] is True
    assert health["status"] == "healthy"
    assert all(item["passed"] for item in health["production_readiness"]["checks"])
    assert "postgres://" not in repr(health)
    assert "secret" not in repr(health)
