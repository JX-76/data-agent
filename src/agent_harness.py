# -*- coding: utf-8 -*-
"""Agent Harness — deterministic evaluation and replay for Data Agent.

This module keeps the harness layer lightweight, Python 2.7 compatible, and
case-driven. It validates agent responses, trace coverage, and failure
classification without depending on any external service.
"""

from __future__ import unicode_literals

import codecs
import json
import os
import time
import traceback

from agent_facade import AgentFacade
from contracts import validate_response_contract
from eval_baseline import EvalCase
from trace_completeness import aggregate_dag_trace, validate_trace_completeness
from trace_contracts import build_replay_package, build_trace_envelope, validate_trace_envelope
try:
    from benchmark_scorer import classify_failure
except Exception:  # pragma: no cover
    def classify_failure(failure_type):
        return failure_type


def _ensure_dir(path):
    if path and not os.path.isdir(path):
        os.makedirs(path)


def _now_ms():
    return int(time.time() * 1000)


def _is_jsonl(path):
    return path.lower().endswith('.jsonl')


def _as_dict(value):
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, 'to_dict'):
        try:
            return dict(value.to_dict())
        except Exception:
            return {}
    try:
        return dict(value)
    except Exception:
        return {}


def _event_names(trace):
    names = []
    for item in trace or []:
        if isinstance(item, dict):
            name = item.get('name') or item.get('stage')
            if name:
                names.append(name)
    return names


def _normalize_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _to_text(value):
    if value is None:
        return value
    try:
        if isinstance(value, bytes):
            return value.decode('utf-8', 'ignore')
    except Exception:
        pass
    try:
        unicode_type = unicode
    except NameError:
        unicode_type = str
    if isinstance(value, unicode_type):
        return value
    try:
        return unicode_type(value)
    except Exception:
        return '%s' % value


def _sanitize(obj):
    if isinstance(obj, dict):
        clean = {}
        for key, value in obj.items():
            clean[_to_text(key)] = _sanitize(value)
        return clean
    if isinstance(obj, list):
        return [_sanitize(item) for item in obj]
    if isinstance(obj, tuple):
        return [_sanitize(item) for item in obj]
    if isinstance(obj, bytes):
        return _to_text(obj)
    return obj


def _answer_report_projection(result):
    """Return the safe, evaluation-facing subset of an AnswerEnvelope.

    Raw results remain attached to an in-memory run for replay compatibility;
    callers that persist/share reports must use this projection instead.
    """
    result = _as_dict(result)
    envelope = _as_dict(result.get('answer_envelope'))
    if not envelope:
        try:
            from answer_contracts import build_answer_envelope
            envelope = build_answer_envelope(result, query=result.get('query'))
        except Exception:
            envelope = {'contract': 'answer_envelope_v1', 'status': result.get('status')}
    return _sanitize({
        'answer_envelope': envelope,
        'tool_summary': envelope.get('tool_summary') or {},
        'evidence_summary': envelope.get('evidence_refs') or [],
        'memory_refs': envelope.get('memory_refs') or [],
        'rag_refs': envelope.get('rag_refs') or [],
        'claim_audit': envelope.get('claim_audit') or result.get('claim_audit') or {},
        'quality_score': envelope.get('quality') or result.get('quality') or {},
        'hallucination_findings': envelope.get('hallucination_findings') or [],
    })


def summarize_trace_quality(results):
    """Aggregate additive DAG trace completeness metrics for harness reports."""
    results = results or []
    evaluated_count = 0
    complete_count = 0
    missing_node_breakdown = {}
    first_failure_breakdown = {}
    skipped_count = 0

    for item in results:
        item = _as_dict(item)
        completeness = _as_dict(item.get('dag_trace_completeness'))
        if not completeness:
            skipped_count += 1
            continue
        observed = completeness.get('observed_nodes') or []
        if not observed:
            skipped_count += 1
            continue
        evaluated_count += 1
        if completeness.get('complete') is True:
            complete_count += 1
        for node in completeness.get('missing_nodes') or []:
            missing_node_breakdown[node] = missing_node_breakdown.get(node, 0) + 1
        summary = _as_dict(completeness.get('summary'))
        first_failure = summary.get('first_failure')
        if first_failure:
            first_failure_breakdown[first_failure] = first_failure_breakdown.get(first_failure, 0) + 1

    return {
        'contract': 'trace_quality_summary_v1',
        'evaluated_count': evaluated_count,
        'complete_count': complete_count,
        'incomplete_count': evaluated_count - complete_count,
        'skipped_count': skipped_count,
        'complete_rate': round(complete_count / float(max(1, evaluated_count)), 4),
        'missing_node_breakdown': missing_node_breakdown,
        'first_failure_breakdown': first_failure_breakdown,
    }


def _normalize_result(result):
    result = _as_dict(result)
    plan = _as_dict(result.get('plan'))
    analysis = _as_dict(result.get('analysis'))
    if not result.get('task_type'):
        result['task_type'] = plan.get('task_type') or analysis.get('type')
    if not result.get('intent'):
        result['intent'] = plan.get('intent')
    if result.get('metric') is None:
        result['metric'] = plan.get('metric')
    if result.get('dimensions') in (None, []):
        dims = plan.get('dimensions')
        if dims is None and isinstance(analysis.get('definition'), dict):
            dims = analysis.get('definition', {}).get('dimensions')
        if dims is not None:
            result['dimensions'] = dims
    if result.get('status') in (None, ''):
        result['status'] = plan.get('status') or analysis.get('status')
    return result


class AgentHarness(object):
    """Unified harness for running and evaluating agent cases."""

    def __init__(self, facade_factory=None, observer_factory=None, reports_dir=None):
        self.facade_factory = facade_factory
        self.observer_factory = observer_factory
        if reports_dir:
            self.reports_dir = reports_dir
        else:
            _script_dir = os.path.dirname(os.path.abspath(__file__))
            self.reports_dir = os.path.abspath(os.path.join(_script_dir, os.pardir, 'harness', 'reports'))

        _ensure_dir(self.reports_dir)

    def _make_facade(self, case=None):
        if self.facade_factory is not None:
            facade = self.facade_factory()
        else:
            case_id = None
            if isinstance(case, dict):
                case_id = case.get('id') or case.get('case_id')
            facade = AgentFacade(session_id=case_id or 'harness-session')
        if self.observer_factory is not None and hasattr(facade, 'observer'):
            facade.observer = self.observer_factory()
        return facade

    def load_cases(self, path):
        """Load benchmark cases from JSON or JSONL.

        JSON files may be either a list of cases or a dict with a "cases" key.
        """
        if not os.path.isabs(path):
            path = os.path.abspath(path)
        if not os.path.exists(path):
            raise IOError('Case file not found: %s' % path)

        cases = []
        if _is_jsonl(path):
            with codecs.open(path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    cases.append(_as_dict(json.loads(line)))
            return cases

        with codecs.open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, dict) and 'cases' in data:
            data = data['cases']
        if not isinstance(data, list):
            raise ValueError('Unsupported case format in %s' % path)
        for item in data:
            cases.append(_as_dict(item))
        return cases

    def run_case(self, case):
        """Run a single case through AgentFacade.ask() or an external tool and evaluate it."""
        case = _as_dict(case)
        if case.get('type') == 'external_tool' or case.get('tool_id'):
            return self.run_external_tool_case(case)
        if case.get('type') == 'mcp_adapter':
            return self.run_mcp_adapter_case(case)
        eval_case = EvalCase.from_dict(case)
        facade = self._make_facade(case)
        started = _now_ms()
        trace = []
        trace_summary = {}
        result = {}
        exc_text = None
        try:
            result = facade.ask(eval_case.query)
            result = _normalize_result(result)
            trace = facade.get_trace(result.get('trace_id')) if hasattr(facade, 'get_trace') else []
            observer = getattr(facade, 'observer', None)
            if observer is not None and hasattr(observer, 'summarize'):
                trace_summary = observer.summarize(result.get('trace_id'))
        except Exception:
            exc_text = traceback.format_exc()
            result = {
                'status': 'error',
                'query': eval_case.query,
                'errors': [exc_text],
                'diagnostics': {'exception': exc_text},
            }
        finished = _now_ms()
        evaluated = self.evaluate_case(case, result, trace=trace, trace_summary=trace_summary, exception_text=exc_text)
        evaluated['duration_ms'] = finished - started
        evaluated['case'] = case
        evaluated['result'] = result
        evaluated.update(_answer_report_projection(result))
        evaluated['trace'] = trace
        evaluated['trace_summary'] = trace_summary
        evaluated['trace_envelope'] = build_trace_envelope(trace, result=result, case=case)
        evaluated['trace_validation'] = validate_trace_envelope(evaluated['trace_envelope'])
        evaluated['replay_package'] = build_replay_package(case, evaluated)
        evaluated['dag_trace'] = aggregate_dag_trace(trace)
        evaluated['dag_trace_completeness'] = validate_trace_completeness(result.get('status'), trace)
        if not evaluated['trace_validation'].get('valid'):
            evaluated.setdefault('errors', [])
            for error in evaluated['trace_validation'].get('errors') or []:
                evaluated['errors'].append('trace:%s' % error)
            evaluated['passed'] = False
            evaluated['failure_type'] = evaluated.get('failure_type') or 'trace_contract_violation'
        return evaluated

    def run_mcp_adapter_case(self, case):
        """Execute an MCP-style request while retaining external-tool evaluation."""
        case = _as_dict(case)
        started = _now_ms()
        request = _as_dict(case.get('request'))
        try:
            from mcp_adapter import McpAdapter
            response = McpAdapter().handle_request(request)
            if response.get('error'):
                result = {
                    'status': 'error', 'tool_id': None, 'data': {},
                    'diagnostics': {'failure_type': 'mcp_protocol_error', 'error': response.get('error', {}).get('message')},
                    'trace_event': {},
                }
            else:
                result = _as_dict(response.get('result'))
            trace = [result.get('trace_event')] if result.get('trace_event') else []
            evaluated = self.evaluate_external_tool_case(case, result, trace=trace)
            evaluated['case_type'] = 'external_tool'
            evaluated['mcp_response'] = response
        except Exception:
            exc_text = traceback.format_exc()
            result = {'status': 'error', 'data': {}, 'diagnostics': {'exception': exc_text, 'failure_type': 'unexpected_exception'}}
            evaluated = self.evaluate_external_tool_case(case, result, trace=[], exception_text=exc_text)
            evaluated['case_type'] = 'external_tool'
        evaluated['duration_ms'] = _now_ms() - started
        evaluated['case'] = case
        evaluated['result'] = result
        evaluated.update(_answer_report_projection(result))
        trace = [result.get('trace_event')] if result.get('trace_event') else []
        evaluated['trace_envelope'] = build_trace_envelope(trace, result=result, case=case)
        evaluated['trace_validation'] = validate_trace_envelope(evaluated['trace_envelope'])
        evaluated['replay_package'] = build_replay_package(case, evaluated)
        return evaluated

    def run_external_tool_case(self, case):
        case = _as_dict(case)
        started = _now_ms()
        try:
            from external_tool_executor import ExternalToolExecutor
            executor = ExternalToolExecutor()
            result = executor.call(case.get('tool_id'), case.get('args') or {}, case.get('context') or {})
            trace = []
            if result.get('trace_event'):
                trace = [result.get('trace_event')]
            evaluated = self.evaluate_external_tool_case(case, result, trace=trace)
        except Exception:
            exc_text = traceback.format_exc()
            result = {'status': 'error', 'tool_id': case.get('tool_id'), 'data': {}, 'diagnostics': {'exception': exc_text, 'failure_type': 'unexpected_exception'}}
            evaluated = self.evaluate_external_tool_case(case, result, trace=[], exception_text=exc_text)
        evaluated['duration_ms'] = _now_ms() - started
        evaluated['case'] = case
        evaluated['result'] = result
        evaluated.update(_answer_report_projection(result))
        trace = [result.get('trace_event')] if result.get('trace_event') else []
        evaluated['trace_envelope'] = build_trace_envelope(trace, result=result, case=case)
        evaluated['trace_validation'] = validate_trace_envelope(evaluated['trace_envelope'])
        evaluated['replay_package'] = build_replay_package(case, evaluated)
        return evaluated

    def run_cases(self, cases):
        results = []
        for case in cases or []:
            results.append(self.run_case(case))
        return results

    def evaluate_external_tool_case(self, case, result, trace=None, exception_text=None):
        case = _as_dict(case)
        result = _as_dict(result)
        expected = _as_dict(case.get('expected'))
        trace = trace or []
        errors = []
        failure_type = None
        data = _as_dict(result.get('data'))
        diagnostics = _as_dict(result.get('diagnostics'))
        trace_event = _as_dict(result.get('trace_event'))

        if exception_text:
            errors.append('unexpected_exception')
            failure_type = 'unexpected_exception'

        if expected.get('status') is not None and result.get('status') != expected.get('status'):
            errors.append('status expected=%s got=%s' % (expected.get('status'), result.get('status')))
            if failure_type is None:
                failure_type = 'status_mismatch'

        if expected.get('failure_type') is not None and diagnostics.get('failure_type') != expected.get('failure_type'):
            errors.append('failure_type expected=%s got=%s' % (expected.get('failure_type'), diagnostics.get('failure_type')))
            if failure_type is None:
                failure_type = 'external_tool_failure_type_mismatch'

        for key in expected.get('output_keys') or []:
            if key not in data:
                errors.append('missing_output_key: %s' % key)
                if failure_type is None:
                    failure_type = 'external_tool_output_contract_error'

        expected_trace = expected.get('trace_event')
        if expected_trace and trace_event.get('name') != expected_trace:
            errors.append('trace_event expected=%s got=%s' % (expected_trace, trace_event.get('name')))
            if failure_type is None:
                failure_type = 'external_tool_trace_missing'

        expected_trace_events = expected.get('trace_events') or []
        if expected_trace_events:
            names = _event_names(trace)
            missing = [name for name in expected_trace_events if name not in names]
            if missing:
                errors.append('trace_missing_event: %s' % ','.join(missing))
                if failure_type is None:
                    failure_type = 'external_tool_trace_missing'

        if expected.get('risk_level') is not None and trace_event.get('risk_level') != expected.get('risk_level'):
            errors.append('risk_level expected=%s got=%s' % (expected.get('risk_level'), trace_event.get('risk_level')))
            if failure_type is None:
                failure_type = 'risk_mismatch'

        if expected.get('side_effect') is not None and trace_event.get('side_effect') != expected.get('side_effect'):
            errors.append('side_effect expected=%s got=%s' % (expected.get('side_effect'), trace_event.get('side_effect')))
            if failure_type is None:
                failure_type = 'external_tool_policy_mismatch'

        if not errors:
            failure_type = None
        else:
            failure_type = classify_failure(failure_type)

        return {
            'id': case.get('id') or 'external_tool_case',
            'query': case.get('query', ''),
            'category': case.get('category', 'external_tool'),
            'case_type': 'external_tool',
            'expected': expected,
            'passed': len(errors) == 0,
            'failure_type': failure_type,
            'errors': errors,
            'result': result,
            'trace': trace,
            'trace_summary': {},
        }

    def evaluate_case(self, case, result, trace=None, trace_summary=None, exception_text=None):
        """Check response contract, routing fields, and trace coverage."""
        case = _as_dict(case)
        result = _as_dict(result)
        expected = _as_dict(case.get('expected'))
        trace = trace or []
        trace_summary = trace_summary or {}

        errors = []
        failure_type = None

        # Contract validation is the first gate.
        contract_ok, missing = validate_response_contract(result)
        if not contract_ok:
            errors.append('contract_missing_key: %s' % ','.join(missing))
            if failure_type is None:
                failure_type = 'contract_missing_key'

        # Exception / execution gating.
        if exception_text:
            errors.append('unexpected_exception')
            if failure_type is None:
                failure_type = 'unexpected_exception'
        elif result.get('status') == 'error' or result.get('errors'):
            errors.append('execution_error')
            if failure_type is None:
                failure_type = 'execution_error'

        # Route / status / semantic contract checks.
        expected_status = expected.get('status')
        if expected_status is not None and result.get('status') != expected_status:
            errors.append('status expected=%s got=%s' % (expected_status, result.get('status')))
            if failure_type is None:
                failure_type = 'status_mismatch'

        expected_intent = expected.get('intent')
        if expected_intent is not None and result.get('intent') != expected_intent:
            errors.append('intent expected=%s got=%s' % (expected_intent, result.get('intent')))
            if failure_type is None:
                failure_type = 'intent_mismatch'

        expected_task_type = expected.get('task_type')
        if expected_task_type is not None and result.get('task_type') != expected_task_type:
            errors.append('task_type expected=%s got=%s' % (expected_task_type, result.get('task_type')))
            if failure_type is None:
                failure_type = 'task_type_mismatch'

        expected_metric = expected.get('metric')
        if expected_metric is not None and result.get('metric') != expected_metric:
            errors.append('metric expected=%s got=%s' % (expected_metric, result.get('metric')))
            if failure_type is None:
                failure_type = 'metric_mismatch'

        expected_dimensions = expected.get('dimensions')
        if expected_dimensions is not None:
            got_dimensions = result.get('dimensions') or []
            # Use set comparison for dimension order independence
            if set(got_dimensions) != set(expected_dimensions):
                errors.append('dimensions expected=%s got=%s' % (expected_dimensions, got_dimensions))
                if failure_type is None:
                    failure_type = 'dimension_mismatch'

        expected_risk_level = expected.get('risk_level')
        if expected_risk_level is not None and result.get('risk_level') != expected_risk_level:
            errors.append('risk_level expected=%s got=%s' % (expected_risk_level, result.get('risk_level')))
            if failure_type is None:
                failure_type = 'risk_mismatch'

        expected_requires_human_review = expected.get('requires_human_review')
        if expected_requires_human_review is not None and bool(result.get('requires_human_review')) != bool(expected_requires_human_review):
            errors.append('requires_human_review expected=%s got=%s' % (expected_requires_human_review, result.get('requires_human_review')))
            if failure_type is None:
                failure_type = 'human_review_mismatch'

        expected_approval_status = expected.get('approval_status')
        if expected_approval_status is not None and result.get('approval_status') != expected_approval_status:
            errors.append('approval_status expected=%s got=%s' % (expected_approval_status, result.get('approval_status')))
            if failure_type is None:
                failure_type = 'approval_mismatch'

        # Optional route field if a case wants to distinguish route from intent.
        expected_route = expected.get('route')
        if expected_route is not None and result.get('intent') != expected_route:
            errors.append('route expected=%s got=%s' % (expected_route, result.get('intent')))
            if failure_type is None:
                failure_type = 'route_mismatch'

        # Contract key subset check.
        expected_contract_keys = expected.get('contract_keys') or []
        if expected_contract_keys:
            missing_keys = []
            for key in expected_contract_keys:
                if key not in result:
                    missing_keys.append(key)
            if missing_keys:
                errors.append('contract_missing_key: %s' % ','.join(missing_keys))
                if failure_type is None:
                    failure_type = 'contract_missing_key'

        # Trace events are checked as presence-based gates.
        expected_trace_events = expected.get('trace_events') or []
        if expected_trace_events:
            names = _event_names(trace)
            missing_events = []
            for name in expected_trace_events:
                if name not in names:
                    missing_events.append(name)
            if missing_events:
                errors.append('trace_missing_event: %s' % ','.join(missing_events))
                if failure_type is None:
                    failure_type = 'trace_missing_event'

        expected_prompt_chain = expected.get('prompt_chain')
        if expected_prompt_chain is not None and list(result.get('prompt_chain') or []) != list(expected_prompt_chain):
            errors.append('prompt_chain expected=%s got=%s' % (expected_prompt_chain, result.get('prompt_chain')))
            if failure_type is None:
                failure_type = 'prompt_chain_mismatch'

        expected_sandbox = expected.get('sandbox')
        if expected_sandbox is not None:
            got_sandbox = result.get('sandbox') or {}
            sandbox_mismatch = []
            if isinstance(expected_sandbox, dict):
                for key, value in expected_sandbox.items():
                    if got_sandbox.get(key) != value:
                        sandbox_mismatch.append('%s expected=%s got=%s' % (key, value, got_sandbox.get(key)))
            elif got_sandbox != expected_sandbox:
                sandbox_mismatch.append('sandbox expected=%s got=%s' % (expected_sandbox, got_sandbox))
            if sandbox_mismatch:
                errors.append('sandbox_mismatch: %s' % '; '.join(sandbox_mismatch))
                if failure_type is None:
                    failure_type = 'sandbox_mismatch'

        expected_execution_mode = expected.get('execution_mode')
        if expected_execution_mode is not None:
            plan_data = _as_dict(result.get('plan'))
            got_execution_mode = result.get('execution_mode') or plan_data.get('execution_mode')
            if got_execution_mode != expected_execution_mode:
                errors.append('execution_mode expected=%s got=%s' % (expected_execution_mode, got_execution_mode))
                if failure_type is None:
                    failure_type = 'execution_mode_mismatch'

        expected_human_gate = expected.get('human_gate')
        if expected_human_gate is not None:
            got_human_gate = result.get('human_gate') or {}
            human_gate_mismatch = []
            if isinstance(expected_human_gate, dict):
                for key, value in expected_human_gate.items():
                    if got_human_gate.get(key) != value:
                        human_gate_mismatch.append('%s expected=%s got=%s' % (key, value, got_human_gate.get(key)))
            elif got_human_gate != expected_human_gate:
                human_gate_mismatch.append('human_gate expected=%s got=%s' % (expected_human_gate, got_human_gate))
            if human_gate_mismatch:
                errors.append('human_gate_mismatch: %s' % '; '.join(human_gate_mismatch))
                if failure_type is None:
                    failure_type = 'human_gate_mismatch'

        # Phase 21-D: ReAct observation governance checks.
        expected_react = _as_dict(expected.get('react'))
        if expected_react:
            react_events = [_as_dict(e) for e in trace
                            if _as_dict(e).get('name') == 'react_observation']
            react_actions = [_as_dict(e.get('metadata')).get('action')
                             for e in react_events]
            min_obs = expected_react.get('min_observations')
            if min_obs is not None and len(react_events) < min_obs:
                errors.append('react_observation_missing: got=%d min=%d' % (
                    len(react_events), min_obs))
                if failure_type is None:
                    failure_type = 'react_observation_missing'
            exp_actions = expected_react.get('expected_actions')
            if exp_actions is not None:
                got_actions = set(a for a in react_actions if a)
                if got_actions != set(exp_actions):
                    errors.append('react_action expected=%s got=%s' % (
                        exp_actions, react_actions))
                    if failure_type is None:
                        failure_type = 'react_action_mismatch'
            exp_injectable = expected_react.get('injectable')
            if exp_injectable is not None:
                got_injectable = any(
                    _as_dict(e.get('metadata')).get('injectable')
                    for e in react_events)
                if bool(got_injectable) != bool(exp_injectable):
                    errors.append('react_injectable expected=%s got=%s' % (
                        exp_injectable, got_injectable))
                    if failure_type is None:
                        failure_type = 'react_injectable_mismatch'

            # Phase 21-E: response diagnostics expose bounded-loop control
            # data. This checks control metadata only, never raw observations.
            react_loop = _as_dict(_as_dict(result.get('diagnostics')).get('react_loop'))
            expected_steps = expected_react.get('steps')
            if expected_steps is not None and react_loop.get('steps') != expected_steps:
                errors.append('react_steps expected=%s got=%s' % (
                    expected_steps, react_loop.get('steps')))
                if failure_type is None:
                    failure_type = 'react_loop_mismatch'
            expected_terminal = expected_react.get('terminal_action')
            if expected_terminal is not None and react_loop.get('terminal_action') != expected_terminal:
                errors.append('react_terminal_action expected=%s got=%s' % (
                    expected_terminal, react_loop.get('terminal_action')))
                if failure_type is None:
                    failure_type = 'react_loop_mismatch'
            expected_replan_count = expected_react.get('replan_count')
            if expected_replan_count is not None and react_loop.get('replan_count') != expected_replan_count:
                errors.append('react_replan_count expected=%s got=%s' % (
                    expected_replan_count, react_loop.get('replan_count')))
                if failure_type is None:
                    failure_type = 'react_loop_mismatch'
            expected_replan_events = expected_react.get('replan_events')
            if expected_replan_events is not None:
                replan_events = [e for e in trace if _as_dict(e).get('name') == 'react_replan']
                if len(replan_events) != expected_replan_events:
                    errors.append('react_replan_events expected=%s got=%s' % (
                        expected_replan_events, len(replan_events)))
                    if failure_type is None:
                        failure_type = 'react_loop_mismatch'

        # Additional unsupported / blocked classifications.
        if expected_status in ('unsupported', 'template_only') and result.get('status') != expected_status:

            errors.append('unsupported_mismatch')
            if failure_type is None:
                failure_type = 'unsupported_mismatch'

        if result.get('status') == 'blocked' and not result.get('blocked_reason'):
            errors.append('blocked result missing blocked_reason')
            if failure_type is None:
                failure_type = 'status_mismatch'

        if not errors:
            failure_type = None
        else:
            failure_type = classify_failure(failure_type)

        return {
            'id': case.get('id') or case.get('case_id') or 'case',
            'query': case.get('query', ''),
            'category': case.get('category', ''),
            'expected': expected,
            'passed': len(errors) == 0,
            'failure_type': failure_type,
            'errors': errors,
            'result': result,
            'trace': trace,
            'trace_summary': trace_summary,
        }

    def summarize(self, results):
        """Produce aggregate metrics and failure breakdown."""
        results = results or []
        total = len(results)
        passed = sum(1 for item in results if item.get('passed'))
        failed = total - passed
        failure_breakdown = {}
        failure_stage_breakdown = {}
        route_total = 0
        route_hit = 0
        task_type_total = 0
        task_type_hit = 0
        contract_total = total
        contract_hit = 0
        trace_total = 0
        trace_hit = 0
        metric_total = 0
        metric_hit = 0
        dimension_total = 0
        dimension_hit = 0
        status_total = 0
        status_hit = 0
        risk_total = 0
        risk_hit = 0
        approval_total = 0
        approval_hit = 0
        prompt_chain_total = 0
        prompt_chain_hit = 0
        sandbox_total = 0
        sandbox_hit = 0
        human_gate_total = 0
        human_gate_hit = 0
        execution_mode_total = 0
        execution_mode_hit = 0
        react_case_total = 0
        react_case_with_obs = 0
        react_action_total = 0
        react_action_hit = 0
        react_obs_total = 0
        react_obs_quarantined = 0

        for item in results:

            expected = _as_dict(item.get('expected'))
            result = _as_dict(item.get('result'))
            trace = item.get('trace') or []
            failure_type = item.get('failure_type')
            if failure_type:
                failure_breakdown[failure_type] = failure_breakdown.get(failure_type, 0) + 1
                # Keep the report layer dependency-free: stage labels are a
                # stable observability projection of already-classified errors.
                if failure_type in ('routing_error',):
                    failure_stage = 'routing'
                elif failure_type in ('planning_error',):
                    failure_stage = 'planning'
                elif failure_type in ('execution_error',):
                    failure_stage = 'execution'
                elif failure_type in ('analysis_error',):
                    failure_stage = 'analysis'
                elif failure_type in ('report_error', 'contract_error'):
                    failure_stage = 'report'
                elif failure_type in ('governance_error',):
                    failure_stage = 'governance'
                else:
                    failure_stage = 'unknown'
                failure_stage_breakdown[failure_stage] = failure_stage_breakdown.get(failure_stage, 0) + 1

            if expected.get('intent') is not None:
                route_total += 1
                if result.get('intent') == expected.get('intent'):
                    route_hit += 1

            if expected.get('task_type') is not None:
                task_type_total += 1
                if result.get('task_type') == expected.get('task_type'):
                    task_type_hit += 1

            if item.get('case_type') == 'external_tool':
                contract_hit += 1
            else:
                ok, _missing = validate_response_contract(result)
                if ok:
                    contract_hit += 1

            trace_events = expected.get('trace_events') or []
            if trace_events:
                trace_total += 1
                names = _event_names(trace)
                if all(name in names for name in trace_events):
                    trace_hit += 1

            if expected.get('metric') is not None:
                metric_total += 1
                if result.get('metric') == expected.get('metric'):
                    metric_hit += 1

            if expected.get('dimensions') is not None:
                dimension_total += 1
                if list(result.get('dimensions') or []) == list(expected.get('dimensions') or []):
                    dimension_hit += 1

            if expected.get('status') is not None:
                status_total += 1
                if result.get('status') == expected.get('status'):
                    status_hit += 1

            if expected.get('risk_level') is not None:
                risk_total += 1
                got_risk = result.get('risk_level')
                if item.get('case_type') == 'external_tool':
                    got_risk = _as_dict(result.get('trace_event')).get('risk_level')
                if got_risk == expected.get('risk_level'):
                    risk_hit += 1

            if expected.get('approval_status') is not None:
                approval_total += 1
                if result.get('approval_status') == expected.get('approval_status'):
                    approval_hit += 1

            if expected.get('prompt_chain') is not None:
                prompt_chain_total += 1
                if list(result.get('prompt_chain') or []) == list(expected.get('prompt_chain') or []):
                    prompt_chain_hit += 1

            if expected.get('sandbox') is not None:
                sandbox_total += 1
                got_sandbox = result.get('sandbox') or {}
                expected_sandbox = expected.get('sandbox')
                if isinstance(expected_sandbox, dict):
                    ok = True
                    for key, value in expected_sandbox.items():
                        if got_sandbox.get(key) != value:
                            ok = False
                            break
                    if ok:
                        sandbox_hit += 1
                elif got_sandbox == expected_sandbox:
                    sandbox_hit += 1

            if expected.get('execution_mode') is not None:
                execution_mode_total += 1
                plan_data = _as_dict(result.get('plan'))
                got_execution_mode = result.get('execution_mode') or plan_data.get('execution_mode')
                if got_execution_mode == expected.get('execution_mode'):
                    execution_mode_hit += 1

            if expected.get('human_gate') is not None:
                human_gate_total += 1
                got_human_gate = result.get('human_gate') or {}
                expected_human_gate = expected.get('human_gate')
                if isinstance(expected_human_gate, dict):
                    ok = True
                    for key, value in expected_human_gate.items():
                        if got_human_gate.get(key) != value:
                            ok = False
                            break
                    if ok:
                        human_gate_hit += 1
                elif got_human_gate == expected_human_gate:
                    human_gate_hit += 1

            # Phase 21-D: ReAct observation governance metrics.
            expected_react = _as_dict(expected.get('react'))
            react_events = [_as_dict(e) for e in trace
                            if _as_dict(e).get('name') == 'react_observation']
            if expected_react or react_events:
                react_case_total += 1
                if react_events:
                    react_case_with_obs += 1
                for ev in react_events:
                    action = _as_dict(ev.get('metadata')).get('action')
                    react_obs_total += 1
                    if action == 'quarantine':
                        react_obs_quarantined += 1
                exp_actions = expected_react.get('expected_actions')
                if exp_actions is not None:
                    react_action_total += 1
                    got_actions = set(
                        _as_dict(ev.get('metadata')).get('action')
                        for ev in react_events)
                    got_actions = set(a for a in got_actions if a)
                    if got_actions == set(exp_actions):
                        react_action_hit += 1

        def _rate(hit, denom):

            if not denom:
                return None
            return hit * 1.0 / denom

        trace_quality = summarize_trace_quality(results)

        return {
            'total': total,
            'passed': passed,
            'failed': failed,
            'pass_rate': _rate(passed, total) or 0.0,
            'route_accuracy': _rate(route_hit, route_total),
            'task_type_accuracy': _rate(task_type_hit, task_type_total),
            'contract_pass_rate': _rate(contract_hit, contract_total),
            'trace_complete_rate': _rate(trace_hit, trace_total),
            'status_accuracy': _rate(status_hit, status_total),
            'metric_accuracy': _rate(metric_hit, metric_total),
            'dimension_accuracy': _rate(dimension_hit, dimension_total),
            'risk_accuracy': _rate(risk_hit, risk_total),
            'approval_status_accuracy': _rate(approval_hit, approval_total),
            'prompt_chain_accuracy': _rate(prompt_chain_hit, prompt_chain_total),
            'sandbox_accuracy': _rate(sandbox_hit, sandbox_total),
            'human_gate_accuracy': _rate(human_gate_hit, human_gate_total),
            'execution_mode_accuracy': _rate(execution_mode_hit, execution_mode_total),
            'react_observation_coverage_rate': _rate(react_case_with_obs, react_case_total),
            'react_action_accuracy': _rate(react_action_hit, react_action_total),
            'react_quarantine_rate': _rate(react_obs_quarantined, react_obs_total),
            'react_observation_total': react_obs_total,
            'failure_breakdown': failure_breakdown,
            'failure_stage_breakdown': failure_stage_breakdown,
            'dag_trace_quality': trace_quality,
        }


    def run_suite(self, suite_name, cases):
        results = self.run_cases(cases)
        metrics = self.summarize(results)
        failures = [item for item in results if not item.get('passed')]
        report = {
            'suite': suite_name,
            'timestamp_ms': _now_ms(),
            'metrics': metrics,
            'results': results,
            'failures': failures,
        }
        self.save_report(report, suite_name)
        return report

    def save_report(self, report, suite_name):
        _ensure_dir(self.reports_dir)
        safe_name = os.path.splitext(os.path.basename(suite_name))[0]
        latest_path = os.path.join(self.reports_dir, '%s_latest.json' % safe_name)
        stamp_path = os.path.join(self.reports_dir, '%s_%s.json' % (safe_name, time.strftime('%Y%m%d_%H%M%S')))

        payload = json.dumps(_sanitize(report), ensure_ascii=False, indent=2, sort_keys=True, default=str)
        for path in (latest_path, stamp_path):
            with codecs.open(path, 'w', encoding='utf-8') as f:
                f.write(payload)
        return latest_path


__all__ = ['AgentHarness', 'summarize_trace_quality']
