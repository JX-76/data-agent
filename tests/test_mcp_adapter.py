# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'src')
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from mcp_adapter import McpAdapter


def test_mcp_list_tools_reads_registry():
    response = McpAdapter().handle_request({'jsonrpc': '2.0', 'id': 'list_1', 'method': 'tools/list'})
    assert response['id'] == 'list_1'
    names = [item['name'] for item in response['result']['tools']]
    assert 'warehouse.query_sql' in names
    assert 'semantic.catalog_read' in names


def test_mcp_query_sql_uses_governed_gateway():
    response = McpAdapter().handle_request({
        'jsonrpc': '2.0', 'id': 'query_1', 'method': 'tools/call',
        'params': {'name': 'warehouse.query_sql', 'arguments': {'sql': 'SELECT 1 AS value'},
                   'context': {'intent': 'metric_query', 'trace_id': 'mcp_test_query'}},
    })
    result = response['result']
    assert result['status'] == 'ok'
    assert result['trace_event']['name'] == 'external_tool_call'
    assert result['trace_event']['tool_id'] == 'warehouse.query_sql'


def test_mcp_query_sql_keeps_write_policy_block():
    response = McpAdapter().handle_request({
        'jsonrpc': '2.0', 'id': 'block_1', 'method': 'tools/call',
        'params': {'name': 'warehouse.query_sql', 'arguments': {'sql': 'DROP TABLE orders'},
                   'context': {'intent': 'metric_query'}},
    })
    assert response['result']['status'] == 'blocked'
    assert response['result']['diagnostics']['failure_type'] == 'external_tool_policy_denied'


def test_mcp_unknown_method_is_structured_error():
    response = McpAdapter().handle_request({'jsonrpc': '2.0', 'id': 'bad_1', 'method': 'unknown/method'})
    assert response['error']['code'] == -32601


def test_mcp_call_requires_tool_name():
    response = McpAdapter().handle_request({'jsonrpc': '2.0', 'id': 'bad_2', 'method': 'tools/call', 'params': {}})
    assert response['error']['code'] == -32602
