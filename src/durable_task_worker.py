# -*- coding: utf-8 -*-
"""Minimal durable-task worker adapter.

The task state, idempotency and retry decisions remain in
``DurableTaskControlPlane``.  This adapter only supplies a local threaded worker
for development/test use; production queue/worker implementations must claim
and execute the same persisted TaskRecord contract.
"""
from __future__ import unicode_literals

import threading
import time

from durable_task_control_plane import DurableTaskControlPlane, QUEUED, RETRY_WAIT

DURABLE_TASK_WORKER_CONTRACT = 'durable_task_worker_v1'


class DurableTaskWorker(object):
    def __init__(self, control_plane=None, poll_interval_seconds=0.05,
                 heartbeat_timeout_seconds=60.0, clock=None):
        self.control_plane = control_plane or DurableTaskControlPlane()
        self.poll_interval_seconds = max(0.01, float(poll_interval_seconds or 0.05))
        self.heartbeat_timeout_seconds = float(heartbeat_timeout_seconds or 60.0)
        self.clock = clock or time.time
        self._executors = {}
        self._receipt_lookups = {}
        self._lock = threading.RLock()
        self._thread = None
        self._running = False

    def submit(self, task_data, executor, receipt_lookup=None):
        """Persist before scheduling. Duplicate idempotency keys reuse task ID.

        An executor is deliberately process-local.  On restart, a production
        worker must register the task type again and reconciliation remains safe
        because no unregistered task is executed implicitly.
        """
        record, duplicate = self.control_plane.submit(task_data)
        # Executor outcome may be translated by legacy/server adapters.  Keep
        # caller-supplied durable metadata (notably requester_scope) on the
        # persisted record regardless of that translation.
        requested_scope = dict((task_data or {}).get('requester_scope') or {})
        if requested_scope and record.requester_scope != requested_scope:
            record = self.control_plane.annotate(
                record.task_id, {'requester_scope': requested_scope},
                event='requester_scope_bound')
        with self._lock:
            if not duplicate:
                self._executors[record.task_id] = executor
                self._receipt_lookups[record.task_id] = receipt_lookup
        return record, duplicate

    def status(self, task_id):
        snapshot = self.control_plane.status_snapshot(task_id)
        if snapshot is not None:
            snapshot['worker_contract'] = DURABLE_TASK_WORKER_CONTRACT
        return snapshot

    def run_once(self):
        """Execute at most one claimable persisted task, returning its snapshot."""
        now = self.clock()
        self.control_plane.recover_stale_running(self.heartbeat_timeout_seconds, now=now)
        candidates = self.control_plane.repository.list()
        for record in candidates:
            if record.state == RETRY_WAIT and record.next_retry_at is not None and record.next_retry_at > now:
                continue
            if record.state not in (QUEUED, RETRY_WAIT):
                continue
            with self._lock:
                executor = self._executors.get(record.task_id)
                receipt_lookup = self._receipt_lookups.get(record.task_id)
            if executor is None:
                # Persisted task is retained for human/operator handoff rather
                # than being treated as a failed implicit execution.
                continue
            try:
                outcome = self.control_plane.execute_once(
                    record.task_id, executor, worker_id='local_durable_worker',
                    receipt_lookup=receipt_lookup, now=now)
            except Exception:
                # Optimistic-lock races are normal with multi-worker adapters;
                # polling continues and state is read from repository next tick.
                continue
            return outcome.to_dict()
        return None

    def _loop(self):
        while self._running:
            self.run_once()
            time.sleep(self.poll_interval_seconds)

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, name='durable-task-worker')
        self._thread.daemon = True
        self._thread.start()

    def stop(self, join_timeout_seconds=1.0):
        self._running = False
        if self._thread is not None:
            self._thread.join(float(join_timeout_seconds or 1.0))
            self._thread = None


__all__ = ['DURABLE_TASK_WORKER_CONTRACT', 'DurableTaskWorker']
