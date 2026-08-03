# -*- coding: utf-8 -*-
"""Sandbox enterprise connectors implementing the governed readonly contracts."""
from __future__ import unicode_literals

from db_adapter import ReadonlyQueryExecutor, SQLiteReadonlyDBAdapter
from integration_contracts import ConnectorSpec
from sandbox_data_factory import build_sandbox_connection, sandbox_manifest


class SandboxWarehouseConnector(object):
    def __init__(self):
        self.spec = ConnectorSpec('sandbox.warehouse', environment='sandbox',
                                  capabilities=['schema_introspect', 'query_sql'],
                                  schema_version=sandbox_manifest()['schema_version'],
                                  read_only=True, approved=True, owner='data-agent')
        self.executor = ReadonlyQueryExecutor(SQLiteReadonlyDBAdapter(connection=build_sandbox_connection()))

    def health_check(self):
        result = self.executor.execute('SELECT 1 AS healthy', limit=1)
        return {'status': 'ok' if not result.get('error_type') else 'error', 'connector': self.spec.to_dict()}

    def describe_schema(self):
        return self.executor.describe_schema()

    def query(self, sql, limit=None, offset=0):
        try:
            return self.executor.execute(sql, limit=limit, offset=offset)
        except ValueError as exc:
            return {'status': 'error', 'rows': [], 'row_count': 0, 'sql': sql,
                    'source': 'sandbox', 'error': str(exc),
                    'error_type': 'readonly_violation'}


__all__ = ['SandboxWarehouseConnector']
