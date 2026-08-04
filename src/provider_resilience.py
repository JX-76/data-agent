# -*- coding: utf-8 -*-
"""Bounded, observable provider-call resilience primitives.

This module is intentionally dependency-free and Python 2.7 compatible.  It is
for idempotent provider reads/generation requests only: callers of external
writes must use durable-task receipt reconciliation rather than automatic retry.
"""
from __future__ import unicode_literals

import random
import time

from circuit_breaker import CircuitBreaker, CircuitBreakerOpenError

PROVIDER_RESILIENCE_CONTRACT = 'provider_resilience_v1'

_TRANSIENT = set(['timeout', 'unavailable', 'connection_error',
                  'provider_runtime_error', 'rate_limited', 'http_429',
                  'http_500', 'http_502', 'http_503', 'http_504'])
_NON_RETRYABLE = set(['missing_api_key', 'auth_failed', 'invalid_response',
                      'unsafe_response', 'empty_response', 'validation',
                      'schema', 'permission', 'policy'])


class ProviderCallError(Exception):
    """Safe normalized error carrying only a stable error category."""
    def __init__(self, code, message):
        Exception.__init__(self, message)
        self.code = code
        self.message = message


def is_retryable_code(code):
    value = str(code or '').lower()
    if value in _NON_RETRYABLE:
        return False
    return value in _TRANSIENT


class ProviderResilience(object):
    """Execute an idempotent provider call with bounded retry and circuit break.

    ``sleep`` and ``randomizer`` are injectable so all resilience behavior is
    deterministic in tests.  The returned observation deliberately omits request
    bodies, responses and credentials.
    """
    def __init__(self, provider_name, max_attempts=3, base_delay_seconds=0.25,
                 max_delay_seconds=2.0, jitter_seconds=0.1,
                 failure_threshold=3, recovery_timeout_seconds=15.0,
                 clock=None, sleep=None, randomizer=None, breaker=None):
        self.provider_name = provider_name or 'provider'
        self.max_attempts = max(1, int(max_attempts or 1))
        self.base_delay_seconds = max(0.0, float(base_delay_seconds or 0))
        self.max_delay_seconds = max(self.base_delay_seconds, float(max_delay_seconds or 0))
        self.jitter_seconds = max(0.0, float(jitter_seconds or 0))
        self.clock = clock or time.time
        self.sleep = sleep or time.sleep
        self.randomizer = randomizer or random.random
        self.breaker = breaker or CircuitBreaker(
            name='provider:%s' % self.provider_name,
            failure_threshold=max(1, int(failure_threshold or 1)),
            recovery_timeout=float(recovery_timeout_seconds or 0),
            half_open_max_calls=1,
            success_threshold=1)
        self.last_observation = None

    def _delay(self, attempt):
        base = min(self.max_delay_seconds,
                   self.base_delay_seconds * (2 ** max(0, int(attempt) - 1)))
        return base + (self.randomizer() * self.jitter_seconds if self.jitter_seconds else 0.0)

    def _observation(self, started, attempts, outcome, error_code=None, delays=None):
        return {
            'contract': PROVIDER_RESILIENCE_CONTRACT,
            'provider': self.provider_name,
            'attempt_count': int(attempts),
            'max_attempts': self.max_attempts,
            'outcome': outcome,
            'error_code': error_code,
            'retryable': bool(error_code and is_retryable_code(error_code)),
            'scheduled_delay_seconds': list(delays or []),
            'elapsed_ms': int(max(0, (self.clock() - started) * 1000)),
            'circuit': self.breaker.stats,
        }

    def call(self, fn, error_code_getter=None):
        """Run ``fn``; retries only normalized transient failures.

        ``error_code_getter(exception)`` must return a stable category.  The
        caller's exception type and safe message are preserved on terminal
        failure, while ``last_observation`` supplies the audit-safe trace.
        """
        started = self.clock()
        delays = []
        attempts = 0
        for attempts in range(1, self.max_attempts + 1):
            try:
                value = self.breaker.call(fn)
                self.last_observation = self._observation(started, attempts, 'succeeded', delays=delays)
                return value
            except CircuitBreakerOpenError:
                self.last_observation = self._observation(started, attempts - 1, 'circuit_open', 'circuit_open', delays)
                raise ProviderCallError('circuit_open', 'Provider circuit is open; retry later.')
            except Exception as exc:
                code = error_code_getter(exc) if error_code_getter else getattr(exc, 'code', None)
                code = str(code or 'provider_runtime_error').lower()
                if not is_retryable_code(code) or attempts >= self.max_attempts:
                    self.last_observation = self._observation(started, attempts, 'failed', code, delays)
                    raise
                delay = self._delay(attempts)
                delays.append(delay)
                self.sleep(delay)
        # Defensive fallback; the loop always returns or raises.
        self.last_observation = self._observation(started, attempts, 'failed', 'provider_runtime_error', delays)
        raise ProviderCallError('provider_runtime_error', 'Provider call failed.')


__all__ = ['PROVIDER_RESILIENCE_CONTRACT', 'ProviderCallError',
           'ProviderResilience', 'is_retryable_code']
