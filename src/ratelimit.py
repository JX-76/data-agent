"""Rate limiting middleware with sliding window.

Per-IP in-memory rate limiter. No external dependencies.

Usage:
    from ratelimit import RateLimiter, RateLimitMiddleware
    limiter = RateLimiter(max_requests=100, window_seconds=60)
    app.add_middleware(RateLimitMiddleware, limiter=limiter)
"""

from __future__ import annotations

import time
import threading
from collections import defaultdict
from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


class RateLimiter:
    """Sliding window rate limiter.

    Tracks request counts per key (IP) within a time window.
    Thread-safe via lock.

    Args:
        max_requests: Max requests allowed per window
        window_seconds: Time window in seconds
        block_seconds: Seconds to block after exceeding limit (0 = just reject)
    """

    def __init__(
        self,
        max_requests: int = 100,
        window_seconds: int = 60,
        block_seconds: int = 30,
    ):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.block_seconds = block_seconds

        # key → list of timestamps (sliding window)
        self._windows: dict[str, list[float]] = defaultdict(list)
        # key → block_until timestamp
        self._blocked: dict[str, float] = {}
        self._lock = threading.Lock()

    def _cleanup(self):
        """Periodic cleanup of expired entries."""
        now = time.time()
        with self._lock:
            # Cleanup windows
            stale_keys = []
            for key, timestamps in self._windows.items():
                self._windows[key] = [t for t in timestamps if now - t < self.window_seconds]
                if not self._windows[key]:
                    stale_keys.append(key)
            for key in stale_keys:
                del self._windows[key]

            # Cleanup blocks
            expired_blocks = [k for k, v in self._blocked.items() if now > v]
            for k in expired_blocks:
                del self._blocked[k]

    def check(self, key: str) -> tuple[bool, Optional[int]]:
        """Check if request is allowed.

        Returns:
            (allowed, retry_after_seconds)
            - (True, None): allowed
            - (False, seconds): denied, retry after N seconds
        """
        now = time.time()

        with self._lock:
            # Check if blocked
            if key in self._blocked:
                if now < self._blocked[key]:
                    remaining = int(self._blocked[key] - now) + 1
                    return False, remaining
                del self._blocked[key]

            # Sliding window: remove expired timestamps
            self._windows[key] = [t for t in self._windows[key] if now - t < self.window_seconds]

            # Check limit
            if len(self._windows[key]) >= self.max_requests:
                if self.block_seconds > 0:
                    self._blocked[key] = now + self.block_seconds
                return False, self.block_seconds or 1

            # Allow
            self._windows[key].append(now)
            return True, None

    def stats(self, key: Optional[str] = None) -> dict:
        """Get rate limit stats."""
        with self._lock:
            if key:
                return {
                    "key": key,
                    "count": len(self._windows.get(key, [])),
                    "limit": self.max_requests,
                    "blocked": key in self._blocked,
                }
            return {
                "total_keys": len(self._windows),
                "blocked_keys": len(self._blocked),
                "limit": self.max_requests,
                "window_seconds": self.window_seconds,
            }


class RateLimitMiddleware(BaseHTTPMiddleware):
    """FastAPI middleware that applies rate limiting per client IP."""

    def __init__(self, app, limiter: RateLimiter, key_header: Optional[str] = None):
        super().__init__(app)
        self.limiter = limiter
        self.key_header = key_header  # Optional: use custom header instead of IP

    async def dispatch(self, request: Request, call_next):
        # Determine key
        if self.key_header:
            key = request.headers.get(self.key_header, "unknown")
        else:
            # Use X-Forwarded-For if behind proxy, else client IP
            forwarded = request.headers.get("X-Forwarded-For", "")
            if forwarded:
                key = forwarded.split(",")[0].strip()
            else:
                key = request.client.host if request.client else "unknown"

        # Skip health check
        if request.url.path == "/health":
            return await call_next(request)

        allowed, retry_after = self.limiter.check(key)
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={
                    "status": "rate_limited",
                    "retry_after_seconds": retry_after,
                    "limit": self.limiter.max_requests,
                    "window_seconds": self.limiter.window_seconds,
                },
                headers={"Retry-After": str(retry_after)},
            )

        return await call_next(request)
