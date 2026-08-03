# -*- coding: utf-8 -*-
"""Durable Task Control Plane P1.

Dependency-light, Python 2.7 compatible task persistence and state transitions.
It deliberately provides in-memory and SQLite adapters only; production database,
queue and worker adapters are extension points, not simulated production claims.
"""
from __future__ import unicode_literals

import copy
import hashlib
import json
import random
import sqlite3
import time
import uuid

TASK_RECORD_CONTRACT = 'durable_task_record_v1'
TASK_STATUS_CONTRACT = 'durable_task_status_v1'

QUEUED = 'queued'
RUNNING = 'running'
RETRY_WAIT = 'retry_wait'
SUCCEEDED = 'succeeded'
FAILED = 'failed'
BLOCKED = 'blocked'
CANCELLED = 'cancelled'
UNKNOWN = 'unknown'
TERMINAL_STATES = (SUCCEEDED, FAILED, BLOCKED, CANCELLED)

_ALLOWED = {
    QUEUED: (RUNNING, BLOCKED, CANCELLED),
    RUNNING: (SUCCEEDED, RETRY_WAIT, FAILED, BLOCKED, CANCELLED, UNKNOWN),
    RETRY_WAIT: (QUEUED, BLOCKED, CANCELLED),
    UNKNOWN: (RUNNING, SUCCEEDED, FAILED, BLOCKED, CANCELLED),
    SUCCEEDED: (), FAILED: (), BLOCKED: (), CANCELLED: (),
}
_NON_RETRYABLE = set(['schema', 'validation', 'permission', 'policy', 'security', 'cancelled'])
_TRANSIENT = set(['timeout', 'network', 'connection', 'rate_limit', '429', '503', 'unavailable', 'transient'])


class TaskStateError(Exception):
    pass


class TaskConflictError(Exception):
    pass


def _now(value=None):
    return float(time.time() if value is None else value)


def _new_id(prefix):
    return '%s_%s' % (prefix, uuid.uuid4().hex[:16])


def _safe_summary(value, limit=300):
    text = str(value or '').replace('\n', ' ').replace('\r', ' ')
    return text[:int(limit)]


def _copy(value):
    return copy.deepcopy(value)


def _hash_input(value):
    encoded = json.dumps(value if value is not None else {}, sort_keys=True, separators=(',', ':'), ensure_ascii=True)
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()


class TaskRecord(object):
    """Typed, serializable task state. Unknown extra metadata stays in ledger."""
    def __init__(self, task_id=None, case_id=None, task_type=None, worker_type=None,
                 input_ref=None, input_hash=None, idempotency_key=None, state=QUEUED,
                 parent_task_id=None, trace_id=None, session_id=None, attempt=0,
                 max_attempts=3, retry_policy=None, next_retry_at=None, deadline=None,
                 started_at=None, ended_at=None, heartbeat_at=None, tool_receipt=None,
                 execution_receipt=None, output_ref=None, error_code=None,
                 error_class=None, safe_error_summary=None, dependencies=None,
                 blocked_by=None, risk_level='low', budget=None, cost_ledger=None,
                 schema_version=1, created_at=None, updated_at=None, version=0,
                 reconciliation_required=False, audit=None):
        now = _now(created_at)
        self.task_id = task_id or _new_id('task')
        self.case_id = case_id
        self.parent_task_id = parent_task_id
        self.trace_id = trace_id
        self.session_id = session_id
        self.task_type = task_type or 'unspecified'
        self.worker_type = worker_type or 'unspecified'
        self.input_ref = input_ref
        self.input_hash = input_hash or _hash_input(input_ref)
        self.idempotency_key = idempotency_key or ('idem:%s' % self.task_id)
        self.state = state
        self.attempt = int(attempt or 0)
        self.max_attempts = max(1, int(max_attempts or 1))
        self.retry_policy = dict(retry_policy or {'base_seconds': 1, 'max_seconds': 60, 'jitter': 0})
        self.next_retry_at = next_retry_at
        self.deadline = deadline
        self.started_at = started_at
        self.ended_at = ended_at
        self.heartbeat_at = heartbeat_at
        self.tool_receipt = tool_receipt
        self.execution_receipt = execution_receipt
        self.output_ref = output_ref
        self.error_code = error_code
        self.error_class = error_class
        self.safe_error_summary = safe_error_summary
        self.dependencies = list(dependencies or [])
        self.blocked_by = list(blocked_by or [])
        self.risk_level = risk_level or 'low'
        self.budget = dict(budget or {})
        self.cost_ledger = dict(cost_ledger or {})
        self.schema_version = int(schema_version or 1)
        self.created_at = now
        self.updated_at = _now(updated_at if updated_at is not None else now)
        self.version = int(version or 0)
        self.reconciliation_required = bool(reconciliation_required)
        self.audit = list(audit or [])

    @classmethod
    def from_dict(cls, data):
        data = dict(data or {})
        data.pop('contract', None)
        return cls(**data)

    def to_dict(self):
        return {
            'contract': TASK_RECORD_CONTRACT, 'task_id': self.task_id, 'case_id': self.case_id,
            'parent_task_id': self.parent_task_id, 'trace_id': self.trace_id, 'session_id': self.session_id,
            'task_type': self.task_type, 'worker_type': self.worker_type, 'input_ref': self.input_ref,
            'input_hash': self.input_hash, 'idempotency_key': self.idempotency_key, 'state': self.state,
            'attempt': self.attempt, 'max_attempts': self.max_attempts, 'retry_policy': dict(self.retry_policy),
            'next_retry_at': self.next_retry_at, 'deadline': self.deadline, 'started_at': self.started_at,
            'ended_at': self.ended_at, 'heartbeat_at': self.heartbeat_at, 'tool_receipt': self.tool_receipt,
            'execution_receipt': self.execution_receipt, 'output_ref': self.output_ref,
            'error_code': self.error_code, 'error_class': self.error_class,
            'safe_error_summary': self.safe_error_summary, 'dependencies': list(self.dependencies),
            'blocked_by': list(self.blocked_by), 'risk_level': self.risk_level, 'budget': dict(self.budget),
            'cost_ledger': dict(self.cost_ledger), 'schema_version': self.schema_version,
            'created_at': self.created_at, 'updated_at': self.updated_at, 'version': self.version,
            'reconciliation_required': self.reconciliation_required, 'audit': list(self.audit),
        }


class TaskRepository(object):
    def create(self, record): raise NotImplementedError
    def get(self, task_id): raise NotImplementedError
    def update(self, record, expected_version): raise NotImplementedError
    def find_idempotency(self, case_id, idempotency_key): raise NotImplementedError
    def list(self, case_id=None): raise NotImplementedError


class InMemoryTaskRepository(TaskRepository):
    def __init__(self):
        self.records = {}

    def create(self, record):
        record = TaskRecord.from_dict(record.to_dict() if isinstance(record, TaskRecord) else record)
        if record.task_id in self.records:
            raise TaskConflictError('duplicate_task_id')
        existing = self.find_idempotency(record.case_id, record.idempotency_key)
        if existing:
            raise TaskConflictError('duplicate_idempotency_key')
        self.records[record.task_id] = record.to_dict()
        return TaskRecord.from_dict(self.records[record.task_id])

    def get(self, task_id):
        value = self.records.get(task_id)
        return TaskRecord.from_dict(value) if value else None

    def update(self, record, expected_version):
        current = self.get(record.task_id)
        if current is None:
            raise TaskConflictError('task_not_found')
        if int(current.version) != int(expected_version):
            raise TaskConflictError('optimistic_lock_conflict')
        self.records[record.task_id] = record.to_dict()
        return self.get(record.task_id)

    def find_idempotency(self, case_id, idempotency_key):
        for record in self.list(case_id=case_id):
            if record.idempotency_key == idempotency_key:
                return record
        return None

    def list(self, case_id=None):
        values = [TaskRecord.from_dict(v) for v in self.records.values()]
        return [v for v in values if case_id is None or v.case_id == case_id]


class SQLiteTaskRepository(TaskRepository):
    """SQLite metadata adapter. A production database adapter must implement TaskRepository."""
    def __init__(self, path=':memory:'):
        self.conn = sqlite3.connect(path)
        self.conn.execute('CREATE TABLE IF NOT EXISTS durable_tasks (task_id TEXT PRIMARY KEY, case_id TEXT, idem TEXT, version INTEGER, payload TEXT)')
        self.conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS durable_tasks_case_idem ON durable_tasks(case_id, idem)')
        self.conn.commit()

    def create(self, record):
        record = TaskRecord.from_dict(record.to_dict() if isinstance(record, TaskRecord) else record)
        try:
            self.conn.execute('INSERT INTO durable_tasks(task_id,case_id,idem,version,payload) VALUES(?,?,?,?,?)',
                              (record.task_id, record.case_id, record.idempotency_key, record.version,
                               json.dumps(record.to_dict(), sort_keys=True, ensure_ascii=True)))
            self.conn.commit()
        except sqlite3.IntegrityError:
            raise TaskConflictError('duplicate_task_or_idempotency_key')
        return record

    def get(self, task_id):
        row = self.conn.execute('SELECT payload FROM durable_tasks WHERE task_id=?', (task_id,)).fetchone()
        return TaskRecord.from_dict(json.loads(row[0])) if row else None

    def update(self, record, expected_version):
        cursor = self.conn.execute('UPDATE durable_tasks SET version=?,payload=? WHERE task_id=? AND version=?',
            (record.version, json.dumps(record.to_dict(), sort_keys=True, ensure_ascii=True), record.task_id, int(expected_version)))
        self.conn.commit()
        if cursor.rowcount != 1:
            raise TaskConflictError('optimistic_lock_conflict')
        return self.get(record.task_id)

    def find_idempotency(self, case_id, idempotency_key):
        row = self.conn.execute('SELECT payload FROM durable_tasks WHERE case_id IS ? AND idem=?', (case_id, idempotency_key)).fetchone()
        return TaskRecord.from_dict(json.loads(row[0])) if row else None

    def list(self, case_id=None):
        if case_id is None:
            rows = self.conn.execute('SELECT payload FROM durable_tasks').fetchall()
        else:
            rows = self.conn.execute('SELECT payload FROM durable_tasks WHERE case_id IS ?', (case_id,)).fetchall()
        return [TaskRecord.from_dict(json.loads(row[0])) for row in rows]

    def close(self):
        self.conn.close()


class DurableTaskControlPlane(object):
    def __init__(self, repository=None, clock=None, randomizer=None):
        self.repository = repository or InMemoryTaskRepository()
        self.clock = clock or time.time
        self.randomizer = randomizer or random.random

    def submit(self, data):
        record = data if isinstance(data, TaskRecord) else TaskRecord.from_dict(data)
        duplicate = self.repository.find_idempotency(record.case_id, record.idempotency_key)
        if duplicate:
            return duplicate, True
        record.audit.append({'event': 'submitted', 'at': _now(self.clock())})
        return self.repository.create(record), False

    def _save(self, record, from_state, event, now=None):
        if record.state not in _ALLOWED.get(from_state, ()):
            raise TaskStateError('illegal_transition:%s->%s' % (from_state, record.state))
        record.version += 1
        record.updated_at = _now(now if now is not None else self.clock())
        record.audit.append({'event': event, 'from': from_state, 'to': record.state, 'at': record.updated_at})
        return self.repository.update(record, record.version - 1)

    def claim(self, task_id, worker_id='worker', now=None):
        now = _now(now if now is not None else self.clock())
        record = self.repository.get(task_id)
        if record is None:
            raise TaskConflictError('task_not_found')
        if record.state == RETRY_WAIT and record.next_retry_at is not None and record.next_retry_at <= now:
            prior = record.state; record.state = QUEUED; record.next_retry_at = None; record = self._save(record, prior, 'retry_due', now)
        if record.state != QUEUED:
            raise TaskConflictError('task_not_claimable:%s' % record.state)
        failed = [dep for dep in record.dependencies if not self.repository.get(dep) or self.repository.get(dep).state != SUCCEEDED]
        if failed:
            prior = record.state; record.state = BLOCKED; record.blocked_by = failed; record.ended_at = now
            return self._save(record, prior, 'dependency_blocked', now)
        prior = record.state
        record.state = RUNNING; record.attempt += 1; record.started_at = record.started_at or now; record.heartbeat_at = now
        record.audit.append({'worker_id': worker_id})
        return self._save(record, prior, 'claimed', now)

    def heartbeat(self, task_id, now=None):
        record = self.repository.get(task_id)
        if record is None or record.state != RUNNING: raise TaskConflictError('task_not_running')
        record.heartbeat_at = _now(now if now is not None else self.clock()); record.version += 1
        record.audit.append({'event': 'heartbeat', 'at': record.heartbeat_at})
        return self.repository.update(record, record.version - 1)

    def succeed(self, task_id, receipt=None, output_ref=None, now=None):
        record = self.repository.get(task_id)
        if record is None: raise TaskConflictError('task_not_found')
        prior = record.state
        record.state = SUCCEEDED; record.execution_receipt = receipt; record.output_ref = output_ref; record.ended_at = _now(now if now is not None else self.clock()); record.reconciliation_required = False
        return self._save(record, prior, 'succeeded', record.ended_at)

    def fail(self, task_id, error_code, error_class='runtime', summary=None, receipt=None, now=None):
        now = _now(now if now is not None else self.clock())
        record = self.repository.get(task_id)
        if record is None: raise TaskConflictError('task_not_found')
        prior = record.state
        record.error_code = error_code; record.error_class = error_class; record.safe_error_summary = _safe_summary(summary or error_code); record.execution_receipt = receipt or record.execution_receipt
        retryable = self._is_retryable(error_code, error_class)
        if retryable and record.attempt < record.max_attempts:
            record.state = RETRY_WAIT; record.next_retry_at = now + self._delay(record)
            return self._save(record, prior, 'retry_scheduled', now)
        record.state = FAILED; record.ended_at = now
        return self._save(record, prior, 'dead_letter_handoff', now)

    def unknown(self, task_id, summary='downstream outcome unknown', now=None):
        record = self.repository.get(task_id)
        if record is None: raise TaskConflictError('task_not_found')
        prior = record.state; record.state = UNKNOWN; record.reconciliation_required = True; record.safe_error_summary = _safe_summary(summary)
        return self._save(record, prior, 'reconciliation_required', now)

    def reconcile(self, task_id, receipt=None, outcome='succeeded', now=None):
        record = self.repository.get(task_id)
        if record is None or record.state != UNKNOWN: raise TaskConflictError('task_not_unknown')
        if outcome == 'succeeded':
            record.reconciliation_required = False
            return self.succeed(task_id, receipt=receipt, now=now)
        if outcome == 'failed':
            record.reconciliation_required = False
            return self.fail(task_id, 'reconciliation_failed', 'runtime', receipt=receipt, now=now)
        raise TaskStateError('invalid_reconciliation_outcome')

    def cancel(self, task_id, now=None):
        record = self.repository.get(task_id)
        if record is None: raise TaskConflictError('task_not_found')
        if record.state in TERMINAL_STATES:
            return record
        prior = record.state; record.state = CANCELLED; record.ended_at = _now(now if now is not None else self.clock())
        return self._save(record, prior, 'cancelled', record.ended_at)

    def resume(self, task_id, now=None):
        record = self.repository.get(task_id)
        if record is None or record.state not in (FAILED, BLOCKED, CANCELLED): raise TaskConflictError('task_not_resumable')
        prior = record.state; record.state = QUEUED; record.blocked_by = []; record.error_code = None; record.error_class = None; record.safe_error_summary = None; record.ended_at = None; record.reconciliation_required = False
        # Human-reviewed resume is an explicit operator action and is the only
        # legal way to leave a terminal/handoff state.
        record.version += 1; record.updated_at = _now(now if now is not None else self.clock())
        record.audit.append({'event': 'resumed', 'from': prior, 'to': record.state, 'at': record.updated_at})
        return self.repository.update(record, record.version - 1)

    def recover_stale_running(self, heartbeat_timeout_seconds, now=None):
        now = _now(now if now is not None else self.clock()); recovered = []
        for record in self.repository.list():
            if record.state == RUNNING and (record.heartbeat_at is None or record.heartbeat_at + heartbeat_timeout_seconds < now):
                recovered.append(self.unknown(record.task_id, 'worker heartbeat expired', now))
        return recovered

    def status_snapshot(self, task_id):
        record = self.repository.get(task_id)
        if record is None: return None
        data = record.to_dict(); data['contract'] = TASK_STATUS_CONTRACT
        return data

    def execute_once(self, task_id, executor, worker_id='worker', receipt_lookup=None, now=None):
        record = self.claim(task_id, worker_id=worker_id, now=now)
        try:
            result = executor(record.to_dict())
        except Exception as exc:
            return self.fail(task_id, 'executor_exception', 'runtime', summary=exc, now=now)
        result = dict(result or {})
        if result.get('timed_out'):
            receipt = receipt_lookup(record.to_dict()) if receipt_lookup else None
            if receipt is not None:
                return self.succeed(task_id, receipt=receipt, output_ref=result.get('output_ref'), now=now)
            if result.get('non_queryable_write'):
                return self.unknown(task_id, 'timeout on non-queryable external write', now=now)
            return self.fail(task_id, 'timeout', 'transient', summary='downstream timeout', now=now)
        if result.get('status') == 'ok':
            return self.succeed(task_id, receipt=result.get('receipt'), output_ref=result.get('output_ref'), now=now)
        return self.fail(task_id, result.get('error_code') or 'executor_failed', result.get('error_class') or 'runtime', result.get('safe_error_summary'), receipt=result.get('receipt'), now=now)

    def _is_retryable(self, code, error_class):
        text = ('%s %s' % (code or '', error_class or '')).lower()
        if any(token in text for token in _NON_RETRYABLE): return False
        return any(token in text for token in _TRANSIENT)

    def _delay(self, record):
        policy = record.retry_policy; base = float(policy.get('base_seconds', 1)); maximum = float(policy.get('max_seconds', 60)); jitter = float(policy.get('jitter', 0))
        delay = min(maximum, base * (2 ** max(0, record.attempt - 1)))
        return delay + (self.randomizer() * jitter if jitter else 0)


__all__ = ['TASK_RECORD_CONTRACT', 'TASK_STATUS_CONTRACT', 'TaskRecord', 'TaskRepository', 'InMemoryTaskRepository', 'SQLiteTaskRepository', 'DurableTaskControlPlane', 'TaskStateError', 'TaskConflictError', 'QUEUED', 'RUNNING', 'RETRY_WAIT', 'SUCCEEDED', 'FAILED', 'BLOCKED', 'CANCELLED', 'UNKNOWN']
