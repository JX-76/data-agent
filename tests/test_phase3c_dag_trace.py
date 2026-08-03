# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'src')
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from agent_facade import AgentFacade
from observability import ObservationRecorder


def _dag_nodes(trace):
    return [e for e in trace if e.get('name') == 'dag_node']


def test_phase3c_success_trace_has_core_dag_nodes():
    facade = AgentFacade(session_id='phase3c-ok')
    facade.observer = ObservationRecorder()
    result = facade.ask('最近7天GMV')
    trace = facade.get_trace(result['trace_id'])
    nodes = _dag_nodes(trace)
    node_names = [e['stage'] for e in nodes]
    for expected in ['precheck', 'route', 'execute', 'analyze', 'report']:
        assert expected in node_names
    for event in nodes:
        payload = event['metadata']
        assert payload['contract'] == 'controlled_dag_trace_event_v1'
        assert payload['node'] == event['stage']


def test_phase3c_blocked_trace_stops_at_precheck():
    facade = AgentFacade(session_id='phase3c-block')
    facade.observer = ObservationRecorder()
    result = facade.ask('drop table orders')
    trace = facade.get_trace(result['trace_id'])
    nodes = _dag_nodes(trace)
    node_names = [e['stage'] for e in nodes]
    assert 'precheck' in node_names
    assert 'execute' not in node_names
    assert nodes[0]['metadata']['status'] == 'blocked'


def test_phase3c_trace_completeness_for_normal_terminal_ok():
    facade = AgentFacade(session_id='phase3c-complete')
    facade.observer = ObservationRecorder()
    result = facade.ask('按渠道看GMV')
    assert result['status'] == 'ok'
    trace = facade.get_trace(result['trace_id'])
    dag_node_names = set(e['stage'] for e in _dag_nodes(trace))
    assert set(['precheck', 'route', 'execute', 'analyze', 'report']).issubset(dag_node_names)
    assert trace[-1]['name'] == 'complete'
