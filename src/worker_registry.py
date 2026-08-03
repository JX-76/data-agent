# -*- coding: utf-8 -*-
"""Worker registry for controlled Supervisor-Worker multi-agent runtime."""
from __future__ import unicode_literals

import time

try:
    basestring
except NameError:  # pragma: no cover - Python 3 compatibility
    basestring = str

from multi_agent_contracts import (
    AgentResult, RESULT_OK, RESULT_ERROR, A2A_VALID_RESULT_STATUSES,
    WORKER_DATA_ANALYSIS, WORKER_KNOWLEDGE_QA, WORKER_TOOL,
    WORKER_SAFETY, WORKER_CLARIFICATION, WORKER_DATA_ANALYST,
    WORKER_DIAGNOSIS, WORKER_AUDITOR,
)
from evidence_bus import EvidenceBus


class WorkerSpec(object):
    def __init__(self, worker_type, handler, description='', input_schema=None,
                 output_schema=None, allowed_tools=None, max_steps=4):
        if not worker_type:
            raise ValueError('worker_type is required')
        if not callable(handler):
            raise ValueError('handler must be callable')
        self.worker_type = worker_type
        self.handler = handler
        self.description = description or ''
        self.input_schema = dict(input_schema or {})
        self.output_schema = dict(output_schema or {})
        self.allowed_tools = list(allowed_tools or [])
        self.max_steps = int(max_steps or 4)

    def to_dict(self):
        return {
            'worker_type': self.worker_type,
            'description': self.description,
            'input_schema': dict(self.input_schema),
            'output_schema': dict(self.output_schema),
            'allowed_tools': list(self.allowed_tools),
            'max_steps': self.max_steps,
        }


def _raw_result_has_explicit_status(raw):
    if isinstance(raw, AgentResult):
        return True
    if isinstance(raw, dict):
        if raw.get('status'):
            return True
        payload = raw.get('payload') if isinstance(raw.get('payload'), dict) else {}
        return bool(payload.get('status'))
    return False


def _matches_json_schema_type(value, expected_type):
    if expected_type == 'string':
        return isinstance(value, basestring)
    if expected_type == 'array':
        return isinstance(value, list)
    if expected_type == 'object':
        return isinstance(value, dict)
    if expected_type == 'boolean':
        return isinstance(value, bool)
    if expected_type == 'integer':
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == 'number':
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected_type == 'null':
        return value is None
    return True


def _find_output_type_errors(output, output_schema):
    properties = output_schema.get('properties') or {}
    errors = []
    for field, schema in properties.items():
        if field not in output or not isinstance(schema, dict):
            continue
        expected = schema.get('type')
        if isinstance(expected, list):
            ok = any(_matches_json_schema_type(output.get(field), item) for item in expected)
        elif expected:
            ok = _matches_json_schema_type(output.get(field), expected)
        else:
            ok = True
        if not ok:
            errors.append({'field': field, 'expected': expected})
    return errors


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _expected_scope_from_input(task_input):
    task_input = task_input or {}
    allowed_time_ranges = task_input.get('allowed_time_ranges')
    if allowed_time_ranges is None:
        allowed_time_ranges = []
        if task_input.get('time_range') is not None:
            allowed_time_ranges.append(task_input.get('time_range'))
        if task_input.get('compare_time_range') is not None:
            allowed_time_ranges.append(task_input.get('compare_time_range'))
        if task_input.get('previous_time_range') is not None:
            allowed_time_ranges.append(task_input.get('previous_time_range'))
    return {
        'metric': task_input.get('metric'),
        'allowed_time_ranges': allowed_time_ranges,
        'dimensions': list(task_input.get('dimensions') or []),
        'filters': task_input.get('filters') or {},
        'dataid': task_input.get('dataid'),
        'data_version': task_input.get('data_version'),
        'tenant_id': task_input.get('tenant_id'),
        'user_id': task_input.get('user_id'),
        'permission_scope': task_input.get('permission_scope'),
    }


def _get_or_create_evidence_bus(dag_state):
    if dag_state is None:
        return None
    bus = dag_state.get('evidence_bus')
    if isinstance(bus, EvidenceBus):
        return bus
    if isinstance(bus, dict):
        bus = EvidenceBus(bus.get('records') or [])
        dag_state['evidence_bus'] = bus
        return bus
    bus = EvidenceBus()
    dag_state['evidence_bus'] = bus
    return bus


class WorkerRegistry(object):
    def __init__(self):
        self._workers = {}

    def register(self, spec):
        if not isinstance(spec, WorkerSpec):
            spec = WorkerSpec(**dict(spec or {}))
        self._workers[spec.worker_type] = spec
        return spec

    def get(self, worker_type):
        return self._workers.get(worker_type)

    def list_workers(self):
        return [self._workers[k].to_dict() for k in sorted(self._workers.keys())]

    def run(self, task, dag_state=None):
        spec = self.get(task.worker_type)
        if spec is None:
            return AgentResult(task.task_id, status=RESULT_ERROR,
                               errors=[{'error': 'worker_not_found', 'worker_type': task.worker_type}])
        required = spec.input_schema.get('required') or []
        missing = [k for k in required if k not in task.input]
        if missing:
            return AgentResult(task.task_id, status=RESULT_ERROR,
                               errors=[{'error': 'missing_required_input', 'fields': missing}])
        t0 = time.time()
        try:
            raw = spec.handler(task, dag_state)
            if not _raw_result_has_explicit_status(raw):
                result = AgentResult(
                    task.task_id,
                    status=RESULT_ERROR,
                    output={'authority': 'unverified', 'limitations': ['missing_worker_result_status']},
                    errors=[{'error': 'missing_worker_result_status'}],
                )
                result.metrics.setdefault('latency_ms', int((time.time() - t0) * 1000))
                return result
            result = AgentResult.from_value(raw, task_id=task.task_id)
            if result.status not in A2A_VALID_RESULT_STATUSES:
                result = AgentResult(
                    task.task_id,
                    status=RESULT_ERROR,
                    output={'authority': 'unverified', 'limitations': ['invalid_worker_result_status']},
                    errors=[{'error': 'invalid_worker_result_status', 'status': result.status}],
                    metrics=result.metrics,
                )
            elif result.status == RESULT_OK:
                required_output = spec.output_schema.get('required') or []
                missing_output = [k for k in required_output if k not in result.output]
                if missing_output:
                    result = AgentResult(
                        task.task_id,
                        status=RESULT_ERROR,
                        output={'authority': 'unverified', 'limitations': ['missing_required_output']},
                        errors=[{'error': 'missing_required_output', 'fields': missing_output}],
                        metrics=result.metrics,
                    )
                else:
                    type_errors = _find_output_type_errors(result.output, spec.output_schema)
                    if type_errors:
                        result = AgentResult(
                            task.task_id,
                            status=RESULT_ERROR,
                            output={'authority': 'unverified', 'limitations': ['invalid_output_type']},
                            errors=[{'error': 'invalid_output_type', 'fields': type_errors}],
                            metrics=result.metrics,
                        )
                    else:
                        envelope_error = self._validate_execution_envelope(result.output)
                        if envelope_error:
                            result = AgentResult(
                                task.task_id,
                                status=RESULT_ERROR,
                                output={'authority': 'unverified', 'limitations': [envelope_error.get('error')]},
                                errors=[envelope_error],
                                metrics=result.metrics,
                            )
                    if result.status == RESULT_OK and spec.output_schema.get('evidence_ids_must_resolve'):
                        bus = _get_or_create_evidence_bus(dag_state)
                        if bus is not None:
                            bus.record_envelope((result.output or {}).get('execution_envelope'), producer_task_id=task.task_id,
                                                trace_id=(dag_state or {}).get('trace_id'))
                            evidence_ids = _as_list((result.output or {}).get('evidence_ids'))
                            valid, missing = bus.validate_ids(evidence_ids)
                            if evidence_ids and missing:
                                result = AgentResult(
                                    task.task_id,
                                    status=RESULT_ERROR,
                                    output={'authority': 'unverified', 'limitations': ['unresolved_evidence_ids']},
                                    errors=[{'error': 'unresolved_evidence_ids', 'missing': missing, 'valid': valid}],
                                    metrics=result.metrics,
                                )
                            elif evidence_ids and spec.output_schema.get('evidence_scope_must_match'):
                                expected_scope = task.input.get('expected_scope') or (result.output or {}).get('expected_scope') or _expected_scope_from_input(task.input)
                                ttl_seconds = task.input.get('evidence_ttl_seconds')
                                if ttl_seconds is None:
                                    ttl_seconds = spec.output_schema.get('evidence_ttl_seconds')
                                now = task.input.get('evidence_now')
                                valid, rejected = bus.validate_scope(
                                    evidence_ids,
                                    expected_scope=expected_scope,
                                    ttl_seconds=ttl_seconds,
                                    now=now)
                                if rejected:
                                    result = AgentResult(
                                        task.task_id,
                                        status=RESULT_ERROR,
                                        output={'authority': 'unverified', 'limitations': ['invalid_evidence_scope']},
                                        errors=[{'error': 'invalid_evidence_scope', 'rejected': rejected, 'valid': valid}],
                                        metrics=result.metrics,
                                    )
            result.metrics.setdefault('latency_ms', int((time.time() - t0) * 1000))
            return result
        except Exception as exc:
            return AgentResult(task.task_id, status=RESULT_ERROR,
                               errors=[{'error': str(exc)}],
                               metrics={'latency_ms': int((time.time() - t0) * 1000)})

    def _validate_execution_envelope(self, output):
        output = output or {}
        envelope = output.get('execution_envelope') if isinstance(output, dict) else None
        if envelope is None:
            return None
        if not isinstance(envelope, dict):
            return {'error': 'invalid_execution_envelope', 'reason': 'not_object'}
        status = envelope.get('status')
        authority = envelope.get('authority')
        evidence_id = envelope.get('evidence_id')
        if status != RESULT_OK:
            return {'error': 'invalid_execution_envelope', 'reason': 'non_ok_envelope_status', 'status': status}
        if authority != 'verified_execution':
            return {'error': 'invalid_execution_envelope', 'reason': 'unverified_authority', 'authority': authority}
        if not evidence_id:
            return {'error': 'invalid_execution_envelope', 'reason': 'missing_evidence_id'}
        return None


def echo_worker(task, dag_state=None):
    return AgentResult(task.task_id, status=RESULT_OK, output=dict(task.input))


def merge_inputs_worker(task, dag_state=None):
    state = dag_state or {}
    outputs = {}
    for dep in task.dependencies:
        result = (state.get('results') or {}).get(dep)
        if result:
            outputs[dep] = result.get('output') or {}
    data = dict(task.input)
    data['dependency_outputs'] = outputs
    return AgentResult(task.task_id, status=RESULT_OK, output=data)


def build_default_worker_registry():
    registry = WorkerRegistry()
    for worker_type in [
        WORKER_DATA_ANALYSIS,
        WORKER_KNOWLEDGE_QA,
        WORKER_TOOL,
        WORKER_CLARIFICATION,
        WORKER_DATA_ANALYST,
        WORKER_DIAGNOSIS,
        WORKER_AUDITOR,
    ]:
        registry.register(WorkerSpec(worker_type, echo_worker, description='local deterministic %s worker' % worker_type))
    registry.register(WorkerSpec('merge', merge_inputs_worker, description='merge dependency outputs'))
    registry.register(WorkerSpec(WORKER_SAFETY, echo_worker, description='local deterministic safety worker'))
    return registry


__all__ = ['WorkerSpec', 'WorkerRegistry', 'build_default_worker_registry']
