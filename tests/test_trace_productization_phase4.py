# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'src')
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from agent_harness import AgentHarness
from harness_snapshot import normalize_case_result
from trace_replay import replay_case


def test_harness_result_includes_read_only_dag_trace_summary():
    harness = AgentHarness()
    evaluated = harness.run_case({
        'id': 'phase4_dag_trace',
        'query': '最近7天GMV',
        'expected': {'status': 'ok'},
    })
    assert evaluated['dag_trace']['contract'] == 'dag_trace_summary_v1'
    assert evaluated['dag_trace_completeness']['contract'] == 'trace_completeness_v1'
    assert 'precheck' in evaluated['dag_trace_completeness']['observed_nodes']


def test_snapshot_and_replay_preserve_additive_trace_summaries():
    case = {
        'id': 'phase4_replay',
        'query': '最近7天GMV',
        'expected': {'status': 'ok'},
    }
    replay = replay_case(case)
    assert replay['dag_trace']['contract'] == 'dag_trace_summary_v1'
    assert replay['dag_trace_completeness']['status'] == 'ok'

    snapshot = normalize_case_result({
        'id': replay['id'],
        'result': replay['result'],
        'trace': replay['trace'],
        'expected': replay['expected'],
        'passed': replay['passed'],
    })
    assert snapshot['dag_trace']['contract'] == 'trace_completeness_v1'
    assert 'observed_nodes' in snapshot['dag_trace']
