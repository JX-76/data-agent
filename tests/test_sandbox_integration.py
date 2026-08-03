# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'src')
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from db_factory import build_db_adapter
from integration_contracts import ConnectorSpec, validate_real_connection
from sandbox_connectors import SandboxWarehouseConnector


def test_sandbox_factory_executes_real_readonly_sql():
    adapter = build_db_adapter({'DATA_AGENT_DB_MODE': 'sandbox'})
    result = adapter.execute('SELECT SUM(gmv) AS gmv FROM orders')
    assert result['row_count'] == 1
    assert result['rows'][0]['gmv'] == 900.0


def test_sandbox_connector_schema_and_query_contract():
    connector = SandboxWarehouseConnector()
    assert connector.health_check()['status'] == 'ok'
    assert 'orders' in connector.describe_schema()
    result = connector.query('SELECT COUNT(*) AS total FROM orders')
    assert result['rows'][0]['total'] == 5


def test_sandbox_connector_rejects_write():
    result = SandboxWarehouseConnector().query('DROP TABLE orders')
    assert result['status'] == 'error'
    assert result['error_type'] == 'readonly_violation'


def test_real_connection_is_fail_closed_without_approval():
    spec = ConnectorSpec('enterprise.warehouse', environment='real', read_only=True, approved=True)
    assert validate_real_connection(spec, {})['allowed'] is False
    assert validate_real_connection(spec, {'DATA_AGENT_APPROVE_REAL_CONNECTION': 'true'})['allowed'] is True
