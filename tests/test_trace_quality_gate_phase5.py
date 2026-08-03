# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import codecs
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'src')
if SRC not in sys.path:
    sys.path.insert(0, SRC)
SCRIPTS = os.path.join(ROOT, 'scripts')
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

from agent_harness import summarize_trace_quality
from release_dashboard import compute_dashboard, format_dashboard_text
from run_trace_completeness_gate import main as trace_gate_main


def test_summarize_trace_quality_counts_complete_missing_and_skipped():
    results = [
        {'dag_trace_completeness': {'observed_nodes': ['governance', 'route'], 'complete': True, 'missing_nodes': [], 'summary': {}}},
        {'dag_trace_completeness': {'observed_nodes': ['governance'], 'complete': False, 'missing_nodes': ['execute'], 'summary': {'first_failure': 'execute'}}},
        {'case_type': 'external_tool'},
    ]
    q = summarize_trace_quality(results)
    assert q['contract'] == 'trace_quality_summary_v1'
    assert q['evaluated_count'] == 2
    assert q['complete_count'] == 1
    assert q['skipped_count'] == 1
    assert q['missing_node_breakdown']['execute'] == 1
    assert q['first_failure_breakdown']['execute'] == 1


def test_dashboard_trace_quality_is_safe_aggregate_only():
    d = compute_dashboard([{'status': 'ok', 'query': 'secret', 'sql': 'select *'}], {'total': 1, 'ok': 1}, trace_quality={
        'evaluated_count': 2,
        'complete_count': 1,
        'incomplete_count': 1,
        'complete_rate': 0.5,
        'missing_node_breakdown': {'execute': 1},
    })
    assert d['trace_quality']['contract'] == 'trace_quality_summary_v1'
    assert d['trace_quality']['complete_rate'] == 0.5
    assert 'query' not in json.dumps(d, ensure_ascii=False)
    assert 'select *' not in json.dumps(d, ensure_ascii=False)
    assert 'Trace quality' in format_dashboard_text(d)


def test_trace_completeness_gate_passes_for_simple_case_file():
    fd, path = tempfile.mkstemp(suffix='.jsonl')
    os.close(fd)
    try:
        with codecs.open(path, 'w', encoding='utf-8') as f:
            f.write(json.dumps({'id': 'tg1', 'query': '最近7天GMV', 'expected': {'status': 'ok'}}, ensure_ascii=False) + '\n')
        assert trace_gate_main(['--cases', path, '--min-complete-rate', '0.0', '--json']) == 0
    finally:
        if os.path.exists(path):
            os.remove(path)
