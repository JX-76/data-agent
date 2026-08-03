# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'src')
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from db_adapter import MockDBAdapter, ReadonlyQueryExecutor
from data_source_gateway import DataSourceGateway
from external_tool_executor import ExternalToolExecutor


def _gateway(query_log=None):
    db = ReadonlyQueryExecutor(MockDBAdapter(query_log=query_log))
    executor = ExternalToolExecutor(db_executor=db)
    return DataSourceGateway(executor=executor, default_context={'intent': 'metric_query'})


def test_gateway_read_semantic_catalog_goes_through_policy_and_trace():
    result = _gateway().read_semantic_catalog({'trace_id': 't_gw_catalog'})
    assert result['status'] == 'ok'
    assert 'metrics' in result['data']
    assert result['trace_event']['name'] == 'external_tool_call'


def test_gateway_query_sql_readonly_ok():
    query_log = []
    result = _gateway(query_log=query_log).query_sql('SELECT 1 AS value', limit=5,
                                                      context={'trace_id': 't_gw_query'})
    assert result['status'] == 'ok'
    assert query_log == ['SELECT 1 AS value']


def test_gateway_query_sql_blocks_write():
    result = _gateway().query_sql('DELETE FROM orders', context={'trace_id': 't_gw_block'})
    assert result['status'] == 'blocked'
    assert result['diagnostics']['failure_type'] == 'external_tool_policy_denied'


def test_gateway_introspect_schema_ok():
    result = _gateway().introspect_schema({'trace_id': 't_gw_schema'})
    assert result['status'] == 'ok'
    assert 'schema' in result['data']


def test_gateway_default_context_merged_with_call_context():
    gateway = _gateway()
    merged = gateway._context({'trace_id': 't_gw_merge'})
    assert merged['intent'] == 'metric_query'
    assert merged['trace_id'] == 't_gw_merge'
