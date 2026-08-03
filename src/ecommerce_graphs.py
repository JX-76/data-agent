# -*- coding: utf-8 -*-
"""Business graph builders for deterministic ecommerce multi-agent runtime."""
from __future__ import unicode_literals

from multi_agent_contracts import WORKER_DATA_ANALYST, WORKER_DIAGNOSIS, WORKER_AUDITOR
from supervisor_runtime import SupervisorRuntime
from worker_registry import build_default_worker_registry
from ecommerce_workers import register_ecommerce_workers
from evidence_bus import EvidenceBus
from case_blackboard import CaseBlackboard
from case_contracts import CaseArtifact, Hypothesis, ARTIFACT_SIGNAL, ARTIFACT_CONTRIBUTION
from gmv_health_playbook import build_gmv_health_case, build_gmv_health_dynamic_tasks, gmv_health_expected_scope
from evidence_freshness import assess_case_evidence_freshness
from reexecution_dispatcher import EvidenceReexecutionDispatcher


GRAPH_METRIC_QUERY = 'metric_query'
GRAPH_BREAKDOWN = 'breakdown'
GRAPH_COMPARISON = 'comparison'
GRAPH_ROOT_CAUSE = 'root_cause'
GRAPH_REPORT = 'report'


class EcommerceGraphSpec(object):
    def __init__(self, graph_type, tasks, required_slots=None, required_evidence=None,
                 output_contract='supervisor_runtime_result_v1'):
        self.graph_type = graph_type
        self.tasks = list(tasks or [])
        self.required_slots = list(required_slots or [])
        self.required_evidence = list(required_evidence or [])
        self.output_contract = output_contract

    def to_dict(self):
        return {
            'graph_type': self.graph_type,
            'tasks': list(self.tasks),
            'required_slots': list(self.required_slots),
            'required_evidence': list(self.required_evidence),
            'output_contract': self.output_contract,
        }


def _base_payload(request):
    request = dict(request or {})
    return {
        'metric': request.get('metric'),
        'time_range': request.get('time_range'),
        'dimensions': list(request.get('dimensions') or []),
        'filters': request.get('filters') or {},
        'rows': list(request.get('rows') or []),
        'execution_result': request.get('execution_result') or {},
        'execution_envelope': request.get('execution_envelope') or {},
        'query_id': request.get('query_id'),
        'evidence_id': request.get('evidence_id'),
        'dataid': request.get('dataid'),
        'data_version': request.get('data_version'),
        'simulate_error': request.get('simulate_error'),
        'execution_engine': request.get('execution_engine'),
        'execution_plan': request.get('execution_plan') or {},
        'evidence_bus': request.get('evidence_bus'),
        'evidence_ttl_seconds': request.get('evidence_ttl_seconds'),
        'evidence_now': request.get('evidence_now'),
        'access_context': request.get('access_context') or {},
        'tenant_id': request.get('tenant_id') or (request.get('access_context') or {}).get('tenant_id'),
        'user_id': request.get('user_id') or (request.get('access_context') or {}).get('user_id'),
        'permission_scope': request.get('permission_scope') or (request.get('access_context') or {}).get('permission_scope'),
        'graph_type': request.get('graph_type'),
        'compare_time_range': request.get('compare_time_range') or request.get('previous_time_range'),
        'case_id': request.get('case_id'),
        'business_case': request.get('business_case'),
        'case_blackboard': request.get('case_blackboard'),
        'case_context': request.get('case_context') or {},
    }


def _expected_scope(payload, allowed_time_ranges=None):
    allowed = [x for x in (allowed_time_ranges or [payload.get('time_range')]) if x is not None]
    return {
        'metric': payload.get('metric'),
        'time_range': payload.get('time_range'),
        'allowed_time_ranges': allowed,
        'dimensions': list(payload.get('dimensions') or []),
        'filters': payload.get('filters') or {},
        'dataid': payload.get('dataid'),
        'data_version': payload.get('data_version'),
        'tenant_id': payload.get('tenant_id'),
        'user_id': payload.get('user_id'),
        'permission_scope': payload.get('permission_scope'),
    }


def build_metric_query_graph(request):
    payload = _base_payload(request)
    tasks = [
        {
            'task_id': 'data_analyst',
            'worker_type': WORKER_DATA_ANALYST,
            'intent': 'execute_metric_query',
            'input': payload,
            'idempotency_key': 'metric_query:data_analyst',
        },
        {
            'task_id': 'auditor',
            'worker_type': WORKER_AUDITOR,
            'intent': 'audit_metric_query',
            'input': {'required_evidence': True, 'expected_scope': _expected_scope(payload)},
            'dependencies': ['data_analyst'],
            'idempotency_key': 'metric_query:auditor',
        },
    ]
    return EcommerceGraphSpec(
        GRAPH_METRIC_QUERY, tasks,
        required_slots=['metric', 'time_range'],
        required_evidence=['verified_execution'],
    )


def build_breakdown_graph(request):
    payload = _base_payload(request)
    tasks = [
        {
            'task_id': 'data_analyst',
            'worker_type': WORKER_DATA_ANALYST,
            'intent': 'execute_breakdown_query',
            'input': payload,
            'idempotency_key': 'breakdown:data_analyst',
        },
        {
            'task_id': 'diagnosis',
            'worker_type': WORKER_DIAGNOSIS,
            'intent': 'derive_breakdown_findings',
            'input': {'dimensions': payload.get('dimensions') or []},
            'dependencies': ['data_analyst'],
            'idempotency_key': 'breakdown:diagnosis',
        },
        {
            'task_id': 'auditor',
            'worker_type': WORKER_AUDITOR,
            'intent': 'audit_breakdown',
            'input': {'required_evidence': True, 'expected_scope': _expected_scope(payload)},
            'dependencies': ['data_analyst', 'diagnosis'],
            'idempotency_key': 'breakdown:auditor',
        },
    ]
    return EcommerceGraphSpec(
        GRAPH_BREAKDOWN, tasks,
        required_slots=['metric', 'time_range', 'dimensions'],
        required_evidence=['verified_execution'],
    )


def build_comparison_graph(request):
    request = dict(request or {})
    current = _base_payload(request)
    previous_request = dict(request)
    previous_request['time_range'] = request.get('compare_time_range') or request.get('previous_time_range')
    previous_request['rows'] = request.get('previous_rows') or []
    previous_request['execution_result'] = request.get('previous_execution_result') or {}
    previous_request['execution_envelope'] = request.get('previous_execution_envelope') or {}
    previous_request['execution_plan'] = request.get('previous_execution_plan') or {}
    previous_request['query_id'] = request.get('previous_query_id') or 'q_previous'
    previous_request['evidence_id'] = request.get('previous_evidence_id') or 'ev_previous'
    previous = _base_payload(previous_request)
    tasks = [
        {
            'task_id': 'current_period',
            'worker_type': WORKER_DATA_ANALYST,
            'intent': 'execute_current_period_query',
            'input': current,
            'idempotency_key': 'comparison:current_period',
        },
        {
            'task_id': 'previous_period',
            'worker_type': WORKER_DATA_ANALYST,
            'intent': 'execute_previous_period_query',
            'input': previous,
            'idempotency_key': 'comparison:previous_period',
        },
        {
            'task_id': 'auditor',
            'worker_type': WORKER_AUDITOR,
            'intent': 'audit_comparison',
            'input': {'required_evidence': True, 'expected_scope': _expected_scope(
                current, allowed_time_ranges=[current.get('time_range'), previous.get('time_range')])},
            'dependencies': ['current_period', 'previous_period'],
            'idempotency_key': 'comparison:auditor',
        },
    ]
    return EcommerceGraphSpec(
        GRAPH_COMPARISON, tasks,
        required_slots=['metric', 'time_range', 'compare_time_range'],
        required_evidence=['verified_execution:current', 'verified_execution:previous'],
    )


def build_root_cause_graph(request):
    """Build a bounded root-cause candidate graph.

    The graph deliberately performs contribution-style diagnosis only.  It does
    not claim causal proof; the Auditor still gates every finding on verified
    execution evidence.
    """
    request = dict(request or {})
    payload = _base_payload(request)
    if payload.get('execution_engine') is not None and not payload.get('execution_plan'):
        payload['execution_plan'] = {
            'task_type': 'comparison',
            'model': request.get('model') or 'order_detail',
            'metric': payload.get('metric'),
            'dimensions': payload.get('dimensions') or [],
            'time_range': payload.get('time_range'),
             'previous_time_range': payload.get('compare_time_range'),
             'filters': payload.get('filters') if isinstance(payload.get('filters'), list) else [],
             'dataid': payload.get('dataid'),
             'data_version': payload.get('data_version'),
             'analysis_config': {'claim_scope': 'contribution_not_causal'},
         }
    tasks = [
        {
            'task_id': 'data_analyst',
            'worker_type': WORKER_DATA_ANALYST,
            'intent': 'execute_root_cause_candidate_query',
            'input': payload,
            'idempotency_key': 'root_cause:data_analyst',
        },
        {
            'task_id': 'diagnosis',
            'worker_type': WORKER_DIAGNOSIS,
            'intent': 'derive_root_cause_candidates',
            'input': {'dimensions': payload.get('dimensions') or [], 'graph_type': GRAPH_ROOT_CAUSE},
            'dependencies': ['data_analyst'],
            'idempotency_key': 'root_cause:diagnosis',
        },
        {
            'task_id': 'auditor',
            'worker_type': WORKER_AUDITOR,
            'intent': 'audit_root_cause_candidates',
            'input': {'required_evidence': True, 'claim_scope': 'contribution_not_causal',
                      'expected_scope': _expected_scope(payload)},
            'dependencies': ['data_analyst', 'diagnosis'],
            'idempotency_key': 'root_cause:auditor',
        },
    ]
    return EcommerceGraphSpec(
        GRAPH_ROOT_CAUSE, tasks,
        required_slots=['metric', 'time_range', 'dimensions'],
        required_evidence=['verified_execution', 'claim_scope:contribution_not_causal'],
    )


def build_report_graph(request):
    """Build an evidence-only report graph from current verified execution."""
    payload = _base_payload(request)
    tasks = [
        {
            'task_id': 'data_analyst',
            'worker_type': WORKER_DATA_ANALYST,
            'intent': 'load_report_evidence',
            'input': payload,
            'idempotency_key': 'report:data_analyst',
        },
        {
            'task_id': 'diagnosis',
            'worker_type': WORKER_DIAGNOSIS,
            'intent': 'compile_evidence_report_findings',
            'input': {'dimensions': payload.get('dimensions') or [], 'graph_type': GRAPH_REPORT},
            'dependencies': ['data_analyst'],
            'idempotency_key': 'report:diagnosis',
        },
        {
            'task_id': 'auditor',
            'worker_type': WORKER_AUDITOR,
            'intent': 'audit_evidence_report',
            'input': {'required_evidence': True, 'claim_scope': 'verified_report_only',
                      'expected_scope': _expected_scope(payload)},
            'dependencies': ['data_analyst', 'diagnosis'],
            'idempotency_key': 'report:auditor',
        },
    ]
    return EcommerceGraphSpec(
        GRAPH_REPORT, tasks,
        required_slots=['metric', 'time_range'],
        required_evidence=['verified_execution', 'claim_scope:verified_report_only'],
    )


def build_ecommerce_graph(graph_type, request):
    if graph_type == GRAPH_METRIC_QUERY:
        return build_metric_query_graph(request)
    if graph_type == GRAPH_BREAKDOWN:
        return build_breakdown_graph(request)
    if graph_type == GRAPH_COMPARISON:
        return build_comparison_graph(request)
    if graph_type == GRAPH_ROOT_CAUSE:
        return build_root_cause_graph(request)
    if graph_type == GRAPH_REPORT:
        return build_report_graph(request)
    raise ValueError('unsupported ecommerce graph_type: %s' % graph_type)


def build_ecommerce_worker_registry():
    registry = build_default_worker_registry()
    return register_ecommerce_workers(registry)


def _slot_present(value):
    if value is None:
        return False
    if value == '':
        return False
    if isinstance(value, (list, tuple, dict)) and len(value) == 0:
        return False
    return True


def _missing_required_slots(graph, request):
    request = dict(request or {})
    missing = []
    for slot in graph.required_slots:
        aliases = [slot]
        if slot == 'compare_time_range':
            aliases = ['compare_time_range', 'previous_time_range']
        found = False
        for alias in aliases:
            if _slot_present(request.get(alias)):
                found = True
                break
        if not found:
            missing.append(slot)
    return missing


def _blocked_graph_result(graph, trace_id, session_id, missing_slots):
    node_states = dict((task.get('task_id'), 'skipped') for task in graph.tasks)
    return {
        'status': 'blocked',
        'results': {},
        'trace_events': [],
        'node_states': node_states,
        'errors': [{'error': 'missing_required_graph_slots', 'missing_slots': list(missing_slots or [])}],
        'metrics': {'node_count': len(graph.tasks), 'trace_complete': True},
        'final_output': {},
        'trace_id': trace_id,
        'session_id': session_id,
        'blocked_reason': 'missing_required_graph_slots',
        'missing_slots': list(missing_slots or []),
        'graph': {
            'graph_type': graph.graph_type,
            'required_slots': graph.required_slots,
            'required_evidence': graph.required_evidence,
            'output_contract': graph.output_contract,
        },
    }


def run_ecommerce_graph(graph_type, request, trace_id=None, session_id=None,
                        runtime=None, worker_registry=None):
    request = dict(request or {})
    request.setdefault('evidence_bus', EvidenceBus())
    request.setdefault('graph_type', graph_type)
    graph = build_ecommerce_graph(graph_type, request)
    missing_slots = _missing_required_slots(graph, request)
    if missing_slots:
        return _blocked_graph_result(graph, trace_id, session_id, missing_slots)
    case_blackboard = _prepare_case_blackboard(graph_type, request)
    reexecution = _schedule_case_evidence_reexecution(case_blackboard, request)
    for task in graph.tasks:
        task.setdefault('input', {})
        task['input'].setdefault('evidence_bus', request.get('evidence_bus'))
        task['input'].setdefault('graph_type', graph_type)
        if case_blackboard is not None:
            task['input'].setdefault('case_id', case_blackboard.case.case_id)
            task['input'].setdefault('case_context', case_blackboard.get_case_context())
    if runtime is None:
        registry = worker_registry or build_ecommerce_worker_registry()
        runtime = SupervisorRuntime(worker_registry=registry, max_steps=20, retry_limit=0)
    result = runtime.run(graph.tasks, trace_id=trace_id, session_id=session_id)
    if case_blackboard is not None:
        _update_case_blackboard_from_result(case_blackboard, graph_type, result, request, trace_id)
    reexecution = _complete_case_evidence_reexecution(case_blackboard, result, reexecution, request)
    if reexecution is not None:
        result['reexecution'] = reexecution
    result['graph'] = {
        'graph_type': graph.graph_type,
        'required_slots': graph.required_slots,
        'required_evidence': graph.required_evidence,
        'output_contract': graph.output_contract,
    }
    if case_blackboard is not None:
        result['case_context'] = case_blackboard.get_case_context()
        result['dynamic_tasks'] = [task.to_dict() for task in build_gmv_health_dynamic_tasks(case_blackboard.case)]
    return result


def _schedule_case_evidence_reexecution(board, request):
    """Schedule a refresh before a case graph consumes historical evidence.

    This is intentionally only enabled for the case-blackboard graph path.  The
    normal graph data-analyst task remains the actual executor; this helper
    supplies the missing control-plane lifecycle so stale/scope-mismatched case
    evidence cannot be silently reused without an auditable re-verification.
    """
    if board is None:
        return None
    request = dict(request or {})
    expected_scope = gmv_health_expected_scope(board.case)
    ttl = request.get('evidence_ttl_seconds')
    now = request.get('evidence_now')
    freshness = assess_case_evidence_freshness(
        board, expected_scope=expected_scope, ttl_seconds=ttl, now=now)
    dispatcher = EvidenceReexecutionDispatcher()
    dispatch = dispatcher.dispatch(
        board, freshness.get('reexecution_plan') or {},
        tenant_id=request.get('tenant_id') or (request.get('access_context') or {}).get('tenant_id'),
        session_id=request.get('session_id') or board.case.case_id)
    return {'freshness': freshness, 'dispatch': dispatch, 'dispatcher': dispatcher}


def _first_execution_envelope(result):
    """Return a graph execution envelope, including a failed one for closure."""
    results = (result or {}).get('results') or {}
    preferred = ['data_analyst', 'current_period', 'previous_period']
    task_ids = preferred + [key for key in results if key not in preferred]
    for task_id in task_ids:
        output = ((results.get(task_id) or {}).get('output') or {})
        envelope = output.get('execution_envelope')
        if envelope:
            return envelope
    return {}


def _complete_case_evidence_reexecution(board, result, reexecution, request):
    if board is None or not reexecution:
        return reexecution
    dispatch = reexecution.get('dispatch') or {}
    dispatcher = reexecution.pop('dispatcher', None)
    # Dispatcher instances are operational implementation details, never an API
    # payload.  Even a no-op freshness assessment must remain JSON/replay safe.
    if dispatch.get('status') not in ('scheduled', 'duplicate') or dispatcher is None:
        return reexecution
    envelope = _first_execution_envelope(result)
    reexecution['completion'] = dispatcher.complete(
        board, dispatch, envelope, now=(request or {}).get('evidence_now'))
    return reexecution


def _prepare_case_blackboard(graph_type, request):
    request = dict(request or {})
    if request.get('case_blackboard') is not None:
        return request.get('case_blackboard')
    business_case = request.get('business_case')
    enable_case = request.get('enable_case') or business_case is not None or request.get('case_id') is not None
    if not enable_case:
        return None
    if business_case is None:
        business_case = build_gmv_health_case(
            metric=request.get('metric') or 'gmv',
            time_range=request.get('time_range'),
            dimensions=request.get('dimensions') or [],
            filters=request.get('filters') or {},
            dataid=request.get('dataid'),
            data_version=request.get('data_version'),
            tenant_id=request.get('tenant_id') or (request.get('access_context') or {}).get('tenant_id'),
            user_id=request.get('user_id') or (request.get('access_context') or {}).get('user_id'),
            permission_scope=request.get('permission_scope') or (request.get('access_context') or {}).get('permission_scope'),
        )
        if request.get('case_id'):
            business_case.case_id = request.get('case_id')
    return CaseBlackboard(business_case, evidence_bus=request.get('evidence_bus'))


def _update_case_blackboard_from_result(board, graph_type, result, request, trace_id=None):
    expected_scope = gmv_health_expected_scope(board.case)
    ttl = request.get('evidence_ttl_seconds')
    now = request.get('evidence_now')
    results = result.get('results') or {}
    for task_id, node in results.items():
        output = node.get('output') or {}
        envelope = output.get('execution_envelope') or {}
        if envelope:
            board.record_execution_envelope(envelope, producer_task_id=task_id, trace_id=trace_id,
                                            graph_type=graph_type, expected_scope=expected_scope,
                                            ttl_seconds=ttl, now=now)
    # Workers may write verified envelopes directly into the shared EvidenceBus.
    # Prune the bus before creating case artifacts so cross-scope/expired facts
    # cannot leak into case_context or become hypothesis/action support.
    board.prune_invalid_evidence(expected_scope=expected_scope, ttl_seconds=ttl, now=now)
    analyst = (results.get('data_analyst') or results.get('current_period') or {}).get('output') or {}
    evidence_ids = analyst.get('evidence_ids') or analyst.get('evidence_refs') or []
    if evidence_ids:
        board.add_artifact(CaseArtifact(
            board.case.case_id, ARTIFACT_SIGNAL,
            payload={'graph_type': graph_type, 'summary': 'verified execution signal recorded'},
            evidence_ids=evidence_ids, produced_by='data_analyst'),
            expected_scope=expected_scope, ttl_seconds=ttl, now=now)
    diagnosis = (results.get('diagnosis') or {}).get('output') or {}
    for finding in diagnosis.get('findings') or []:
        ids = finding.get('evidence_ids') or diagnosis.get('evidence_ids') or []
        if not ids:
            continue
        board.add_artifact(CaseArtifact(
            board.case.case_id, ARTIFACT_CONTRIBUTION,
            payload={'graph_type': graph_type, 'finding': finding},
            evidence_ids=ids, produced_by='diagnosis'),
            expected_scope=expected_scope, ttl_seconds=ttl, now=now)
        if finding.get('kind') == 'contribution_candidate_not_causal':
            board.propose_hypothesis(Hypothesis(
                board.case.case_id, finding.get('text') or 'contribution candidate requires validation',
                support_evidence_ids=ids, confidence=0.4,
                metadata={'claim_scope': finding.get('claim_scope'), 'top_candidate': finding.get('top_candidate')}),
                expected_scope=expected_scope, ttl_seconds=ttl, now=now)
    return board


__all__ = [
    'GRAPH_METRIC_QUERY', 'GRAPH_BREAKDOWN', 'GRAPH_COMPARISON',
    'GRAPH_ROOT_CAUSE', 'GRAPH_REPORT',
    'EcommerceGraphSpec', '_expected_scope', '_missing_required_slots',
    'build_metric_query_graph', 'build_breakdown_graph',
    'build_comparison_graph', 'build_root_cause_graph', 'build_report_graph',
    'build_ecommerce_graph', 'build_ecommerce_worker_registry',
    'run_ecommerce_graph',
]
