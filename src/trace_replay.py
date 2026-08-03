# -*- coding: utf-8 -*-
"""Trace replay helpers for Agent Harness failures."""
from __future__ import unicode_literals

import os

from agent_harness import AgentHarness
from harness_snapshot import save_json


def find_case_by_id(cases, case_id):
    for case in cases or []:
        if case.get('id') == case_id or case.get('case_id') == case_id:
            return case
    return None


def replay_case(case, reports_dir=None):
    harness = AgentHarness(reports_dir=reports_dir)
    evaluated = harness.run_case(case)
    return {
        'id': evaluated.get('id'),
        'query': evaluated.get('query'),
        'expected': evaluated.get('expected'),
        'passed': evaluated.get('passed'),
        'failure_type': evaluated.get('failure_type'),
        'errors': evaluated.get('errors'),
        'result': evaluated.get('result'),
        'trace': evaluated.get('trace'),
        'trace_summary': evaluated.get('trace_summary'),
        'dag_trace': evaluated.get('dag_trace'),
        'dag_trace_completeness': evaluated.get('dag_trace_completeness'),
    }


def save_replay(root, replay):
    replay_dir = os.path.join(root, 'harness', 'replays')
    case_id = replay.get('id') or 'case'
    path = os.path.join(replay_dir, '%s_latest_replay.json' % case_id)
    save_json(path, replay)
    return path


__all__ = ['find_case_by_id', 'replay_case', 'save_replay']
