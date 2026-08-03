# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'src')
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from contracts import build_execution_envelope
from db_adapter import ReadonlyQueryExecutor, SQLiteReadonlyDBAdapter
from execution_engine import ExecutionEngine
from ecommerce_graphs import (
    GRAPH_METRIC_QUERY, GRAPH_BREAKDOWN, GRAPH_COMPARISON,
    GRAPH_ROOT_CAUSE, GRAPH_REPORT,
    build_breakdown_graph, build_comparison_graph, build_root_cause_graph,
    build_report_graph, build_ecommerce_worker_registry, run_ecommerce_graph,
)
from ecommerce_answer_adapter import adapt_ecommerce_graph_to_result
from ecommerce_workers import auditor_worker
from evidence_bus import EvidenceBus
from case_blackboard import CaseBlackboard
from gmv_health_playbook import build_gmv_health_case, gmv_health_expected_scope
from multi_agent_contracts import AgentTask, AgentResult, RESULT_BLOCKED, RESULT_OK
from supervisor_runtime import SupervisorRuntime
from trace_contracts import validate_reexecution_replay
from worker_registry import WorkerSpec


def _request(**overrides):
    data = {
        'metric': 'gmv',
        'time_range': 'last_7_days',
        'dimensions': [],
        'rows': [{'gmv': 100}],
        'query_id': 'q_current',
        'evidence_id': 'ev_current',
        'dataid': 'orders',
        'data_version': 'v1',
    }
    data.update(overrides)
    return data


def test_metric_query_graph_runs_data_analyst_then_auditor_with_verified_evidence():
    result = run_ecommerce_graph(GRAPH_METRIC_QUERY, _request(), trace_id='trace-metric')

    assert result['status'] == 'ok'
    assert result['graph']['graph_type'] == GRAPH_METRIC_QUERY
    assert result['node_states']['data_analyst'] == 'succeeded'
    assert result['node_states']['auditor'] == 'succeeded'
    analyst = result['results']['data_analyst']['output']
    assert analyst['execution_envelope']['authority'] == 'verified_execution'
    assert analyst['evidence_refs'] == ['ev_current']
    audit = result['results']['auditor']['output']
    assert audit['audit_status'] == 'ok'
    assert audit['evidence_refs'] == ['ev_current']


def test_breakdown_graph_declares_diagnosis_and_preserves_evidence_scope():
    graph = build_breakdown_graph(_request(dimensions=['channel'], rows=[{'channel': 'ads', 'gmv': 60}]))
    task_ids = [t['task_id'] for t in graph.tasks]
    assert task_ids == ['data_analyst', 'diagnosis', 'auditor']
    assert graph.required_slots == ['metric', 'time_range', 'dimensions']

    result = run_ecommerce_graph(GRAPH_BREAKDOWN, _request(dimensions=['channel'], rows=[{'channel': 'ads', 'gmv': 60}]), trace_id='trace-breakdown')
    assert result['status'] == 'ok'
    diagnosis = result['results']['diagnosis']['output']
    assert diagnosis['findings'][0]['evidence_ids'] == ['ev_current']
    assert diagnosis['limitations'] == ['diagnosis_is_not_causal_proof']
    audit = result['results']['auditor']['output']
    assert audit['unsupported_claims'] == []


def test_comparison_graph_requires_two_verified_periods():
    graph = build_comparison_graph(_request(compare_time_range='previous_7_days', previous_rows=[{'gmv': 80}]))
    assert graph.required_evidence == ['verified_execution:current', 'verified_execution:previous']

    result = run_ecommerce_graph(
        GRAPH_COMPARISON,
        _request(compare_time_range='previous_7_days', previous_rows=[{'gmv': 80}], previous_evidence_id='ev_previous'),
        trace_id='trace-comparison')
    assert result['status'] == 'ok'
    assert result['node_states']['current_period'] == 'succeeded'
    assert result['node_states']['previous_period'] == 'succeeded'
    audit = result['results']['auditor']['output']
    assert audit['audit_status'] == 'ok'
    assert audit['evidence_refs'] == ['ev_current', 'ev_previous']


def test_execution_error_does_not_run_downstream_nodes_as_ok():
    result = run_ecommerce_graph(
        GRAPH_METRIC_QUERY,
        _request(simulate_error=True, rows=[], evidence_id=None),
        trace_id='trace-error')

    assert result['status'] == 'partial'
    assert result['node_states']['data_analyst'] == 'failed'
    assert result['node_states']['auditor'] == 'skipped'
    analyst = result['results']['data_analyst']
    assert analyst['status'] != 'ok'
    assert analyst['output']['execution_envelope']['authority'] == 'unverified'
    assert analyst['output']['execution_envelope']['evidence_id'] is None


def test_data_analyst_rejects_unverified_execution_envelope():
    envelope = build_execution_envelope(
        status='ok', stage='execute', query_id='q1', evidence_id=None,
        row_count=1, authority='unverified')
    result = run_ecommerce_graph(
        GRAPH_METRIC_QUERY,
        _request(execution_envelope=envelope, rows=[], metric='gmv', evidence_id=None),
        trace_id='trace-unverified')

    assert result['status'] == 'pending_human_review'
    assert result['node_states']['data_analyst'] == 'blocked'
    assert result['node_states']['auditor'] == 'skipped'
    analyst = result['results']['data_analyst']
    assert analyst['output']['authority'] == 'unverified'
    assert analyst['output']['evidence_refs'] == []


def test_auditor_blocks_scope_mismatched_verified_evidence():
    envelope = build_execution_envelope(
        status='ok', stage='execute', query_id='q_wrong_metric', evidence_id='ev_wrong_metric',
        dataid='orders', data_version='v1', row_count=1, time_range='last_7_days',
        authority='verified_execution', metadata={'metric': 'orders', 'dimensions': []})
    result = run_ecommerce_graph(
        GRAPH_METRIC_QUERY,
        _request(metric='gmv', execution_envelope=envelope, rows=[{'orders': 10}]),
        trace_id='trace-scope-mismatch')

    assert result['status'] == 'partial'
    assert result['node_states']['data_analyst'] == 'failed'
    assert result['node_states']['auditor'] == 'skipped'
    analyst = result['results']['data_analyst']
    assert analyst['status'] == 'error'
    assert analyst['errors'][0]['error'] == 'invalid_evidence_scope'
    assert analyst['errors'][0]['rejected'][0]['error'] == 'evidence_scope_mismatch'
    assert 'metric' in analyst['errors'][0]['rejected'][0]['fields']
    adapted = adapt_ecommerce_graph_to_result(result, query='GMV', trace_id='trace-scope-mismatch')
    assert adapted['status'] == 'no_answer'
    assert adapted['final_answer']['facts'] == []


def test_comparison_auditor_allows_current_and_previous_time_scopes():
    result = run_ecommerce_graph(
        GRAPH_COMPARISON,
        _request(compare_time_range='previous_7_days', previous_rows=[{'gmv': 80}], previous_evidence_id='ev_previous'),
        trace_id='trace-comparison-scope')

    assert result['status'] == 'ok'
    audit = result['results']['auditor']['output']
    assert audit['scope_mismatches'] == []
    assert audit['evidence_refs'] == ['ev_current', 'ev_previous']


def test_graph_preflight_blocks_missing_required_slots_without_running_workers():
    result = run_ecommerce_graph(
        GRAPH_BREAKDOWN,
        _request(dimensions=[], rows=[{'gmv': 100}]),
        trace_id='trace-missing-dimensions')

    assert result['status'] == 'blocked'
    assert result['blocked_reason'] == 'missing_required_graph_slots'
    assert result['missing_slots'] == ['dimensions']
    assert result['errors'][0]['error'] == 'missing_required_graph_slots'
    assert result['results'] == {}
    assert result['node_states']['data_analyst'] == 'skipped'
    assert result['node_states']['diagnosis'] == 'skipped'
    assert result['node_states']['auditor'] == 'skipped'


def test_graph_preflight_blocks_metric_query_missing_time_range():
    result = run_ecommerce_graph(
        GRAPH_METRIC_QUERY,
        _request(time_range=None),
        trace_id='trace-missing-time')

    assert result['status'] == 'blocked'
    assert result['missing_slots'] == ['time_range']
    assert result['results'] == {}
    assert result['node_states']['data_analyst'] == 'skipped'
    assert result['graph']['required_slots'] == ['metric', 'time_range']


def test_graph_preflight_accepts_previous_time_range_alias_for_comparison():
    result = run_ecommerce_graph(
        GRAPH_COMPARISON,
        _request(previous_time_range='previous_7_days', previous_rows=[{'gmv': 80}], previous_evidence_id='ev_previous'),
        trace_id='trace-previous-alias')

    assert result['status'] == 'ok'
    assert result['node_states']['current_period'] == 'succeeded'
    assert result['node_states']['previous_period'] == 'succeeded'
    audit = result['results']['auditor']['output']
    assert audit['audit_status'] == 'ok'


def test_runtime_max_steps_stalls_to_error_without_ok_consumption():
    runtime = SupervisorRuntime(worker_registry=build_ecommerce_worker_registry(), max_steps=1, retry_limit=0)
    result = run_ecommerce_graph(GRAPH_METRIC_QUERY, _request(), trace_id='trace-max-steps', runtime=runtime)

    assert result['status'] == 'error'
    assert result['errors'][0]['error'] == 'max_steps_exceeded'
    adapted = adapt_ecommerce_graph_to_result(result, query='GMV', trace_id='trace-max-steps')
    assert adapted['status'] == 'error'
    assert adapted['final_answer']['facts'] == []


def test_auditor_blocks_unsupported_fact_even_if_dependency_status_is_ok():
    task = AgentTask('auditor', task_id='auditor', dependencies=['bad'])
    state = {
        'results': {
            'bad': AgentResult('bad', status=RESULT_OK, output={
                'facts': [{'text': 'GMV grew 20%', 'evidence_ids': []}],
                'evidence_refs': [],
            }).to_dict()
        }
    }
    result = auditor_worker(task, state)
    assert result.status == RESULT_BLOCKED
    assert result.output['audit_status'] == 'blocked'
    assert result.output['unsupported_claims'] == ['GMV grew 20%']


class _FakeExecutionEngine(object):
    def __init__(self, result):
        self.result = result
        self.calls = []

    def execute(self, plan, trace_id=None, task_id=None):
        self.calls.append({'plan': plan, 'trace_id': trace_id, 'task_id': task_id})
        return self.result


def test_ecommerce_registry_overrides_placeholder_workers():
    registry = build_ecommerce_worker_registry()
    task = AgentTask('data_analyst', task_id='da1', task_input=_request())
    result = registry.run(task)
    assert result.status == RESULT_OK
    assert result.output['authority'] == 'verified_execution'
    assert result.output['evidence_refs'] == ['ev_current']


def test_data_analyst_can_call_execution_engine_and_record_evidence_bus():
    envelope = build_execution_envelope(
        status='ok', stage='db_execute', query_id='q_engine', evidence_id='ev_engine',
        dataid='orders', data_version='v2', row_count=1, time_range='last_7_days',
        authority='verified_execution', metadata={'metric': 'gmv', 'dimensions': []})
    engine = _FakeExecutionEngine({
        'status': 'ok',
        'results': [{'gmv': 123}],
        'execution_envelope': envelope,
    })
    bus = EvidenceBus()

    result = run_ecommerce_graph(
        GRAPH_METRIC_QUERY,
        _request(rows=[], execution_engine=engine,
                 execution_plan={'model': 'order_detail', 'metric': 'gmv'},
                 evidence_bus=bus, data_version='v2'),
        trace_id='trace-engine')

    assert result['status'] == 'ok'
    assert engine.calls[0]['task_id'] == 'data_analyst'
    analyst = result['results']['data_analyst']['output']
    assert analyst['rows'] == [{'gmv': 123}]
    assert analyst['evidence_refs'] == ['ev_engine']
    assert analyst['evidence_bus']['records'][0]['evidence_id'] == 'ev_engine'


def test_graph_answer_adapter_promotes_only_audited_verified_facts():
    result = run_ecommerce_graph(
        GRAPH_BREAKDOWN,
        _request(dimensions=['channel'], rows=[{'channel': 'ads', 'gmv': 60}]),
        trace_id='trace-adapter')

    adapted = adapt_ecommerce_graph_to_result(result, query='按渠道拆 GMV', trace_id='trace-adapter', task_id='task-adapter')

    assert adapted['status'] == 'ok'
    assert adapted['final_answer']['status'] == 'ok'
    assert adapted['final_answer']['facts']
    assert adapted['final_answer']['facts'][0]['evidence_ids'] == ['ev_current']
    assert adapted['final_answer']['citations'] == ['ev_current']
    assert adapted['provenance']['evidence_bus']['records'][0]['evidence_id'] == 'ev_current'


def test_ecommerce_worker_registry_enforces_evidence_refs_for_key_workers():
    registry = build_ecommerce_worker_registry()
    specs = dict((item['worker_type'], item) for item in registry.list_workers())

    for worker_type in ('data_analyst', 'diagnosis', 'auditor'):
        assert specs[worker_type]['output_schema']['evidence_ids_must_resolve'] is True
        assert specs[worker_type]['output_schema']['evidence_scope_must_match'] is True
        assert 'evidence_ids' in specs[worker_type]['output_schema']['required']


def test_ecommerce_graph_rejects_key_worker_unresolved_evidence_ids():
    registry = build_ecommerce_worker_registry()
    registry.register(WorkerSpec(
        'diagnosis',
        lambda task, dag_state=None: AgentResult(task.task_id, status=RESULT_OK, output={
            'findings': [{'text': 'unsafe unsupported finding', 'evidence_ids': ['missing_ev']}],
            'evidence_ids': ['missing_ev'],
            'limitations': [],
        }),
        output_schema={
            'required': ['findings', 'evidence_ids', 'limitations'],
            'properties': {
                'findings': {'type': 'array'},
                'evidence_ids': {'type': 'array'},
                'limitations': {'type': 'array'},
            },
            'evidence_ids_must_resolve': True,
        },
    ))

    result = run_ecommerce_graph(
        GRAPH_BREAKDOWN,
        _request(dimensions=['channel'], rows=[{'channel': 'ads', 'gmv': 60}]),
        trace_id='trace-missing-diagnosis-evidence',
        worker_registry=registry)

    assert result['status'] == 'partial'
    assert result['node_states']['data_analyst'] == 'succeeded'
    assert result['node_states']['diagnosis'] == 'failed'
    assert result['node_states']['auditor'] == 'skipped'
    assert result['results']['diagnosis']['status'] == 'error'
    assert result['results']['diagnosis']['errors'][0]['error'] == 'unresolved_evidence_ids'
    assert result['results']['diagnosis']['output']['authority'] == 'unverified'


def test_ecommerce_graph_rejects_cross_scope_worker_evidence_before_audit():
    envelope = build_execution_envelope(
        status='ok', stage='execute', query_id='q_cross_scope', evidence_id='ev_cross_scope',
        dataid='orders', data_version='v1', row_count=1, time_range='last_7_days',
        authority='verified_execution', metadata={'metric': 'orders', 'dimensions': []})

    result = run_ecommerce_graph(
        GRAPH_METRIC_QUERY,
        _request(metric='gmv', execution_envelope=envelope, rows=[{'orders': 10}]),
        trace_id='trace-cross-scope-worker')

    assert result['status'] == 'partial'
    assert result['node_states']['data_analyst'] == 'failed'
    assert result['node_states']['auditor'] == 'skipped'
    analyst = result['results']['data_analyst']
    assert analyst['errors'][0]['error'] == 'invalid_evidence_scope'
    assert analyst['errors'][0]['rejected'][0]['error'] == 'evidence_scope_mismatch'
    assert 'metric' in analyst['errors'][0]['rejected'][0]['fields']
    adapted = adapt_ecommerce_graph_to_result(result, query='GMV', trace_id='trace-cross-scope-worker')
    assert adapted['final_answer']['facts'] == []


def test_ecommerce_graph_rejects_ttl_expired_worker_evidence_before_audit():
    result = run_ecommerce_graph(
        GRAPH_METRIC_QUERY,
        _request(evidence_ttl_seconds=0.001, evidence_now=9999999999.0),
        trace_id='trace-worker-ttl-expired')

    assert result['status'] == 'partial'
    assert result['node_states']['data_analyst'] == 'failed'
    assert result['node_states']['auditor'] == 'skipped'
    analyst = result['results']['data_analyst']
    assert analyst['errors'][0]['error'] == 'invalid_evidence_scope'
    assert analyst['errors'][0]['rejected'][0]['error'] == 'evidence_ttl_expired'
    adapted = adapt_ecommerce_graph_to_result(result, query='GMV', trace_id='trace-worker-ttl-expired')
    assert adapted['status'] == 'no_answer'
    assert adapted['final_answer']['facts'] == []


def test_graph_answer_adapter_does_not_emit_facts_for_failed_execution():
    result = run_ecommerce_graph(
        GRAPH_METRIC_QUERY,
        _request(simulate_error=True, rows=[], evidence_id=None),
        trace_id='trace-adapter-error')

    adapted = adapt_ecommerce_graph_to_result(result, query='GMV', trace_id='trace-adapter-error')

    assert adapted['status'] == 'no_answer'
    assert adapted['final_answer']['status'] == 'no_answer'
    assert adapted['final_answer']['facts'] == []
    assert adapted['final_answer']['evidence_ids'] == []
    assert 'retry_with_verified_execution_evidence' in adapted['next_actions']


def test_root_cause_graph_emits_only_candidate_not_causal_findings():
    graph = build_root_cause_graph(_request(dimensions=['channel']))
    assert graph.required_slots == ['metric', 'time_range', 'dimensions']
    assert 'claim_scope:contribution_not_causal' in graph.required_evidence

    result = run_ecommerce_graph(
        GRAPH_ROOT_CAUSE,
        _request(dimensions=['channel'], rows=[{'channel': 'ads', 'gmv': 60}]),
        trace_id='trace-root-cause')

    assert result['status'] == 'ok'
    diagnosis = result['results']['diagnosis']['output']
    assert diagnosis['findings'][0]['kind'] == 'contribution_candidate_not_causal'
    assert diagnosis['findings'][0]['evidence_ids'] == ['ev_current']
    assert 'causal claim' not in diagnosis['findings'][0]['text'].lower()
    assert diagnosis['findings'][0]['top_candidate']['label'] == 'channel=ads'
    assert diagnosis['findings'][0]['baseline_warning'] == 'baseline_missing_metric_values'
    adapted = adapt_ecommerce_graph_to_result(result, query='诊断 GMV 下滑原因', trace_id='trace-root-cause')
    assert adapted['final_answer']['status'] == 'ok'
    assert adapted['final_answer']['facts'][0]['evidence_ids'] == ['ev_current']
    assert 'diagnosis_is_not_causal_proof' in adapted['final_answer']['limitations']


def test_report_graph_compiles_evidence_only_report_section():
    graph = build_report_graph(_request())
    assert graph.required_slots == ['metric', 'time_range']
    assert 'claim_scope:verified_report_only' in graph.required_evidence

    result = run_ecommerce_graph(
        GRAPH_REPORT,
        _request(rows=[{'gmv': 100}]),
        trace_id='trace-report')

    assert result['status'] == 'ok'
    finding = result['results']['diagnosis']['output']['findings'][0]
    assert finding['kind'] == 'verified_report_section'
    assert finding['evidence_ids'] == ['ev_current']
    adapted = adapt_ecommerce_graph_to_result(result, query='生成 GMV 报告', trace_id='trace-report')
    assert adapted['final_answer']['status'] == 'ok'
    assert adapted['final_answer']['facts']
    assert adapted['final_answer']['citations'] == ['ev_current']


def test_root_cause_graph_with_execution_engine_builds_real_comparison_plan():
    conn = sqlite3.connect(':memory:')
    conn.execute('CREATE TABLE fct_orders (order_id TEXT, store_id TEXT, product_id TEXT, channel TEXT, order_status TEXT, sell_through REAL, paid_at TEXT)')
    rows = [
        ('o1', 's1', 'p1', 'ads', 'paid', 100.0, '2026-07-10'),
        ('o2', 's1', 'p1', 'organic', 'paid', 80.0, '2026-07-10'),
        ('o3', 's1', 'p1', 'ads', 'paid', 40.0, '2026-07-03'),
        ('o4', 's1', 'p1', 'organic', 'paid', 120.0, '2026-07-03'),
    ]
    conn.executemany('INSERT INTO fct_orders VALUES (?, ?, ?, ?, ?, ?, ?)', rows)
    engine = ExecutionEngine(executor=ReadonlyQueryExecutor(SQLiteReadonlyDBAdapter(connection=conn)), max_retries=0)

    result = run_ecommerce_graph(
        GRAPH_ROOT_CAUSE,
        _request(rows=[], execution_engine=engine, dimensions=['channel'], time_range=('2026-07-10', '2026-07-10'),
                 compare_time_range=('2026-07-03', '2026-07-03'), query_id=None, evidence_id=None),
        trace_id='trace-root-cause-engine')

    assert result['status'] == 'ok'
    analyst = result['results']['data_analyst']['output']
    assert analyst['authority'] == 'verified_execution'
    assert analyst['rows']
    assert analyst['execution_envelope']['authority'] == 'verified_execution'
    compiled = analyst['execution_envelope']['metadata'].get('compiled_sql') or {}
    assert compiled.get('task_type') == 'comparison'
    assert compiled.get('claim_scope') == 'contribution_not_causal'
    assert analyst['execution_envelope']['metadata']['preflight_contract'] == 'sql_preflight_v1'
    diagnosis = result['results']['diagnosis']['output']
    assert diagnosis['findings'][0]['kind'] == 'contribution_candidate_not_causal'
    adapted = adapt_ecommerce_graph_to_result(result, query='诊断 GMV 下滑原因', trace_id='trace-root-cause-engine')
    assert adapted['final_answer']['status'] == 'ok'
    assert adapted['final_answer']['facts'][0]['evidence_ids'] == analyst['evidence_refs']
    assert 'diagnosis_is_not_causal_proof' in adapted['final_answer']['limitations']


def test_root_cause_graph_failure_skips_diagnosis_and_auditor():
    result = run_ecommerce_graph(
        GRAPH_ROOT_CAUSE,
        _request(dimensions=['channel'], simulate_error=True, rows=[], evidence_id=None),
        trace_id='trace-root-cause-error')

    assert result['status'] == 'partial'
    assert result['node_states']['data_analyst'] == 'failed'
    assert result['node_states']['diagnosis'] == 'skipped'
    assert result['node_states']['auditor'] == 'skipped'
    adapted = adapt_ecommerce_graph_to_result(result, query='诊断 GMV 下滑原因', trace_id='trace-root-cause-error')
    assert adapted['status'] == 'no_answer'
    assert adapted['final_answer']['facts'] == []


def test_root_cause_contribution_candidates_are_ranked_and_evidence_bound():
    result = run_ecommerce_graph(
        GRAPH_ROOT_CAUSE,
        _request(dimensions=['channel'], rows=[
            {'channel': 'ads', 'current_gmv': 100, 'previous_gmv': 40},
            {'channel': 'organic', 'current_gmv': 80, 'previous_gmv': 120},
            {'channel': 'affiliate', 'current_gmv': 20, 'previous_gmv': 10},
        ]),
        trace_id='trace-root-cause-ranked')

    assert result['status'] == 'ok'
    finding = result['results']['diagnosis']['output']['findings'][0]
    candidates = finding['contribution_candidates']
    assert [item['label'] for item in candidates] == ['channel=ads', 'channel=organic', 'channel=affiliate']
    assert candidates[0]['delta'] == 60.0
    assert round(candidates[0]['contribution_share'], 2) == 0.55
    assert finding['top_candidate']['label'] == 'channel=ads'
    assert finding['evidence_ids'] == ['ev_current']
    assert 'causal' not in finding['text'].lower()


def test_root_cause_empty_verified_rows_blocks_and_emits_no_facts():
    result = run_ecommerce_graph(
        GRAPH_ROOT_CAUSE,
        _request(dimensions=['channel'], rows=[], row_count=0),
        trace_id='trace-root-cause-empty')

    assert result['status'] == 'pending_human_review'
    assert result['node_states']['data_analyst'] == 'succeeded'
    assert result['node_states']['diagnosis'] == 'blocked'
    assert result['node_states']['auditor'] == 'skipped'
    diagnosis = result['results']['diagnosis']['output']
    assert diagnosis['findings'] == []
    assert 'root_cause_requires_non_empty_verified_rows' in diagnosis['limitations']
    adapted = adapt_ecommerce_graph_to_result(result, query='诊断 GMV 下滑原因', trace_id='trace-root-cause-empty')
    assert adapted['status'] == 'blocked'
    assert adapted['final_answer']['facts'] == []


def test_root_cause_non_numeric_rows_blocks_as_evidence_limited():
    result = run_ecommerce_graph(
        GRAPH_ROOT_CAUSE,
        _request(dimensions=['channel'], rows=[{'channel': 'ads', 'gmv': 'NA'}]),
        trace_id='trace-root-cause-non-numeric')

    assert result['status'] == 'pending_human_review'
    assert result['node_states']['diagnosis'] == 'blocked'
    diagnosis = result['results']['diagnosis']['output']
    assert 'root_cause_requires_numeric_metric_values' in diagnosis['limitations']
    adapted = adapt_ecommerce_graph_to_result(result, query='诊断 GMV 下滑原因', trace_id='trace-root-cause-non-numeric')
    assert adapted['final_answer']['status'] == 'blocked'
    assert adapted['final_answer']['facts'] == []


def test_ecommerce_graph_blocks_cross_tenant_verified_execution():
    envelope = build_execution_envelope(
        status='ok', stage='execute', query_id='q_tenant_a', evidence_id='ev_tenant_a',
        dataid='orders', data_version='v1', row_count=1, time_range='last_7_days',
        authority='verified_execution',
        metadata={'metric': 'gmv', 'dimensions': [], 'tenant_id': 'tenant_a', 'user_id': 'user_a',
                  'permission_scope': {'regions': ['cn']}})

    result = run_ecommerce_graph(
        GRAPH_METRIC_QUERY,
        _request(execution_envelope=envelope, rows=[{'gmv': 100}], tenant_id='tenant_b',
                 user_id='user_a', permission_scope={'regions': ['cn']}),
        trace_id='trace-cross-tenant')

    assert result['status'] == 'partial'
    assert result['node_states']['data_analyst'] == 'failed'
    analyst = result['results']['data_analyst']
    assert analyst['status'] == 'error'
    assert analyst['errors'][0]['error'] == 'invalid_evidence_scope'
    assert 'tenant_id' in analyst['errors'][0]['rejected'][0]['fields']


def test_ecommerce_graph_blocks_cross_user_and_permission_scope_reuse():
    envelope = build_execution_envelope(
        status='ok', stage='execute', query_id='q_user_a', evidence_id='ev_user_a',
        dataid='orders', data_version='v1', row_count=1, time_range='last_7_days',
        authority='verified_execution',
        metadata={'metric': 'gmv', 'dimensions': [], 'tenant_id': 'tenant_a', 'user_id': 'user_a',
                  'permission_scope': {'regions': ['cn'], 'role': 'analyst'}})

    result = run_ecommerce_graph(
        GRAPH_METRIC_QUERY,
        _request(execution_envelope=envelope, rows=[{'gmv': 100}], tenant_id='tenant_a',
                 user_id='user_b', permission_scope={'regions': ['us'], 'role': 'analyst'}),
        trace_id='trace-cross-user-permission')

    assert result['status'] == 'partial'
    analyst = result['results']['data_analyst']
    assert analyst['errors'][0]['error'] == 'invalid_evidence_scope'
    fields = analyst['errors'][0]['rejected'][0]['fields']
    assert 'user_id' in fields
    assert 'permission_scope' in fields


def test_ecommerce_graph_case_blackboard_records_verified_root_cause_artifacts_and_hypotheses():
    result = run_ecommerce_graph(
        GRAPH_ROOT_CAUSE,
        _request(enable_case=True, case_id='case_gmv_health_1', dimensions=['channel'], rows=[
            {'channel': 'ads', 'current_gmv': 100, 'previous_gmv': 40},
            {'channel': 'organic', 'current_gmv': 80, 'previous_gmv': 120},
        ]),
        trace_id='trace-case-root-cause')

    assert result['status'] == 'ok'
    assert result['case_context']['case']['case_id'] == 'case_gmv_health_1'
    assert result['case_context']['case']['scenario'] == 'gmv_health'
    assert result['case_context']['evidence_records'][0]['evidence_id'] == 'ev_current'
    artifacts = result['case_context']['artifacts'].values()
    artifact_types = sorted([item['artifact_type'] for item in artifacts])
    assert artifact_types == ['contribution', 'signal']
    for artifact in artifacts:
        assert artifact['evidence_ids'] == ['ev_current']
    hypotheses = result['case_context']['hypotheses'].values()
    assert len(hypotheses) == 1
    hypothesis = list(hypotheses)[0]
    assert hypothesis['support_evidence_ids'] == ['ev_current']
    assert hypothesis['metadata']['claim_scope'] == 'contribution_not_causal'
    dynamic_tasks = result['dynamic_tasks']
    assert [task['task_type'] for task in dynamic_tasks] == [
        'verify_gmv_signal', 'decompose_gmv_drivers', 'challenge_root_cause']
    assert dynamic_tasks[0]['metadata']['case_id'] == 'case_gmv_health_1'


def test_case_graph_auto_completes_reexecution_when_existing_evidence_is_stale():
    bus = EvidenceBus()
    case_obj = build_gmv_health_case(
        metric='gmv', time_range='last_7_days', dimensions=['channel'],
        dataid='orders', data_version='v1')
    case_obj.case_id = 'case_auto_reexec'
    board = CaseBlackboard(case_obj, evidence_bus=bus)
    stale = build_execution_envelope(
        status='ok', stage='execute', query_id='q_stale', evidence_id='ev_stale',
        dataid='orders', data_version='v1', row_count=1, time_range='last_7_days',
        authority='verified_execution', metadata={'metric': 'gmv', 'dimensions': ['channel'], 'filters': {}})
    board.record_execution_envelope(
        stale, producer_task_id='old_data_analyst', expected_scope=gmv_health_expected_scope(case_obj),
        ttl_seconds=1, now=10.0)
    bus.records['ev_stale']['recorded_at'] = 10.0
    new_envelope = build_execution_envelope(
        status='ok', stage='execute', query_id='q_fresh', evidence_id='ev_fresh',
        dataid='orders', data_version='v1', row_count=1, time_range='last_7_days',
        authority='verified_execution', metadata={'metric': 'gmv', 'dimensions': ['channel'], 'filters': {}})

    result = run_ecommerce_graph(
        GRAPH_ROOT_CAUSE,
        _request(case_blackboard=board, case_id='case_auto_reexec', dimensions=['channel'],
                 rows=[{'channel': 'ads', 'current_gmv': 120, 'previous_gmv': 80}],
                 evidence_bus=bus, execution_envelope=new_envelope,
                 evidence_ttl_seconds=1, evidence_now=20.0),
        trace_id='trace-auto-reexec-new')

    assert result['status'] == 'ok'
    assert result['reexecution']['freshness']['needs_reexecution'] is True
    assert result['reexecution']['dispatch']['status'] == 'scheduled'
    assert result['reexecution']['completion']['status'] == 'completed'
    assert result['reexecution']['completion']['evidence_id'] == 'ev_fresh'
    context = result['case_context']
    assert [item['evidence_id'] for item in context['evidence_records']] == ['ev_fresh']
    assert validate_reexecution_replay(context)['valid'] is True
    assert any(event['event_type'] == 'reexecution.completed' for event in context['events'])


def test_case_blackboard_does_not_promote_cross_scope_evidence_to_artifacts():
    envelope = build_execution_envelope(
        status='ok', stage='execute', query_id='q_wrong_metric', evidence_id='ev_wrong_metric',
        dataid='orders', data_version='v1', row_count=1, time_range='last_7_days',
        authority='verified_execution', metadata={'metric': 'orders', 'dimensions': ['channel']})

    result = run_ecommerce_graph(
        GRAPH_ROOT_CAUSE,
        _request(enable_case=True, case_id='case_cross_scope', metric='gmv', dimensions=['channel'],
                 execution_envelope=envelope, rows=[{'channel': 'ads', 'orders': 10}]),
        trace_id='trace-case-cross-scope')

    assert result['status'] == 'partial'
    assert result['case_context']['case']['case_id'] == 'case_cross_scope'
    assert result['case_context']['evidence_records'] == []
    assert result['case_context']['artifacts'] == {}
    assert result['case_context']['hypotheses'] == {}
    assert result['dynamic_tasks'][0]['metadata']['case_id'] == 'case_cross_scope'
