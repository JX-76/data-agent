"""SSE Integration: Streaming SSE integration with DAG.

Integrates StreamingSSE with the DAG execution for real-time output.
"""

from __future__ import annotations

from typing import Any

from streaming_sse import StreamingSSE


class SSEIntegration:
    """Integrates SSE streaming with DAG execution."""
    
    def __init__(self):
        self.sse = StreamingSSE()
    
    def emit_plan(self, plan: dict[str, Any]) -> None:
        """Emit plan event.
        
        Args:
            plan: Execution plan
        """
        self.sse.emit_plan(plan)
    
    def emit_sql(self, sql: str, step: int) -> None:
        """Emit SQL event.
        
        Args:
            sql: SQL query
            step: Step number
        """
        self.sse.emit_sql(sql, step)
    
    def emit_result(self, result: Any, step: int) -> None:
        """Emit result event.
        
        Args:
            result: Query result
            step: Step number
        """
        self.sse.emit_result(result, step)
    
    def emit_insight(self, insight: str) -> None:
        """Emit insight event.
        
        Args:
            insight: Insight text
        """
        self.sse.emit_insight(insight)
    
    def emit_error(self, error: str, step: int | None = None) -> None:
        """Emit error event.
        
        Args:
            error: Error message
            step: Optional step number
        """
        self.sse.emit_error(error, step)
    
    def emit_complete(self, final_result: dict[str, Any]) -> None:
        """Emit completion event.
        
        Args:
            final_result: Final result
        """
        self.sse.emit_complete(final_result)
    
    def get_events(self) -> list:
        """Get all emitted events.
        
        Returns:
            List of events
        """
        return self.sse.get_events()
    
    def to_sse_stream(self):
        """Generate SSE stream.
        
        Yields:
            SSE formatted strings
        """
        return self.sse.to_sse_stream()
    
    def to_jsonl(self) -> str:
        """Convert to JSONL format.
        
        Returns:
            JSONL string
        """
        return self.sse.to_jsonl()


def create_sse_integration() -> SSEIntegration:
    """Convenience function to create SSE integration.
    
    Returns:
        SSE integration
    """
    return SSEIntegration()
