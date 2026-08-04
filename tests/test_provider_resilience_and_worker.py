# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
SRC = os.path.join(ROOT, 'src')
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from durable_task_control_plane import DurableTaskControlPlane, RETRY_WAIT, SUCCEEDED
from durable_task_worker import DurableTaskWorker
from provider_resilience import ProviderResilience


def _task(**updates):
    value = {'task_id': 'provider-task', 'case_id': 'provider-case',
             'task_type': 'presentation_assist', 'worker_type': 'provider',
             'input_ref': {'query': 'gmv'}, 'idempotency_key': 'provider-idem',
             'max_attempts': 2}
    value.update(updates)
    return value


class _ProviderFailure(Exception):
    def __init__(self, code):
        Exception.__init__(self, code)
        self.code = code


def test_provider_resilience_retries_only_transient_and_keeps_safe_observation():
    attempts = []
    sleeps = []

    def flaky():
        attempts.append(1)
        if len(attempts) < 3:
            raise _ProviderFailure('rate_limited')
        return 'ok'

    policy = ProviderResilience('fixture', max_attempts=3, base_delay_seconds=1,
                                max_delay_seconds=5, jitter_seconds=0,
                                failure_threshold=5, sleep=sleeps.append,
                                clock=lambda: 1)
    assert policy.call(flaky) == 'ok'
    assert len(attempts) == 3
    assert sleeps == [1.0, 2.0]
    assert policy.last_observation['outcome'] == 'succeeded'
    assert policy.last_observation['attempt_count'] == 3
    assert 'fixture' == policy.last_observation['provider']

    denied_attempts = []
    def denied():
        denied_attempts.append(1)
        raise _ProviderFailure('auth_failed')
    policy = ProviderResilience('fixture', max_attempts=3, sleep=lambda _: None)
    try:
        policy.call(denied)
        assert False, 'non-retryable error must propagate'
    except _ProviderFailure:
        pass
    assert len(denied_attempts) == 1
    assert policy.last_observation['retryable'] is False


def test_durable_task_worker_persists_before_execution_and_reuses_idempotency_key():
    now = [10]
    control = DurableTaskControlPlane(clock=lambda: now[0], randomizer=lambda: 0)
    worker = DurableTaskWorker(control, clock=lambda: now[0])
    side_effects = []

    def executor(record):
        side_effects.append(record['idempotency_key'])
        return {'status': 'ok', 'receipt': {'receipt_id': 'safe-receipt'}}

    first, duplicate = worker.submit(_task(), executor)
    same, duplicate_again = worker.submit(_task(task_id='another-id'), executor)
    assert duplicate is False and duplicate_again is True
    assert same.task_id == first.task_id
    assert worker.status(first.task_id)['state'] == 'queued'
    outcome = worker.run_once()
    assert outcome['state'] == SUCCEEDED
    assert side_effects == ['provider-idem']
    assert worker.status(first.task_id)['execution_receipt']['receipt_id'] == 'safe-receipt'


def test_durable_task_worker_defers_retry_until_persisted_retry_time():
    now = [10]
    control = DurableTaskControlPlane(clock=lambda: now[0], randomizer=lambda: 0)
    worker = DurableTaskWorker(control, clock=lambda: now[0])
    calls = []

    def failing(_):
        calls.append(1)
        return {'status': 'error', 'error_code': '503', 'error_class': 'transient'}

    task, _ = worker.submit(_task(retry_policy={'base_seconds': 2, 'max_seconds': 4, 'jitter': 0}), failing)
    assert worker.run_once()['state'] == RETRY_WAIT
    assert calls == [1]
    assert worker.run_once() is None
    now[0] = 12
    assert worker.run_once()['state'] == 'failed'
    assert calls == [1, 1]
