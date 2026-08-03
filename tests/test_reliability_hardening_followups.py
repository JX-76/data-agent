# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import asyncio
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'src')
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import release_api
import server
from contracts import build_execution_envelope
from db_adapter import SQLiteReadonlyDBAdapter
from evidence_bus import EvidenceBus
from mcp_adapter import McpAdapter
from multi_agent_contracts import RESULT_ERROR
from server import QueryRequest
from supervisor_runtime import SupervisorRuntime


class _DummyRequest(object):
    def __init__(self, headers=None):
        self.headers = headers or {}
        self.client = type('Client', (), {'host': '127.0.0.1'})()


def _release_env(status='ok', query='最近7天GMV', trace_id='trace_stream_task_test'):
    result = {
        'status': status,
        'summary': 'done',
        'results': [{'metric': 'gmv', 'value': 1}],
        'trace_id': trace_id,
        'tenant_id': 'tenant-a',
    }
    return release_api._envelope(query, 'sid', result, 0, 'audit_stream_task_test')


async def _first_sse_event(response):
    async for chunk in response.body_iterator:
        if isinstance(chunk, bytes):
            chunk = chunk.decode('utf-8')
        for block in chunk.split('\n\n'):
            if block.startswith('data: ') and '[DONE]' not in block:
                return json.loads(block[len('data: '):])
    return {}


def test_mcp_list_tools_filters_by_allowed_tools_and_redacts_schema():
    response = McpAdapter().handle_request({
        'jsonrpc': '2.0', 'id': 'list_filter', 'method': 'tools/list',
        'params': {'context': {'allowed_tools': ['warehouse.query_sql'], 'redact_schema': True}},
    })
    tools = response['result']['tools']
    assert [item['name'] for item in tools] == ['warehouse.query_sql']
    assert tools[0]['inputSchema']['properties']['sql'] == {'type': 'string'}


def test_mcp_call_blocks_tool_not_allowed_by_context():
    response = McpAdapter().handle_request({
        'jsonrpc': '2.0', 'id': 'call_blocked', 'method': 'tools/call',
        'params': {'name': 'warehouse.query_sql', 'arguments': {'sql': 'SELECT 1'},
                   'context': {'allowed_tools': ['semantic.catalog_read']}},
    })
    assert response['result']['status'] == 'blocked'
    assert response['result']['diagnostics']['failure_type'] == 'mcp_tool_not_allowed'


def test_mcp_call_validates_required_arguments_before_gateway():
    response = McpAdapter().handle_request({
        'jsonrpc': '2.0', 'id': 'call_invalid', 'method': 'tools/call',
        'params': {'name': 'warehouse.query_sql', 'arguments': {}, 'context': {'intent': 'metric_query'}},
    })
    assert response['result']['status'] == 'error'
    assert response['result']['diagnostics']['failure_type'] == 'mcp_invalid_arguments'


def test_sqlite_readonly_rejects_dangerous_tokens_inside_with_statement():
    adapter = SQLiteReadonlyDBAdapter()
    result = adapter.execute('WITH deleted AS (DELETE FROM orders RETURNING *) SELECT * FROM deleted')
    assert result['status'] == 'error'
    assert result['error_type'] == 'readonly_violation'


def test_stream_task_can_be_queried_after_client_reads_start_event(monkeypatch):
    def fake_ask_release(query, session_id=None, use_llm=False, headers=None):
        return _release_env('ok', query=query)

    monkeypatch.setattr(release_api, 'ask_release', fake_ask_release)
    response = asyncio.run(server.query_stream(
        QueryRequest(query='最近7天GMV', session_id='sid', use_llm=False),
        _DummyRequest(headers={'x-dev-tenant-id': 'tenant-a'}),
        None,
    ))
    first = asyncio.run(_first_sse_event(response))
    assert first['type'] == 'start'
    task_id = first['task_id']
    asyncio.run(asyncio.sleep(0.05))
    status_response = asyncio.run(server.task_status(task_id, None))
    payload = json.loads(status_response.body.decode('utf-8'))
    assert payload['contract'] == 'stream_task_v1'
    assert payload['task_id'] == task_id
    assert payload['status'] in ('ok', 'running')
    if payload['status'] == 'ok':
        assert payload['result']['contract'] == 'release_v1_envelope'


def test_supervisor_rejects_malformed_task_payload_without_typeerror():
    runtime = SupervisorRuntime(max_steps=3)
    result = runtime.run(['not-a-task'])
    assert result['status'] == RESULT_ERROR
    assert result['errors'][0]['error'] == 'invalid_task_payload'


def test_evidence_bus_rejects_missing_required_scope_fields():
    envelope = build_execution_envelope(
        status='ok', stage='db_execute', query_id='q_missing_scope', evidence_id='ev_missing_scope',
        authority='verified_execution', row_count=1)
    bus = EvidenceBus()
    bus.record_envelope(envelope)
    valid, rejected = bus.validate_scope(['ev_missing_scope'], expected_scope={
        'metric': 'gmv',
        'allowed_time_ranges': ['last_7_days'],
        'dataid': 'orders',
        'data_version': 'v1',
        'tenant_id': 'tenant_a',
        'permission_scope': {'role': 'analyst'},
    })
    assert valid == []
    assert rejected[0]['error'] == 'evidence_scope_mismatch'
    assert rejected[0]['fields'] == ['metric', 'time_range', 'dataid', 'data_version', 'tenant_id', 'permission_scope']
