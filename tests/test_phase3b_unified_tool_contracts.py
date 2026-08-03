# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'src')
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from data_source_gateway import DataSourceGateway
from external_tool_trace import ExternalToolTraceRecorder
from mcp_adapter import McpAdapter


def test_external_tool_success_exposes_tool_plan_and_dag_trace():
    result = DataSourceGateway().query_sql('SELECT 1 AS value', context={'trace_id': 'phase3b_ok'})
    assert result['status'] == 'ok'
    assert result['diagnostics']['tool_invocation_plan']['contract'] == 'tool_invocation_plan_v1'
    assert result['diagnostics']['tool_invocation_plan']['tool_id'] == 'warehouse.query_sql'
    assert result['trace_event']['dag_event']['contract'] == 'controlled_dag_trace_event_v1'
    assert result['trace_event']['dag_event']['node'] == 'tool_call'


def test_external_tool_blocked_exposes_tool_plan_and_dag_trace():
    result = DataSourceGateway().query_sql('DROP TABLE orders', context={'trace_id': 'phase3b_block'})
    assert result['status'] == 'blocked'
    assert result['diagnostics']['tool_invocation_plan']['contract'] == 'tool_invocation_plan_v1'
    assert result['trace_event']['dag_event']['status'] == 'blocked'


def test_mcp_gateway_returns_unified_tool_contracts():
    response = McpAdapter().handle_request({
        'jsonrpc': '2.0',
        'id': 'phase3b_mcp',
        'method': 'tools/call',
        'params': {
            'name': 'warehouse.query_sql',
            'arguments': {'sql': 'SELECT 1 AS value'},
            'context': {'intent': 'metric_query', 'trace_id': 'phase3b_mcp_trace'},
        },
    })
    result = response['result']
    assert result['diagnostics']['tool_invocation_plan']['tool_id'] == 'warehouse.query_sql'
    assert result['trace_event']['dag_event']['contract'] == 'controlled_dag_trace_event_v1'


def test_trace_recorder_accepts_phase3b_metadata_without_breaking_shape():
    recorder = ExternalToolTraceRecorder()
    event = recorder.record('trace_x', 'warehouse.query_sql', 'ok', args={'sql': 'SELECT 1'}, output={'rows': []},
                            spec={'timeout_ms': 10, 'risk_level': 'low'},
                            policy={'allowed': True}, tool_plan={'contract': 'tool_invocation_plan_v1'},
                            dag_event={'contract': 'controlled_dag_trace_event_v1', 'node': 'tool_call'})
    assert event['tool_id'] == 'warehouse.query_sql'
    assert event['tool_plan']['contract'] == 'tool_invocation_plan_v1'
    assert event['dag_event']['node'] == 'tool_call'
