# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'src')
if SRC not in sys.path: sys.path.insert(0, SRC)

from durable_task_control_plane import (DurableTaskControlPlane, InMemoryTaskRepository,
    SQLiteTaskRepository, TaskRecord, TaskConflictError, TaskStateError, QUEUED, RUNNING,
    RETRY_WAIT, SUCCEEDED, FAILED, BLOCKED, CANCELLED, UNKNOWN)


def _task(**updates):
    data = {'task_id': 'task-a', 'case_id': 'case-a', 'task_type': 'query', 'worker_type': 'data',
            'input_ref': {'metric': 'gmv'}, 'idempotency_key': 'idem-a', 'max_attempts': 3,
            'retry_policy': {'base_seconds': 2, 'max_seconds': 20, 'jitter': 0}, 'trace_id': 'trace-a', 'session_id': 'session-a'}
    data.update(updates)
    return data


def test_task_record_snapshot_and_idempotent_submit():
    cp = DurableTaskControlPlane(clock=lambda: 10)
    first, duplicate = cp.submit(_task())
    again, duplicate_again = cp.submit(_task(task_id='different-task'))
    assert duplicate is False and duplicate_again is True
    assert again.task_id == first.task_id
    snapshot = cp.status_snapshot(first.task_id)
    assert snapshot['contract'] == 'durable_task_status_v1'
    assert snapshot['trace_id'] == 'trace-a' and snapshot['state'] == QUEUED
    assert snapshot['input_hash'] and snapshot['schema_version'] == 1


def test_optimistic_claim_allows_only_one_worker_and_illegal_transition_is_rejected():
    cp = DurableTaskControlPlane(clock=lambda: 10)
    task, ignored = cp.submit(_task())
    assert cp.claim(task.task_id, worker_id='w1', now=11).state == RUNNING
    try:
        cp.claim(task.task_id, worker_id='w2', now=11)
        raised = False
    except TaskConflictError:
        raised = True
    assert raised is True
    try:
        cp.succeed(task.task_id, now=12)
        cp.fail(task.task_id, 'timeout', 'transient', now=13)
        raised = False
    except TaskStateError:
        raised = True
    assert raised is True


def test_transient_retry_backoff_and_nonretryable_schema_permission_fail_closed():
    cp = DurableTaskControlPlane(clock=lambda: 10, randomizer=lambda: 0)
    transient, ignored = cp.submit(_task())
    cp.claim(transient.task_id, now=10)
    waiting = cp.fail(transient.task_id, '503', 'transient', now=10)
    assert waiting.state == RETRY_WAIT and waiting.next_retry_at == 12
    assert cp.claim(transient.task_id, now=12).state == RUNNING
    cp.fail(transient.task_id, '429', 'rate_limit', now=12)
    assert cp.repository.get(transient.task_id).state == RETRY_WAIT

    for name, error_class in [('schema', 'schema'), ('permission', 'permission'), ('policy', 'policy')]:
        task, ignored = cp.submit(_task(task_id='task-' + name, idempotency_key='idem-' + name))
        cp.claim(task.task_id, now=20)
        assert cp.fail(task.task_id, name, error_class, now=20).state == FAILED


def test_retry_exhaustion_dead_letters_and_safe_error_is_bounded():
    cp = DurableTaskControlPlane(clock=lambda: 1)
    task, ignored = cp.submit(_task(max_attempts=1))
    cp.claim(task.task_id, now=1)
    failed = cp.fail(task.task_id, '503', 'transient', summary='x' * 1000, now=1)
    assert failed.state == FAILED and len(failed.safe_error_summary) == 300
    assert failed.audit[-1]['event'] == 'dead_letter_handoff'


def test_timeout_reconciles_receipt_or_marks_nonqueryable_write_unknown():
    cp = DurableTaskControlPlane(clock=lambda: 1)
    success, ignored = cp.submit(_task())
    outcome = cp.execute_once(success.task_id, lambda _: {'timed_out': True}, receipt_lookup=lambda _: {'receipt_id': 'r1'}, now=1)
    assert outcome.state == SUCCEEDED and outcome.execution_receipt['receipt_id'] == 'r1'

    unsafe, ignored = cp.submit(_task(task_id='task-write', idempotency_key='idem-write'))
    outcome = cp.execute_once(unsafe.task_id, lambda _: {'timed_out': True, 'non_queryable_write': True}, now=1)
    assert outcome.state == UNKNOWN and outcome.reconciliation_required is True
    assert cp.reconcile(unsafe.task_id, receipt={'receipt_id': 'manual'}, outcome='succeeded', now=2).state == SUCCEEDED


def test_cancel_resume_and_stale_worker_recovery():
    cp = DurableTaskControlPlane(clock=lambda: 0)
    cancelled, ignored = cp.submit(_task())
    assert cp.cancel(cancelled.task_id, now=1).state == CANCELLED
    assert cp.resume(cancelled.task_id, now=2).state == QUEUED

    stale, ignored = cp.submit(_task(task_id='task-stale', idempotency_key='idem-stale'))
    cp.claim(stale.task_id, now=2)
    recovered = cp.recover_stale_running(heartbeat_timeout_seconds=5, now=8)
    assert [item.task_id for item in recovered] == [stale.task_id]
    assert cp.repository.get(stale.task_id).state == UNKNOWN


def test_failed_dependency_is_blocked_and_never_runs():
    cp = DurableTaskControlPlane(clock=lambda: 1)
    parent, ignored = cp.submit(_task(task_id='parent', idempotency_key='parent', max_attempts=1))
    child, ignored = cp.submit(_task(task_id='child', idempotency_key='child', dependencies=['parent']))
    cp.claim(parent.task_id, now=1)
    cp.fail(parent.task_id, 'schema_invalid', 'schema', now=1)
    blocked = cp.claim(child.task_id, now=2)
    assert blocked.state == BLOCKED and blocked.blocked_by == ['parent']


def test_sqlite_survives_new_control_plane_instance():
    fd, path = tempfile.mkstemp(suffix='.sqlite'); os.close(fd)
    try:
        first = DurableTaskControlPlane(SQLiteTaskRepository(path), clock=lambda: 1)
        task, ignored = first.submit(_task())
        first.claim(task.task_id, now=1)
        first.fail(task.task_id, '503', 'transient', now=1)
        second = DurableTaskControlPlane(SQLiteTaskRepository(path), clock=lambda: 3)
        assert second.status_snapshot(task.task_id)['state'] == RETRY_WAIT
        assert second.claim(task.task_id, now=3).state == RUNNING
        first.repository.close()
        second.repository.close()
    finally:
        if os.path.exists(path): os.remove(path)
