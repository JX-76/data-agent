# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'src')
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from db_adapter import MockDBAdapter, ReadonlyQueryExecutor
from external_tool_executor import ExternalToolExecutor
from external_tool_registry import ExternalToolRegistry
from observability import ObservationRecorder


def test_external_catalog_trace_and_contract():
    observer = ObservationRecorder()
    executor = ExternalToolExecutor(observer=observer)
    result = executor.call('semantic.catalog_read', {}, {'intent': 'metric_query', 'trace_id': 't_ext_1'})
    assert result['status'] == 'ok'
    assert 'metrics' in result['data']
    assert result['trace_event']['name'] == 'external_tool_call'
    assert result['trace_event']['tool_id'] == 'semantic.catalog_read'
    assert result['execution_envelope']['status'] == 'ok'
    assert result['execution_envelope']['authority'] == 'verified_execution'
    assert result['execution_envelope']['tool_call_id'].startswith('tool:')
    assert result['execution_envelope']['evidence_id'] in result['evidence_refs']
    assert observer.events_as_dicts('t_ext_1')[0]['name'] == 'external_tool_call'


def test_external_query_policy_blocks_write_sql():
    executor = ExternalToolExecutor()
    result = executor.call('warehouse.query_sql', {'sql': 'DROP TABLE orders'}, {'intent': 'metric_query'})
    assert result['status'] == 'blocked'
    assert result['diagnostics']['failure_type'] == 'external_tool_policy_denied'
    assert result['execution_envelope']['status'] == 'blocked'
    assert result['execution_envelope']['authority'] == 'unverified'
    assert result['execution_envelope']['evidence_id'] is None


def test_external_query_uses_db_executor():
    query_log = []
    db = ReadonlyQueryExecutor(MockDBAdapter(query_log=query_log))
    executor = ExternalToolExecutor(db_executor=db)
    result = executor.call('warehouse.query_sql', {'sql': 'SELECT 1 AS value', 'limit': 5}, {'intent': 'metric_query'})
    assert result['status'] == 'ok'
    assert result['data']['source'] == 'mock'
    assert result['execution_envelope']['row_count'] == result['data']['row_count']
    assert result['authority'] == 'verified_execution'
    assert query_log == ['SELECT 1 AS value']


def test_external_registry_loads_defaults():
    registry = ExternalToolRegistry(config_path='missing-file.yaml')
    ids = [item['tool_id'] for item in registry.list_tools()]
    assert 'warehouse.query_sql' in ids
    assert 'semantic.catalog_read' in ids


def test_external_tool_error_has_unverified_execution_envelope():
    executor = ExternalToolExecutor()
    result = executor.call('missing.tool', {}, {'intent': 'metric_query', 'trace_id': 't_missing'})
    assert result['status'] == 'error'
    assert result['execution_envelope']['status'] == 'error'
    assert result['execution_envelope']['authority'] == 'unverified'
    assert result['execution_envelope']['error_code'] == 'external_tool_not_found'
    assert result['execution_envelope']['evidence_id'] is None
