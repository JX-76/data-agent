"""Streaming SSE: Server-Sent Events streaming support.

Provides streaming output for real-time responses.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Iterator

import structlog

logger = structlog.get_logger("streaming_sse")


@dataclass
class StreamEvent:
    """A single SSE event."""
    event: str
    data: dict[str, Any]
    id: str | None = None
    retry: int | None = None
    
    def to_sse(self) -> str:
        """Convert to SSE format."""
        lines = []
        if self.id:
            lines.append(f"id: {self.id}")
        if self.event:
            lines.append(f"event: {self.event}")
        if self.retry is not None:
            lines.append(f"retry: {self.retry}")
        
        data_str = json.dumps(self.data, ensure_ascii=False)
        for line in data_str.split("\n"):
            lines.append(f"data: {line}")
        
        lines.append("")
        return "\n".join(lines)


class StreamingSSE:
    """Server-Sent Events streaming handler."""
    
    def __init__(self):
        self._events: list[StreamEvent] = []
        self._counter = 0
    
    def emit(self, event: str, data: dict[str, Any], id: str | None = None) -> StreamEvent:
        """Emit a streaming event.
        
        Args:
            event: Event type
            data: Event data
            id: Optional event ID
        
        Returns:
            Stream event
        """
        self._counter += 1
        event_id = id or f"event-{self._counter}"
        
        stream_event = StreamEvent(
            event=event,
            data=data,
            id=event_id,
        )
        
        self._events.append(stream_event)
        logger.debug("sse_event_emitted", event_type=event, event_id=event_id)
        
        return stream_event
    
    def emit_plan(self, plan: dict[str, Any]) -> StreamEvent:
        """Emit plan event.
        
        Args:
            plan: Execution plan
        
        Returns:
            Stream event
        """
        return self.emit("plan", {
            "type": "plan",
            "content": plan,
            "timestamp": time.time(),
        })
    
    def emit_sql(self, sql: str, step: int) -> StreamEvent:
        """Emit SQL event.
        
        Args:
            sql: SQL query
            step: Step number
        
        Returns:
            Stream event
        """
        return self.emit("sql", {
            "type": "sql",
            "content": sql,
            "step": step,
            "timestamp": time.time(),
        })
    
    def emit_result(self, result: Any, step: int) -> StreamEvent:
        """Emit result event.
        
        Args:
            result: Query result
            step: Step number
        
        Returns:
            Stream event
        """
        return self.emit("result", {
            "type": "result",
            "content": result,
            "step": step,
            "timestamp": time.time(),
        })
    
    def emit_insight(self, insight: str) -> StreamEvent:
        """Emit insight event.
        
        Args:
            insight: Insight text
        
        Returns:
            Stream event
        """
        return self.emit("insight", {
            "type": "insight",
            "content": insight,
            "timestamp": time.time(),
        })
    
    def emit_error(self, error: str, step: int | None = None) -> StreamEvent:
        """Emit error event.
        
        Args:
            error: Error message
            step: Optional step number
        
        Returns:
            Stream event
        """
        data = {
            "type": "error",
            "content": error,
            "timestamp": time.time(),
        }
        if step is not None:
            data["step"] = step
        
        return self.emit("error", data)
    
    def emit_complete(self, final_result: dict[str, Any]) -> StreamEvent:
        """Emit completion event.
        
        Args:
            final_result: Final result
        
        Returns:
            Stream event
        """
        return self.emit("complete", {
            "type": "complete",
            "content": final_result,
            "timestamp": time.time(),
        })
    
    def get_events(self) -> list[StreamEvent]:
        """Get all emitted events.
        
        Returns:
            List of events
        """
        return self._events
    
    def to_sse_stream(self) -> Iterator[str]:
        """Generate SSE stream.
        
        Yields:
            SSE formatted strings
        """
        for event in self._events:
            yield event.to_sse()
    
    def to_jsonl(self) -> str:
        """Convert to JSONL format.
        
        Returns:
            JSONL string
        """
        lines = []
        for event in self._events:
            lines.append(json.dumps({
                "event": event.event,
                "data": event.data,
                "id": event.id,
            }, ensure_ascii=False))
        return "\n".join(lines)


def create_streaming_sse() -> StreamingSSE:
    """Convenience function to create streaming SSE handler.
    
    Returns:
        Streaming SSE handler
    """
    return StreamingSSE()
