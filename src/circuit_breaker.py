# -*- coding: utf-8 -*-
"""Circuit breaker pattern for external API calls.

Provides:
- Automatic failure detection
- Circuit state management (closed/open/half-open)
- Recovery timeout
- Health checks

Python 2.7 compatible.
"""

import threading
import time


class CircuitBreakerOpenError(Exception):
    """Raised when circuit breaker is open."""
    pass


class CircuitBreaker(object):
    """Circuit breaker for external API calls.

    Args:
        name: Identifier for this breaker.
        failure_threshold: Number of failures before opening circuit.
        recovery_timeout: Seconds to wait before trying half-open.
        half_open_max_calls: Max calls to allow in half-open state.
        success_threshold: Consecutive successes needed to close circuit.
    """

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

    def __init__(self, name="default",
                 failure_threshold=5,
                 recovery_timeout=30.0,
                 half_open_max_calls=3,
                 success_threshold=2):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_calls = half_open_max_calls
        self.success_threshold = success_threshold

        self._state = self.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time = 0.0
        self._half_open_calls = 0
        self._lock = threading.RLock()

    @property
    def state(self):
        return self._state

    def call(self, fn, *args, **kwargs):
        """Execute a function with circuit breaker protection.

        Returns the function result on success.
        Raises CircuitBreakerOpenError if circuit is open.
        State transitions are protected by a lock; the wrapped function itself
        executes outside the lock to avoid serializing healthy dependencies.
        """
        with self._lock:
            if self._state == self.OPEN:
                if self._should_attempt_reset():
                    self._state = self.HALF_OPEN
                    self._half_open_calls = 0
                    self._success_count = 0
                else:
                    raise CircuitBreakerOpenError(
                        "Circuit %s is OPEN (failures=%d)" % (self.name, self._failure_count)
                    )

            if self._state == self.HALF_OPEN:
                if self._half_open_calls >= self.half_open_max_calls:
                    raise CircuitBreakerOpenError(
                        "Circuit %s half-open limit reached" % self.name
                    )
                self._half_open_calls += 1

        try:
            result = fn(*args, **kwargs)
            with self._lock:
                self._on_success()
            return result
        except Exception:
            with self._lock:
                self._on_failure()
            raise

    def _on_success(self):
        if self._state == self.HALF_OPEN:
            self._success_count += 1
            if self._success_count >= self.success_threshold:
                self._reset()
        else:
            self._failure_count = max(0, self._failure_count - 1)

    def _on_failure(self):
        self._failure_count += 1
        self._last_failure_time = time.time()

        if self._state == self.HALF_OPEN:
            self._state = self.OPEN
        elif self._failure_count >= self.failure_threshold:
            self._state = self.OPEN

    def _should_attempt_reset(self):
        if self._last_failure_time == 0.0:
            return True
        return (time.time() - self._last_failure_time) >= self.recovery_timeout

    def _reset(self):
        self._state = self.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._half_open_calls = 0
        self._last_failure_time = 0.0

    @property
    def stats(self):
        with self._lock:
            return {
                "name": self.name,
                "state": self._state,
                "failure_count": self._failure_count,
                "success_count": self._success_count,
                "last_failure_time": self._last_failure_time,
            }


class CircuitBreakerRegistry(object):
    """Global registry of named circuit breakers."""

    def __init__(self):
        self._breakers = {}

    def get(self, name, **kwargs):
        if name not in self._breakers:
            self._breakers[name] = CircuitBreaker(name=name, **kwargs)
        return self._breakers[name]

    def all_stats(self):
        return {name: cb.stats for name, cb in self._breakers.items()}


# Global instance
_global_registry = None


def get_circuit_breaker_registry():
    global _global_registry
    if _global_registry is None:
        _global_registry = CircuitBreakerRegistry()
    return _global_registry


__all__ = [
    "CircuitBreaker",
    "CircuitBreakerOpenError",
    "CircuitBreakerRegistry",
    "get_circuit_breaker_registry",
]
