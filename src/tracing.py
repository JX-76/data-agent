"""Distributed tracing with OpenTelemetry support.

Provides request tracing across service boundaries with:
- Trace ID propagation
- Span timing
- Baggage support
- Integration with Langfuse for LLM tracing
"""

from __future__ import annotations

import time
import uuid
from contextlib import contextmanager
from typing import Optional, Generator


class TraceContext:
    """Holds trace context for a single request."""
    
    def __init__(self, trace_id: Optional[str] = None, parent_id: Optional[str] = None):
        self.trace_id = trace_id or self._generate_id()
        self.parent_id = parent_id
        self.span_id = self._generate_id()
        self.start_time = time.time()
        self.baggage: dict[str, str] = {}
    
    @staticmethod
    def _generate_id() -> str:
        return uuid.uuid4().hex[:16]
    
    def to_headers(self) -> dict[str, str]:
        """Convert trace context to HTTP headers for propagation."""
        headers = {
            "X-Trace-Id": self.trace_id,
            "X-Span-Id": self.span_id,
        }
        if self.parent_id:
            headers["X-Parent-Id"] = self.parent_id
        return headers
    
    @classmethod
    def from_headers(cls, headers: dict[str, str]) -> "TraceContext":
        """Create trace context from HTTP headers."""
        trace_id = headers.get("X-Trace-Id")
        parent_id = headers.get("X-Span-Id")
        return cls(trace_id=trace_id, parent_id=parent_id)
    
    def child(self) -> "TraceContext":
        """Create a child trace context."""
        return TraceContext(trace_id=self.trace_id, parent_id=self.span_id)


class Span:
    """Represents a single operation span."""
    
    def __init__(self, name: str, trace_ctx: TraceContext, tags: Optional[dict] = None):
        self.name = name
        self.trace_ctx = trace_ctx
        self.tags = tags or {}
        self.start_time: Optional[float] = None
        self.end_time: Optional[float] = None
        self.duration_ms: float = 0.0
    
    def __enter__(self):
        self.start_time = time.time()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.end_time = time.time()
        self.duration_ms = (self.end_time - self.start_time) * 1000
    
    def set_tag(self, key: str, value: str):
        self.tags[key] = value
    
    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "trace_id": self.trace_ctx.trace_id,
            "span_id": self.trace_ctx.span_id,
            "parent_id": self.trace_ctx.parent_id,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_ms": self.duration_ms,
            "tags": self.tags,
        }


class Tracer:
    """Simple tracer with span tracking."""
    
    def __init__(self):
        self._spans: list[Span] = []
    
    def start_span(self, name: str, trace_ctx: Optional[TraceContext] = None,
                   tags: Optional[dict] = None) -> Span:
        """Start a new span."""
        ctx = trace_ctx or TraceContext()
        span = Span(name, ctx, tags)
        self._spans.append(span)
        return span
    
    def get_spans(self) -> list[dict]:
        """Get all recorded spans."""
        return [s.to_dict() for s in self._spans]
    
    def clear(self):
        """Clear all recorded spans."""
        self._spans.clear()


# Global tracer instance
tracer = Tracer()
