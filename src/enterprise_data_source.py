# -*- coding: utf-8 -*-
"""Governed enterprise data-source configuration and verification boundary.

This module never persists, returns, or logs database secrets.  A connection can
only be attempted after an explicit deployment approval flag and only through a
credential reference (currently an environment-variable reference).
"""
from __future__ import unicode_literals

import json
import os
import re
import socket
import time

CONTRACT = "enterprise_data_source_v1"
TEST_CONTRACT = "enterprise_connection_test_v1"
PROBE_CONTRACT = "enterprise_schema_probe_v1"
CONFIG_ENV = "DATA_AGENT_ENTERPRISE_DATA_SOURCE_CONFIG"
APPROVAL_ENV = "DATA_AGENT_APPROVE_REAL_CONNECTION"
SUPPORTED_TYPES = ("postgresql", "mysql")
DECLARABLE_TYPES = ("postgresql", "mysql", "sqlserver", "oracle", "custom")
_SECRET_REFERENCE = re.compile(r"^env:([A-Z_][A-Z0-9_]*)$")
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SAFE_HOST = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,252}$")
try:
    _TIMEOUT_ERRORS = (socket.timeout, TimeoutError)
except NameError:  # Python 2.7
    _TIMEOUT_ERRORS = (socket.timeout,)


class EnterpriseDataSourceError(Exception):
    def __init__(self, code, message, status_code=400):
        Exception.__init__(self, message)
        self.code = code
        self.message = message
        self.status_code = status_code


def _truthy(value):
    return str(value or "").lower() in ("1", "true", "yes", "on")


def _config_path():
    configured = os.environ.get(CONFIG_ENV)
    if configured:
        return os.path.abspath(configured)
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        ".data_agent_enterprise_data_source.json")


def _safe_text(value, limit=240):
    return str(value or "").replace("\r", " ").replace("\n", " ")[:limit]


def _masked_endpoint(config):
    host = config.get("host") or ""
    port = config.get("port") or ""
    database = config.get("database") or ""
    return "%s:%s/%s" % (host, port, database)


def _require_identifier(value, field, allow_empty=False):
    value = _safe_text(value, 128)
    if not value and allow_empty:
        return ""
    if not _SAFE_IDENTIFIER.match(value):
        raise EnterpriseDataSourceError("invalid_%s" % field, "%s 格式不合法。" % field)
    return value


def _sanitize_error(exc):
    # Do not expose database drivers' raw exception data: it can contain a DSN,
    # account name, path, or infrastructure topology.
    if isinstance(exc, ImportError):
        return "connector_unavailable", "当前运行环境未安装该数据库连接器。"
    if isinstance(exc, _TIMEOUT_ERRORS):
        return "connection_timeout", "连接超时。请检查网络、VPN、防火墙和超时设置。"
    name = exc.__class__.__name__.lower()
    if "auth" in name or "access" in name or "operational" in name:
        return "connection_rejected", "数据库拒绝连接。请检查只读账号、TLS 和网络授权。"
    return "connection_failed", "数据库连接或验证失败；详细信息已脱敏。"


class EnterpriseDataSourceStore(object):
    """Stores only non-secret metadata on the local machine."""
    def __init__(self, path=None):
        self.path = path or _config_path()

    def load(self):
        if not os.path.isfile(self.path):
            return None
        try:
            with open(self.path, "r") as handle:
                data = json.load(handle)
            return data if isinstance(data, dict) else None
        except (ValueError, IOError):
            return None

    def save(self, config):
        directory = os.path.dirname(self.path)
        if directory and not os.path.isdir(directory):
            os.makedirs(directory)
        temp = self.path + ".tmp"
        with open(temp, "w") as handle:
            json.dump(config, handle, ensure_ascii=True, sort_keys=True, indent=2)
        try:
            os.chmod(temp, 0o600)
        except OSError:
            pass
        if os.path.exists(self.path):
            try:
                os.remove(self.path)
            except OSError:
                pass
        os.rename(temp, self.path)

    def clear(self):
        if os.path.exists(self.path):
            os.remove(self.path)


class EnterpriseDataSourceService(object):
    def __init__(self, store=None, environment=None, clock=None, connector_factory=None):
        self.store = store or EnterpriseDataSourceStore()
        self.environment = environment if environment is not None else os.environ
        self.clock = clock or time.time
        self.connector_factory = connector_factory
        self._pool = None
        self._active = False
        self._last_test = None

    def capabilities(self):
        available = []
        for db_type, module_name in (("postgresql", "psycopg2"), ("mysql", "mysql.connector")):
            try:
                __import__(module_name)
                available.append(db_type)
            except ImportError:
                pass
        return {"contract": CONTRACT, "declarable_types": list(DECLARABLE_TYPES),
                "supported_types": list(SUPPORTED_TYPES), "available_connectors": available,
                "unsupported_types": [item for item in DECLARABLE_TYPES if item not in SUPPORTED_TYPES],
                "secrets_policy": "credential_reference_only"}

    def _validate(self, raw):
        raw = dict(raw or {})
        if any(key.lower() in ("password", "secret", "token", "connection_string", "dsn") for key in raw):
            raise EnterpriseDataSourceError("secret_input_denied", "请使用凭据环境变量引用，不要提交密码、Token 或 DSN。")
        db_type = _safe_text(raw.get("db_type"), 32).lower()
        if db_type not in DECLARABLE_TYPES:
            raise EnterpriseDataSourceError("unsupported_db_type", "数据库类型不在允许列表中。")
        host = _safe_text(raw.get("host"), 253).lower()
        if not _SAFE_HOST.match(host):
            raise EnterpriseDataSourceError("invalid_host", "主机名或 IP 地址格式不合法。")
        try:
            port = int(raw.get("port"))
        except (TypeError, ValueError):
            raise EnterpriseDataSourceError("invalid_port", "端口必须是 1 到 65535 的整数。")
        if port < 1 or port > 65535:
            raise EnterpriseDataSourceError("invalid_port", "端口必须是 1 到 65535 的整数。")
        reference = _safe_text(raw.get("credential_reference"), 160)
        if not _SECRET_REFERENCE.match(reference):
            raise EnterpriseDataSourceError("invalid_credential_reference", "凭据引用必须是 env:大写环境变量名。")
        allowed = raw.get("allowed_tables") or []
        if isinstance(allowed, str):
            allowed = [item.strip() for item in allowed.split(",") if item.strip()]
        if not isinstance(allowed, list) or not allowed:
            raise EnterpriseDataSourceError("allowed_tables_required", "必须至少配置一张允许访问的表或视图。")
        allowed = [_require_identifier(item, "allowed_table") for item in allowed]
        ssl_mode = _safe_text(raw.get("ssl_mode") or "require", 16).lower()
        if ssl_mode not in ("disable", "prefer", "require", "verify-ca", "verify-full"):
            raise EnterpriseDataSourceError("invalid_ssl_mode", "TLS 模式不合法。")
        if ssl_mode == "disable":
            raise EnterpriseDataSourceError("tls_required", "企业数据源必须启用 TLS，不能使用 disable。")
        return {"contract": CONTRACT, "source_id": _require_identifier(raw.get("source_id") or "enterprise_default", "source_id"),
                "display_name": _safe_text(raw.get("display_name") or "企业数据源", 100), "db_type": db_type,
                "host": host, "port": port, "database": _require_identifier(raw.get("database"), "database"),
                "schema": _require_identifier(raw.get("schema") or "public", "schema"),
                "username": _safe_text(raw.get("username"), 100), "credential_reference": reference,
                "ssl_mode": ssl_mode, "connect_timeout_seconds": max(1, min(int(raw.get("connect_timeout_seconds") or 5), 30)),
                "read_only_required": True, "allowed_tables": sorted(set(allowed)),
                "tenant_scope_mode": _safe_text(raw.get("tenant_scope_mode") or "external_rls_required", 40),
                "status": "configured", "updated_at": int(self.clock())}

    def configure(self, raw):
        config = self._validate(raw)
        old = self.store.load() or {}
        config["created_at"] = old.get("created_at") or int(self.clock())
        self.store.save(config)
        self.disconnect()
        return self.public_config(config)

    def public_config(self, config=None):
        config = config or self.store.load()
        if not config:
            return {"contract": CONTRACT, "configured": False, "status": "not_configured", "real_connection_approved": _truthy(self.environment.get(APPROVAL_ENV))}
        data = dict(config)
        data.pop("credential_reference", None)
        data["credential_reference_configured"] = True
        data["configured"] = True
        data["endpoint"] = _masked_endpoint(config)
        data["real_connection_approved"] = _truthy(self.environment.get(APPROVAL_ENV))
        data["active"] = self._active
        data["last_test"] = dict(self._last_test) if self._last_test else None
        return data

    def _secret(self, config):
        matched = _SECRET_REFERENCE.match(config.get("credential_reference") or "")
        value = self.environment.get(matched.group(1)) if matched else None
        if not value:
            raise EnterpriseDataSourceError("credential_unavailable", "凭据环境变量未配置或为空。")
        return value

    def _connect(self, config):
        if config.get("db_type") not in SUPPORTED_TYPES:
            raise EnterpriseDataSourceError("connector_not_implemented", "该数据库类型尚未实现连接器；当前仅可声明配置。")
        secret = self._secret(config)
        if self.connector_factory:
            return self.connector_factory(config, secret)
        if config["db_type"] == "postgresql":
            import psycopg2
            return psycopg2.connect(host=config["host"], port=config["port"], dbname=config["database"],
                                    user=config.get("username") or None, password=secret,
                                    connect_timeout=config["connect_timeout_seconds"], sslmode=config["ssl_mode"])
        import mysql.connector
        ssl_disabled = False
        return mysql.connector.connect(host=config["host"], port=config["port"], database=config["database"],
                                       user=config.get("username") or None, password=secret,
                                       connection_timeout=config["connect_timeout_seconds"], ssl_disabled=ssl_disabled)

    def _approved_config(self):
        config = self.store.load()
        if not config:
            raise EnterpriseDataSourceError("not_configured", "尚未配置企业数据源。")
        if not _truthy(self.environment.get(APPROVAL_ENV)):
            raise EnterpriseDataSourceError("real_connection_not_approved", "真实数据库连接未获部署批准。请设置 DATA_AGENT_APPROVE_REAL_CONNECTION=true。", 403)
        return config

    def test_connection(self):
        config = self._approved_config()
        started = time.time()
        conn = None
        try:
            conn = self._connect(config)
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            cursor.fetchone()
            self._active = True
            result = {"contract": TEST_CONTRACT, "status": "verified", "source_id": config["source_id"],
                      "endpoint": _masked_endpoint(config), "db_type": config["db_type"], "tls_required": True,
                      "read_only_required": True, "latency_ms": int((time.time() - started) * 1000),
                      "tested_at": int(self.clock()), "audit_event": "enterprise_connection_test"}
        except EnterpriseDataSourceError:
            raise
        except Exception as exc:
            code, message = _sanitize_error(exc)
            self._active = False
            result = {"contract": TEST_CONTRACT, "status": "failed", "source_id": config["source_id"],
                      "endpoint": _masked_endpoint(config), "error_code": code, "message": message,
                      "latency_ms": int((time.time() - started) * 1000), "tested_at": int(self.clock()),
                      "audit_event": "enterprise_connection_test"}
        finally:
            if conn:
                try: conn.close()
                except Exception: pass
        self._last_test = result
        return dict(result)

    def probe_schema(self):
        config = self._approved_config()
        conn = None
        try:
            conn = self._connect(config)
            cursor = conn.cursor()
            tables = []
            for table in config["allowed_tables"]:
                if config["db_type"] == "postgresql":
                    cursor.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position", (config["schema"], table))
                else:
                    cursor.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position", (config["database"], table))
                columns = cursor.fetchall() or []
                tables.append({"table": table, "columns": [{"name": str(row[0]), "type": str(row[1])} for row in columns]})
            return {"contract": PROBE_CONTRACT, "status": "verified", "source_id": config["source_id"],
                    "schema": config["schema"], "allowed_tables": tables, "row_data_returned": False,
                    "audit_event": "enterprise_schema_probe", "probed_at": int(self.clock())}
        except EnterpriseDataSourceError:
            raise
        except Exception as exc:
            code, message = _sanitize_error(exc)
            return {"contract": PROBE_CONTRACT, "status": "failed", "source_id": config["source_id"],
                    "error_code": code, "message": message, "row_data_returned": False,
                    "audit_event": "enterprise_schema_probe", "probed_at": int(self.clock())}
        finally:
            if conn:
                try: conn.close()
                except Exception: pass

    def disconnect(self):
        self._active = False
        self._pool = None
        return {"contract": CONTRACT, "status": "disconnected", "audit_event": "enterprise_connection_disconnected"}


__all__ = ["EnterpriseDataSourceService", "EnterpriseDataSourceStore", "EnterpriseDataSourceError", "CONTRACT"]
