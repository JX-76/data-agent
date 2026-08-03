# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import time
import uuid

STATUS_PENDING = 'pending'
STATUS_READY = 'ready'
STATUS_RUNNING = 'running'
STATUS_SUCCEEDED = 'succeeded'
STATUS_FAILED = 'failed'
STATUS_SKIPPED = 'skipped'
STATUS_BLOCKED = 'blocked'

RESULT_OK = 'ok'
RESULT_ERROR = 'error'
RESULT_BLOCKED = 'blocked'
RESULT_PARTIAL = 'partial'
RESULT_NEED_CLARIFICATION = 'need_clarification'
RESULT_PENDING_HUMAN_REVIEW = 'pending_human_review'

WORKER_DATA_ANALYSIS = 'data_analysis'
WORKER_KNOWLEDGE_QA = 'knowledge_qa'
WORKER_TOOL = 'tool'
WORKER_SAFETY = 'safety'
WORKER_CLARIFICATION = 'clarification'

# Target multi-agent worker types.  Keep legacy worker names above for
# compatibility; these are additive aliases for the ecommerce multi-agent path.
WORKER_DATA_ANALYST = 'data_analyst'
WORKER_DIAGNOSIS = 'diagnosis'
WORKER_AUDITOR = 'auditor'

A2A_MESSAGE_REQUEST = 'request'
A2A_MESSAGE_RESULT = 'result'
A2A_VALID_MESSAGE_TYPES = (A2A_MESSAGE_REQUEST, A2A_MESSAGE_RESULT)
A2A_VALID_RESULT_STATUSES = (
    RESULT_OK,
    RESULT_ERROR,
    RESULT_BLOCKED,
    RESULT_PARTIAL,
    RESULT_NEED_CLARIFICATION,
    RESULT_PENDING_HUMAN_REVIEW,
)


def _new_id():
    return str(uuid.uuid4())


def _a2a_error(field, error, message):
    return {'field': field, 'error': error, 'message': message}


class AgentBudget(object):
    def __init__(self, max_steps=4, timeout_ms=3000, max_tokens=4000):
        self.max_steps = int(max_steps or 4)
        self.timeout_ms = int(timeout_ms or 3000)
        self.max_tokens = int(max_tokens or 4000)

    @classmethod
    def from_value(cls, value):
        if isinstance(value, AgentBudget):
            return value
        data = dict(value or {})
        return cls(data.get('max_steps', 4), data.get('timeout_ms', 3000), data.get('max_tokens', 4000))

    def to_dict(self):
        return {'max_steps': self.max_steps, 'timeout_ms': self.timeout_ms, 'max_tokens': self.max_tokens}


class A2AMessage(object):
    def __init__(self, trace_id=None, task_id=None, from_agent=None, to_agent=None,
                 message_type=None, constraints=None, evidence_context=None,
                 expected_schema=None, reply_to=None, payload=None, status=None):
        self.trace_id = trace_id
        self.task_id = task_id
        self.from_agent = from_agent
        self.to_agent = to_agent
        self.message_type = message_type
        self.constraints = dict(constraints or {})
        self.evidence_context = dict(evidence_context or {})
        self.expected_schema = dict(expected_schema or {})
        self.reply_to = reply_to
        self.payload = payload if payload is not None else {}
        self.status = status

    @classmethod
    def from_dict(cls, data):
        if isinstance(data, cls):
            return data
        data = dict(data or {})
        return cls(
            trace_id=data.get('trace_id'),
            task_id=data.get('task_id'),
            from_agent=data.get('from_agent'),
            to_agent=data.get('to_agent'),
            message_type=data.get('message_type'),
            constraints=data.get('constraints') or {},
            evidence_context=data.get('evidence_context') or {},
            expected_schema=data.get('expected_schema') or {},
            reply_to=data.get('reply_to'),
            payload=data.get('payload') or {},
            status=data.get('status'),
        )

    def to_dict(self):
        data = {
            'trace_id': self.trace_id,
            'task_id': self.task_id,
            'from_agent': self.from_agent,
            'to_agent': self.to_agent,
            'message_type': self.message_type,
            'constraints': dict(self.constraints),
            'evidence_context': dict(self.evidence_context),
            'expected_schema': dict(self.expected_schema),
            'reply_to': self.reply_to,
            'payload': self.payload if self.payload is not None else {},
        }
        if self.status is not None:
            data['status'] = self.status
        return data


class A2AEnvelope(object):
    def __init__(self, trace_id=None, task_id=None, from_agent=None, to_agent=None,
                 message_type=None, constraints=None, evidence_context=None,
                 expected_schema=None, reply_to=None, payload=None, status=None,
                 messages=None):
        self.trace_id = trace_id
        self.task_id = task_id
        self.from_agent = from_agent
        self.to_agent = to_agent
        self.message_type = message_type
        self.constraints = dict(constraints or {})
        self.evidence_context = dict(evidence_context or {})
        self.expected_schema = dict(expected_schema or {})
        self.reply_to = reply_to
        self.payload = payload if payload is not None else {}
        self.status = status
        self.messages = [A2AMessage.from_dict(m) for m in (messages or [])]
        if not self.messages and any([
            self.trace_id, self.task_id, self.from_agent, self.to_agent,
            self.message_type, self.constraints, self.evidence_context,
            self.expected_schema, self.reply_to, self.payload, self.status is not None,
        ]):
            self.messages = [A2AMessage(
                trace_id=self.trace_id,
                task_id=self.task_id,
                from_agent=self.from_agent,
                to_agent=self.to_agent,
                message_type=self.message_type,
                constraints=self.constraints,
                evidence_context=self.evidence_context,
                expected_schema=self.expected_schema,
                reply_to=self.reply_to,
                payload=self.payload,
                status=self.status,
            )]

    @classmethod
    def from_dict(cls, data):
        if isinstance(data, cls):
            return data
        data = dict(data or {})
        messages = data.get('messages') or []
        if messages:
            parsed_messages = [A2AMessage.from_dict(m) for m in messages]
            first = parsed_messages[0]
            return cls(
                trace_id=data.get('trace_id') or first.trace_id,
                task_id=data.get('task_id') or first.task_id,
                from_agent=data.get('from_agent') or first.from_agent,
                to_agent=data.get('to_agent') or first.to_agent,
                message_type=data.get('message_type') or first.message_type,
                constraints=data.get('constraints') or first.constraints,
                evidence_context=data.get('evidence_context') or first.evidence_context,
                expected_schema=data.get('expected_schema') or first.expected_schema,
                reply_to=data.get('reply_to') or first.reply_to,
                payload=data.get('payload') or first.payload,
                status=data.get('status') or first.status,
                messages=parsed_messages,
            )
        return cls(
            trace_id=data.get('trace_id'),
            task_id=data.get('task_id'),
            from_agent=data.get('from_agent'),
            to_agent=data.get('to_agent'),
            message_type=data.get('message_type'),
            constraints=data.get('constraints') or {},
            evidence_context=data.get('evidence_context') or {},
            expected_schema=data.get('expected_schema') or {},
            reply_to=data.get('reply_to'),
            payload=data.get('payload') or {},
            status=data.get('status'),
        )

    def to_dict(self):
        if len(self.messages) == 1:
            return self.messages[0].to_dict()
        data = {
            'trace_id': self.trace_id,
            'task_id': self.task_id,
            'from_agent': self.from_agent,
            'to_agent': self.to_agent,
            'message_type': self.message_type,
            'constraints': dict(self.constraints),
            'evidence_context': dict(self.evidence_context),
            'expected_schema': dict(self.expected_schema),
            'reply_to': self.reply_to,
            'payload': self.payload if self.payload is not None else {},
        }
        if self.status is not None:
            data['status'] = self.status
        if self.messages:
            data['messages'] = [m.to_dict() for m in self.messages]
        return data

    def _validate_common(self):
        errors = []
        if not self.trace_id:
            errors.append(_a2a_error('trace_id', 'missing_trace_id', 'trace_id is required'))
        if not self.task_id:
            errors.append(_a2a_error('task_id', 'missing_task_id', 'task_id is required'))
        if not self.from_agent:
            errors.append(_a2a_error('from_agent', 'missing_from_agent', 'from_agent is required'))
        if not self.to_agent:
            errors.append(_a2a_error('to_agent', 'missing_to_agent', 'to_agent is required'))
        if self.message_type not in A2A_VALID_MESSAGE_TYPES:
            errors.append(_a2a_error('message_type', 'invalid_message_type', 'unsupported message_type: %s' % self.message_type))
        if self.expected_schema in (None, {}):
            errors.append(_a2a_error('expected_schema', 'missing_expected_schema', 'expected_schema is required'))
        return errors

    def validate_request(self):
        errors = self._validate_common()
        if self.message_type != A2A_MESSAGE_REQUEST:
            errors.append(_a2a_error('message_type', 'invalid_message_type', 'request envelopes must use message_type=request'))
        if self.payload in (None, {}):
            errors.append(_a2a_error('payload', 'missing_payload', 'request payload is required'))
        return errors

    def validate_result(self):
        errors = self._validate_common()
        if self.message_type != A2A_MESSAGE_RESULT:
            errors.append(_a2a_error('message_type', 'invalid_message_type', 'result envelopes must use message_type=result'))
        if self.status is None:
            errors.append(_a2a_error('status', 'missing_status', 'result status is required'))
        elif self.status not in A2A_VALID_RESULT_STATUSES:
            errors.append(_a2a_error('status', 'invalid_status', 'unsupported result status: %s' % self.status))
        return errors


class AgentTask(object):
    def __init__(self, worker_type, task_input=None, task_id=None, parent_task_id=None,
                 intent=None, dependencies=None, budget=None, risk_level='low',
                 idempotency_key=None, metadata=None):
        self.task_id = task_id or _new_id()
        self.parent_task_id = parent_task_id
        self.worker_type = worker_type
        self.intent = intent or ''
        self.input = dict(task_input or {})
        self.dependencies = list(dependencies or [])
        self.budget = AgentBudget.from_value(budget)
        self.risk_level = risk_level or 'low'
        self.idempotency_key = idempotency_key or self.task_id
        self.metadata = dict(metadata or {})

    @classmethod
    def from_dict(cls, data):
        data = dict(data or {})
        payload = data.get('payload') if isinstance(data.get('payload'), dict) else {}
        task_input = data.get('input') or data.get('task_input') or payload.get('input') or payload.get('task_input') or {}
        worker_type = data.get('worker_type') or payload.get('worker_type') or data.get('to_agent') or payload.get('to_agent')
        return cls(
            worker_type=worker_type,
            task_input=task_input,
            task_id=data.get('task_id') or payload.get('task_id'),
            parent_task_id=data.get('parent_task_id') or payload.get('parent_task_id'),
            intent=data.get('intent') or payload.get('intent'),
            dependencies=data.get('dependencies') or payload.get('dependencies') or [],
            budget=data.get('budget') or payload.get('budget') or {},
            risk_level=data.get('risk_level', payload.get('risk_level', 'low')),
            idempotency_key=data.get('idempotency_key') or payload.get('idempotency_key'),
            metadata=data.get('metadata') or payload.get('metadata') or {},
        )

    def to_dict(self):
        return {
            'task_id': self.task_id,
            'parent_task_id': self.parent_task_id,
            'worker_type': self.worker_type,
            'intent': self.intent,
            'input': dict(self.input),
            'dependencies': list(self.dependencies),
            'budget': self.budget.to_dict(),
            'risk_level': self.risk_level,
            'idempotency_key': self.idempotency_key,
            'metadata': dict(self.metadata),
        }


class AgentObservation(object):
    def __init__(self, node_id, event, status='ok', summary='', payload_ref=None,
                 timestamp=None, metadata=None):
        self.node_id = node_id
        self.event = event
        self.status = status
        self.summary = summary or ''
        self.payload_ref = payload_ref
        self.timestamp = timestamp if timestamp is not None else time.time()
        self.metadata = dict(metadata or {})

    def to_dict(self):
        return {
            'node_id': self.node_id,
            'event': self.event,
            'status': self.status,
            'summary': self.summary,
            'payload_ref': self.payload_ref,
            'timestamp': self.timestamp,
            'metadata': dict(self.metadata),
        }


class AgentResult(object):
    def __init__(self, task_id, status=RESULT_OK, output=None, observations=None,
                 citations=None, errors=None, metrics=None):
        self.task_id = task_id
        self.status = status or RESULT_OK
        self.output = dict(output or {})
        self.observations = list(observations or [])
        self.citations = list(citations or [])
        self.errors = list(errors or [])
        self.metrics = dict(metrics or {})

    @classmethod
    def from_value(cls, value, task_id=None):
        if isinstance(value, AgentResult):
            return value
        data = dict(value or {})
        payload = data.get('payload') if isinstance(data.get('payload'), dict) else {}
        return cls(
            task_id=data.get('task_id') or payload.get('task_id') or task_id,
            status=data.get('status') or payload.get('status') or RESULT_OK,
            output=data.get('output') or payload.get('output') or {},
            observations=data.get('observations') or payload.get('observations') or [],
            citations=data.get('citations') or payload.get('citations') or [],
            errors=data.get('errors') or payload.get('errors') or [],
            metrics=data.get('metrics') or payload.get('metrics') or {},
        )

    def to_dict(self):
        return {
            'task_id': self.task_id,
            'status': self.status,
            'output': dict(self.output),
            'observations': [o.to_dict() if hasattr(o, 'to_dict') else dict(o) for o in self.observations],
            'citations': list(self.citations),
            'errors': list(self.errors),
            'metrics': dict(self.metrics),
        }


def validate_a2a_message(envelope):
    data = A2AEnvelope.from_dict(envelope)
    if data.message_type == A2A_MESSAGE_RESULT:
        return data.validate_result()
    return data.validate_request()


def validate_a2a_request(envelope):
    return A2AEnvelope.from_dict(envelope).validate_request()


def validate_a2a_result(envelope):
    return A2AEnvelope.from_dict(envelope).validate_result()


__all__ = [
    'A2AEnvelope', 'A2AMessage', 'A2A_MESSAGE_REQUEST', 'A2A_MESSAGE_RESULT',
    'A2A_VALID_MESSAGE_TYPES', 'A2A_VALID_RESULT_STATUSES',
    'validate_a2a_message', 'validate_a2a_request', 'validate_a2a_result',
    'AgentBudget', 'AgentTask', 'AgentObservation', 'AgentResult',
    'STATUS_PENDING', 'STATUS_READY', 'STATUS_RUNNING', 'STATUS_SUCCEEDED',
    'STATUS_FAILED', 'STATUS_SKIPPED', 'STATUS_BLOCKED', 'RESULT_OK',
    'RESULT_ERROR', 'RESULT_BLOCKED', 'RESULT_PARTIAL', 'RESULT_NEED_CLARIFICATION',
    'RESULT_PENDING_HUMAN_REVIEW', 'WORKER_DATA_ANALYSIS', 'WORKER_KNOWLEDGE_QA',
    'WORKER_TOOL', 'WORKER_SAFETY', 'WORKER_CLARIFICATION',
    'WORKER_DATA_ANALYST', 'WORKER_DIAGNOSIS', 'WORKER_AUDITOR'
]
