# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'src')
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from trace_completeness import aggregate_dag_trace, validate_trace_completeness


def test_aggregate_dag_trace_compacts_names_and_contract():
    trace = [
        {'name': 'governance'},
        {'name': 'route'},
        {'name': 'plan'},
        {'name': 'execute'},
        {'name': 'analyze'},
        {'name': 'explain'},
        {'name': 'return'},
    ]
    agg = aggregate_dag_trace(trace)
    assert agg['contract'] == 'dag_trace_summary_v1'
    assert agg['observed_nodes'][0] == 'governance'
    assert agg['observed_nodes'][-1] == 'return'
    assert agg['node_count'] == 7


def test_validate_trace_completeness_detects_missing_nodes():
    trace = [{'name': 'governance'}, {'name': 'route'}, {'name': 'plan'}]
    comp = validate_trace_completeness('ok', trace)
    assert comp['contract'] == 'trace_completeness_v1'
    assert comp['complete'] is False
    assert 'execute' in comp['missing_nodes']
    assert comp['summary']['first_failure'] == 'execute'


def test_validate_trace_completeness_degrades_for_terminal_failures():
    comp = validate_trace_completeness('blocked', [])
    assert comp['status'] == 'blocked'
    assert comp['complete'] is False
    assert comp['expected_nodes']
