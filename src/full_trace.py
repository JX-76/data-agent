"""Full Trace: Full链路 trace with agent metrics and attribution.

Integrates tracing, metrics, and attribution analysis.
"""

from __future__ import annotations

import uuid
import time
from typing import Any

from tracer import LangfuseTracer
from agent_metrics import AgentMetricsCollector, AttributionAnalyzer


class FullTrace:
    """Full trace with metrics and attribution."""
    
    def __init__(self, tracer: LangfuseTracer | None = None):
        self.tracer = tracer or LangfuseTracer()
        self.metrics = AgentMetricsCollector()
        self.attribution_analyzer = AttributionAnalyzer()
        self._trace_id: str | None = None
        self._start_time: float = 0.0
        self._steps: list[dict[str, Any]] = []
    
    def start_trace(self, query: str, graph_name: str = "data_agent") -> str:
        """Start a new trace.
        
        Args:
            query: User query
            graph_name: Graph name
        
        Returns:
            Trace ID
        """
        self._trace_id = str(uuid.uuid4())
        self._start_time = time.time()
        self._steps = []
        
        # Start Langfuse trace
        self.tracer.on_trace_start(self._trace_id, graph_name, {"query": query})
        
        # Record metrics
        self.metrics.record_task_start(self._trace_id)
        
        return self._trace_id
    
    def add_step(self, node_name: str, state: dict[str, Any], step: int) -> None:
        """Add a step to the trace.
        
        Args:
            node_name: Node name
            state: Execution state
            step: Step number
        """
        if not self._trace_id:
            return
        
        # Start node span
        self.tracer.on_node_start(self._trace_id, node_name, state, step)
        
        # Record step
        self._steps.append({
            "node": node_name,
            "step": step,
            "state": state,
        })
    
    def end_step(self, node_name: str, state: dict[str, Any], step: int, status: str) -> None:
        """End a step.
        
        Args:
            node_name: Node name
            state: Execution state
            step: Step number
            status: Step status
        """
        if not self._trace_id:
            return
        
        # End node span
        self.tracer.on_node_end(self._trace_id, node_name, state, step, status)
        
        # Record tool call
        if status == "success":
            self.metrics.record_tool_call(node_name, True, 0.0)
        else:
            self.metrics.record_tool_call(node_name, False, 0.0)
    
    def end_trace(self, final_state: dict[str, Any], success: bool) -> dict[str, Any]:
        """End the trace.
        
        Args:
            final_state: Final state
            success: Whether task succeeded
        
        Returns:
            Trace result
        """
        if not self._trace_id:
            return {}
        
        # Calculate latency
        latency_ms = (time.time() - self._start_time) * 1000
        
        # End Langfuse trace
        self.tracer.on_trace_end(self._trace_id, final_state)
        
        # Record metrics
        self.metrics.record_task_end(self._trace_id, success, latency_ms, len(self._steps))
        
        # Analyze attribution
        attribution = self.attribution_analyzer.analyze(self._trace_id, self._steps, success)
        self.metrics.add_attribution(attribution)
        
        # Build result
        result = {
            "trace_id": self._trace_id,
            "success": success,
            "latency_ms": latency_ms,
            "steps": len(self._steps),
            "metrics": self.metrics.get_summary(),
            "attribution": attribution.analyze(),
        }
        
        return result
    
    def get_trace_id(self) -> str | None:
        """Get trace ID.
        
        Returns:
            Trace ID or None
        """
        return self._trace_id
    
    def get_metrics(self) -> dict[str, Any]:
        """Get metrics.
        
        Returns:
            Metrics summary
        """
        return self.metrics.get_summary()
    
    def get_attributions(self) -> list:
        """Get attributions.
        
        Returns:
            List of attributions
        """
        return self.metrics.get_attributions()


def create_full_trace(tracer: LangfuseTracer | None = None) -> FullTrace:
    """Convenience function to create full trace.
    
    Args:
        tracer: Optional tracer
    
    Returns:
        Full trace
    """
    return FullTrace(tracer)
