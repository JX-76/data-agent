# -*- coding: utf-8 -*-
"""Data source gateway for governed external interactions.

This module is the stable application-facing entrypoint for data/semantic/schema
access. It intentionally delegates to ExternalToolExecutor so every external
interaction keeps the same registry -> policy -> timeout/circuit -> trace path.
"""
from __future__ import unicode_literals

from external_tool_executor import ExternalToolExecutor


class DataSourceGateway(object):
    def __init__(self, executor=None, default_context=None):
        self.executor = executor or ExternalToolExecutor()
        self.default_context = default_context or {}

    def _context(self, context=None):
        merged = {}
        merged.update(self.default_context or {})
        merged.update(context or {})
        return merged

    def read_semantic_catalog(self, context=None):
        return self.executor.call('semantic.catalog_read', {}, self._context(context))

    def introspect_schema(self, context=None):
        return self.executor.call('warehouse.schema_introspect', {}, self._context(context))

    def query_sql(self, sql, limit=None, offset=0, context=None):
        args = {'sql': sql, 'offset': offset}
        if limit is not None:
            args['limit'] = limit
        return self.executor.call('warehouse.query_sql', args, self._context(context))

    def run_harness_suite(self, suite, context=None):
        return self.executor.call('harness.run_suite', {'suite': suite}, self._context(context))


__all__ = ['DataSourceGateway']
