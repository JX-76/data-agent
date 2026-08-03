# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import json

import pytest

from enterprise_data_source import EnterpriseDataSourceError, EnterpriseDataSourceService, EnterpriseDataSourceStore


class FakeCursor(object):
    def __init__(self):
        self.calls = []
    def execute(self, sql, params=None):
        self.calls.append((sql, params))
    def fetchone(self):
        return (1,)
    def fetchall(self):
        return [("order_id", "text"), ("paid_at", "timestamp")]


class FakeConnection(object):
    def __init__(self):
        self.cursor_value = FakeCursor()
        self.closed = False
    def cursor(self):
        return self.cursor_value
    def close(self):
        self.closed = True


def config():
    return {"source_id": "enterprise_test", "display_name": "测试数据源", "db_type": "postgresql",
            "host": "readonly-db.example.internal", "port": 5432, "database": "analytics", "schema": "public",
            "username": "agent_ro", "credential_reference": "env:TEST_ENTERPRISE_PASSWORD",
            "ssl_mode": "require", "allowed_tables": ["orders_view", "stores_dim"]}


def service(tmp_path, environment=None, connector_factory=None):
    return EnterpriseDataSourceService(EnterpriseDataSourceStore(str(tmp_path / "source.json")),
                                       environment=environment or {}, clock=lambda: 1700000000,
                                       connector_factory=connector_factory)


def test_config_never_persists_or_returns_secret(tmp_path):
    subject = service(tmp_path)
    result = subject.configure(config())
    stored = (tmp_path / "source.json").read_text(encoding="utf-8")
    assert "TEST_ENTERPRISE_PASSWORD" in stored
    assert "password" not in result
    assert "credential_reference" not in result
    assert result["credential_reference_configured"] is True


def test_rejects_secret_plaintext_and_insecure_tls(tmp_path):
    subject = service(tmp_path)
    unsafe = config(); unsafe["password"] = "not-allowed"
    with pytest.raises(EnterpriseDataSourceError) as raised:
        subject.configure(unsafe)
    assert raised.value.code == "secret_input_denied"
    insecure = config(); insecure["ssl_mode"] = "disable"
    with pytest.raises(EnterpriseDataSourceError) as raised:
        subject.configure(insecure)
    assert raised.value.code == "tls_required"


def test_real_connection_is_fail_closed_without_approval(tmp_path):
    subject = service(tmp_path, {"TEST_ENTERPRISE_PASSWORD": "secret"})
    subject.configure(config())
    with pytest.raises(EnterpriseDataSourceError) as raised:
        subject.test_connection()
    assert raised.value.code == "real_connection_not_approved"


def test_connection_test_uses_select_one_and_redacts_secret(tmp_path):
    connection = FakeConnection()
    subject = service(tmp_path, {"DATA_AGENT_APPROVE_REAL_CONNECTION": "true", "TEST_ENTERPRISE_PASSWORD": "secret-value"},
                      connector_factory=lambda cfg, secret: connection)
    subject.configure(config())
    result = subject.test_connection()
    assert result["status"] == "verified"
    assert connection.cursor_value.calls == [("SELECT 1", None)]
    assert "secret-value" not in json.dumps(result)
    assert connection.closed is True


def test_schema_probe_is_limited_to_allowed_tables_and_returns_no_rows(tmp_path):
    connection = FakeConnection()
    subject = service(tmp_path, {"DATA_AGENT_APPROVE_REAL_CONNECTION": "true", "TEST_ENTERPRISE_PASSWORD": "secret"},
                      connector_factory=lambda cfg, secret: connection)
    subject.configure(config())
    result = subject.probe_schema()
    assert result["status"] == "verified"
    assert result["row_data_returned"] is False
    assert [item[1][1] for item in connection.cursor_value.calls] == ["orders_view", "stores_dim"]


def test_unknown_declared_adapter_never_attempts_connection(tmp_path):
    calls = []
    subject = service(tmp_path, {"DATA_AGENT_APPROVE_REAL_CONNECTION": "true", "TEST_ENTERPRISE_PASSWORD": "secret"},
                      connector_factory=lambda cfg, secret: calls.append(cfg))
    source = config(); source["db_type"] = "sqlserver"
    subject.configure(source)
    with pytest.raises(EnterpriseDataSourceError) as raised:
        subject.test_connection()
    assert raised.value.code == "connector_not_implemented"
    assert calls == []
