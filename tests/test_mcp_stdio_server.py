# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'src')
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from mcp_stdio_server import McpStdioServer


def _line(obj):
    return json.dumps(obj) + '\n'


def test_initialize_negotiates_protocol_version():
    server = McpStdioServer()
    response = server.handle_line(_line({
        'jsonrpc': '2.0', 'id': 'init_1', 'method': 'initialize',
        'params': {'protocolVersion': '1999-01-01'},
    }))
    assert response['id'] == 'init_1'
    assert response['result']['protocolVersion'] == server.protocol_version
    assert response['result']['clientProtocolVersion'] == '1999-01-01'
    assert 'serverInfo' in response['result']


def test_ping_returns_empty_result():
    server = McpStdioServer()
    response = server.handle_line(_line({'jsonrpc': '2.0', 'id': 'p1', 'method': 'ping'}))
    assert response == {'jsonrpc': '2.0', 'id': 'p1', 'result': {}}


def test_initialized_notification_is_silent():
    server = McpStdioServer()
    response = server.handle_line(_line({'jsonrpc': '2.0', 'method': 'notifications/initialized'}))
    assert response is None


def test_tools_call_delegates_to_adapter_and_keeps_policy_block():
    server = McpStdioServer()
    response = server.handle_line(_line({
        'jsonrpc': '2.0', 'id': 'call_1', 'method': 'tools/call',
        'params': {'name': 'warehouse.query_sql', 'arguments': {'sql': 'DROP TABLE orders'},
                   'context': {'intent': 'metric_query'}},
    }))
    assert response['result']['status'] == 'blocked'
    assert response['result']['diagnostics']['failure_type'] == 'external_tool_policy_denied'


def test_tools_list_delegates_to_adapter():
    server = McpStdioServer()
    response = server.handle_line(_line({'jsonrpc': '2.0', 'id': 'list_1', 'method': 'tools/list'}))
    names = [tool['name'] for tool in response['result']['tools']]
    assert 'warehouse.query_sql' in names


def test_parse_error_is_structured():
    server = McpStdioServer()
    response = server.handle_line('{not json}\n')
    assert response['error']['code'] == -32700


def test_request_size_limit_rejects_large_payload():
    server = McpStdioServer(max_request_bytes=64)
    big = {'jsonrpc': '2.0', 'id': 'big', 'method': 'tools/call',
           'params': {'name': 'warehouse.query_sql', 'arguments': {'sql': 'SELECT ' + ('a' * 200)}}}
    response = server.handle_line(_line(big))
    assert response['error']['code'] == -32001


def test_invalid_jsonrpc_version_rejected():
    server = McpStdioServer()
    response = server.handle_line(_line({'jsonrpc': '1.0', 'id': 'v1', 'method': 'ping'}))
    assert response['error']['code'] == -32600


def test_auth_token_required_when_configured():
    server = McpStdioServer(auth_token='secret')
    denied = server.handle_line(_line({'jsonrpc': '2.0', 'id': 'a1', 'method': 'tools/list'}))
    assert denied['error']['code'] == -32002
    allowed = server.handle_line(_line({
        'jsonrpc': '2.0', 'id': 'a2', 'method': 'tools/list',
        'params': {'context': {'auth_token': 'secret'}},
    }))
    assert 'result' in allowed


def test_serve_reads_multiple_lines_and_writes_responses():
    server = McpStdioServer()
    payload = _line({'jsonrpc': '2.0', 'id': 's1', 'method': 'ping'})
    payload += _line({'jsonrpc': '2.0', 'method': 'notifications/initialized'})
    payload += _line({'jsonrpc': '2.0', 'id': 's2', 'method': 'tools/list'})
    stdin = io.StringIO(payload)
    stdout = io.StringIO()
    handled = server.serve(input_stream=stdin, output_stream=stdout)
    lines = [line for line in stdout.getvalue().split('\n') if line.strip()]
    # ping + tools/list emit responses, the notification does not.
    assert handled == 2
    assert len(lines) == 2
