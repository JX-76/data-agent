# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'src')
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from diagnosis_runtime import (DiagnosisRuntime, COMPLETE, DEGRADED,
                               DRILLING_CHANNEL, GENERATING_REPORT, VERIFYING)
from external_tool_registry import ExternalToolRegistry, DEFAULT_EXTERNAL_TOOLS
from external_tool_executor import ExternalToolExecutor


def _runtime():
    return DiagnosisRuntime(max_tool_calls=3)


def test_runtime_checkpoint_preserves_only_compact_current_tool_evidence():
    runtime = _runtime()
    state = runtime.start('shop-1', {'type': 'anomaly', 'metric': 'gmv'}, task_id='diag-1')
    saved = runtime.record_tool_result(state['task_id'], 'ecommerce.overview', {
        'status': 'ok', 'data': {'current': 80, 'baseline': 100, 'large_rows': ['x'] * 1000}}, 'overall')
    restored = runtime.resume('diag-1')
    assert saved['checkpoint_version'] >= 2
    assert restored['evidence'][0]['authority'] == 'current_tool_execution'
    assert 'large_rows' in restored['evidence'][0]['summary']
    assert len(restored['evidence'][0]['summary']) <= 480
    assert 'data' not in restored['evidence'][0]


def test_state_path_and_verifier_rejects_unsupported_fact_then_allows_evidenced_report():
    runtime = _runtime()
    state = runtime.start('shop-1', {'type': 'anomaly'}, task_id='diag-2')
    state = runtime.record_tool_result(state['task_id'], 'ecommerce.overview',
                                       {'status': 'ok', 'data': {'delta_pct': -0.2}})
    assert runtime.advance(state['task_id'])['status'] == DRILLING_CHANNEL
    for unused in range(4):
        state = runtime.advance(state['task_id'])
    assert state['status'] == VERIFYING
    rejected = runtime.verify_report(state['task_id'], {'findings': [{'id': 'f1', 'kind': 'fact', 'evidence_refs': ['ev_999']} ]})
    assert rejected['status'] == GENERATING_REPORT
    state = runtime.advance(state['task_id'])
    assert state['status'] == VERIFYING
    accepted = runtime.verify_report(state['task_id'], {'findings': [{'id': 'f1', 'kind': 'fact', 'evidence_refs': ['ev_000']}], 'recommendations': []})
    assert accepted['status'] == COMPLETE


def test_tool_budget_degrades_and_discovery_is_state_bounded():
    runtime = DiagnosisRuntime(max_tool_calls=1)
    state = runtime.start('shop-1', {'type': 'anomaly'}, task_id='diag-3')
    registry = ExternalToolRegistry(tools=DEFAULT_EXTERNAL_TOOLS)
    discovered = runtime.candidates(state['task_id'], registry)
    # Candidate discovery respects the remaining execution budget.
    assert discovered['candidate_tool_ids'] == ['ecommerce.overview']
    runtime.record_tool_result(state['task_id'], 'ecommerce.overview', {'status': 'ok', 'data': {'current': 1}})
    state = runtime.record_tool_result(state['task_id'], 'ecommerce.overview', {'status': 'ok', 'data': {'current': 2}})
    assert state['status'] == DEGRADED


def test_ecommerce_sandbox_tools_are_governed_and_return_contract_data():
    registry = ExternalToolRegistry(tools=DEFAULT_EXTERNAL_TOOLS)
    executor = ExternalToolExecutor(registry=registry)
    result = executor.call('ecommerce.channel_performance', {'metric': 'gmv'}, {'intent': 'anomaly'})
    assert result['status'] == 'ok'
    assert result['data']['row_count'] == 2
