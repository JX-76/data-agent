"""Agent Metrics: Agent-level metrics and attribution analysis.

Provides:
1. Agent-level metrics (task completion rate, tool accuracy, etc.)
2. Attribution analysis (which steps contributed to success/failure)
3. Performance tracking (latency, token usage, etc.)
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger("agent_metrics")


@dataclass
class AgentMetrics:
    """Agent-level metrics."""
    total_tasks: int = 0
    successful_tasks: int = 0
    failed_tasks: int = 0
    avg_latency_ms: float = 0.0
    avg_steps: float = 0.0
    tool_accuracy: float = 0.0
    unnecessary_steps: int = 0
    invalid_loops: int = 0
    human_takeover_rate: float = 0.0
    token_usage: int = 0
    
    @property
    def task_completion_rate(self) -> float:
        """Task completion rate."""
        if self.total_tasks == 0:
            return 0.0
        return self.successful_tasks / self.total_tasks
    
    @property
    def failure_rate(self) -> float:
        """Failure rate."""
        if self.total_tasks == 0:
            return 0.0
        return self.failed_tasks / self.total_tasks


@dataclass
class StepAttribution:
    """Attribution for a single step."""
    step: int
    node: str
    contribution: float  # -1.0 to 1.0
    reason: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class AttributionAnalysis:
    """Attribution analysis for a task."""
    task_id: str
    overall_success: bool
    step_attributions: list[StepAttribution] = field(default_factory=list)
    key_contributors: list[str] = field(default_factory=list)
    key_blockers: list[str] = field(default_factory=list)
    
    def analyze(self) -> dict[str, Any]:
        """Analyze attributions.
        
        Returns:
            Analysis result
        """
        positive = [a for a in self.step_attributions if a.contribution > 0]
        negative = [a for a in self.step_attributions if a.contribution < 0]
        
        self.key_contributors = [a.node for a in sorted(positive, key=lambda x: x.contribution, reverse=True)[:3]]
        self.key_blockers = [a.node for a in sorted(negative, key=lambda x: x.contribution)[:3]]
        
        return {
            "task_id": self.task_id,
            "overall_success": self.overall_success,
            "key_contributors": self.key_contributors,
            "key_blockers": self.key_blockers,
            "positive_steps": len(positive),
            "negative_steps": len(negative),
        }


class AgentMetricsCollector:
    """Collects agent-level metrics."""
    
    def __init__(self):
        self.metrics = AgentMetrics()
        self._task_latencies: list[float] = []
        self._task_steps: list[int] = []
        self._tool_calls: list[dict[str, Any]] = []
        self._attributions: list[AttributionAnalysis] = []
    
    def record_task_start(self, task_id: str) -> None:
        """Record task start.
        
        Args:
            task_id: Task ID
        """
        self.metrics.total_tasks += 1
        logger.info("task_started", task_id=task_id)
    
    def record_task_end(self, task_id: str, success: bool, latency_ms: float, steps: int) -> None:
        """Record task end.
        
        Args:
            task_id: Task ID
            success: Whether task succeeded
            latency_ms: Task latency in milliseconds
            steps: Number of steps
        """
        if success:
            self.metrics.successful_tasks += 1
        else:
            self.metrics.failed_tasks += 1
        
        self._task_latencies.append(latency_ms)
        self._task_steps.append(steps)
        
        # Update averages
        self.metrics.avg_latency_ms = sum(self._task_latencies) / len(self._task_latencies)
        self.metrics.avg_steps = sum(self._task_steps) / len(self._task_steps)
        
        logger.info("task_ended",
            task_id=task_id,
            success=success,
            latency_ms=latency_ms,
            steps=steps,
        )
    
    def record_tool_call(self, tool: str, success: bool, latency_ms: float) -> None:
        """Record tool call.
        
        Args:
            tool: Tool name
            success: Whether call succeeded
            latency_ms: Call latency
        """
        self._tool_calls.append({
            "tool": tool,
            "success": success,
            "latency_ms": latency_ms,
        })
        
        # Update tool accuracy
        successful = sum(1 for c in self._tool_calls if c["success"])
        self.metrics.tool_accuracy = successful / len(self._tool_calls)
        
        logger.info("tool_called",
            tool=tool,
            success=success,
            latency_ms=latency_ms,
        )
    
    def record_unnecessary_step(self, step: int, node: str) -> None:
        """Record unnecessary step.
        
        Args:
            step: Step number
            node: Node name
        """
        self.metrics.unnecessary_steps += 1
        logger.warning("unnecessary_step", step=step, node=node)
    
    def record_invalid_loop(self, step: int, node: str) -> None:
        """Record invalid loop.
        
        Args:
            step: Step number
            node: Node name
        """
        self.metrics.invalid_loops += 1
        logger.warning("invalid_loop", step=step, node=node)
    
    def record_human_takeover(self, task_id: str, reason: str) -> None:
        """Record human takeover.
        
        Args:
            task_id: Task ID
            reason: Takeover reason
        """
        self.metrics.human_takeover_rate = self.metrics.human_takeover_rate + 1 / self.metrics.total_tasks
        logger.info("human_takeover", task_id=task_id, reason=reason)
    
    def record_token_usage(self, tokens: int) -> None:
        """Record token usage.
        
        Args:
            tokens: Number of tokens
        """
        self.metrics.token_usage += tokens
        logger.info("token_usage", tokens=tokens)
    
    def add_attribution(self, attribution: AttributionAnalysis) -> None:
        """Add attribution analysis.
        
        Args:
            attribution: Attribution analysis
        """
        self._attributions.append(attribution)
        logger.info("attribution_added", task_id=attribution.task_id)
    
    def get_metrics(self) -> AgentMetrics:
        """Get current metrics.
        
        Returns:
            Agent metrics
        """
        return self.metrics
    
    def get_attributions(self) -> list[AttributionAnalysis]:
        """Get all attributions.
        
        Returns:
            List of attributions
        """
        return self._attributions
    
    def get_summary(self) -> dict[str, Any]:
        """Get metrics summary.
        
        Returns:
            Metrics summary
        """
        return {
            "total_tasks": self.metrics.total_tasks,
            "successful_tasks": self.metrics.successful_tasks,
            "failed_tasks": self.metrics.failed_tasks,
            "task_completion_rate": self.metrics.task_completion_rate,
            "failure_rate": self.metrics.failure_rate,
            "avg_latency_ms": self.metrics.avg_latency_ms,
            "avg_steps": self.metrics.avg_steps,
            "tool_accuracy": self.metrics.tool_accuracy,
            "unnecessary_steps": self.metrics.unnecessary_steps,
            "invalid_loops": self.metrics.invalid_loops,
            "human_takeover_rate": self.metrics.human_takeover_rate,
            "token_usage": self.metrics.token_usage,
        }


class AttributionAnalyzer:
    """Analyzes attribution for tasks."""
    
    def analyze(self, task_id: str, steps: list[dict[str, Any]], success: bool) -> AttributionAnalysis:
        """Analyze attribution for a task.
        
        Args:
            task_id: Task ID
            steps: List of step results
            success: Whether task succeeded
        
        Returns:
            Attribution analysis
        """
        attributions = []
        
        for i, step in enumerate(steps):
            node = step.get("node", "unknown")
            status = step.get("status", "unknown")
            
            # Calculate contribution
            if status == "success":
                contribution = 1.0
            elif status == "error":
                contribution = -1.0
            elif status == "skipped":
                contribution = 0.0
            else:
                contribution = 0.5
            
            # Adjust based on node type
            if node in ["route", "analyze"]:
                contribution *= 1.5  # Critical nodes
            
            attributions.append(StepAttribution(
                step=i + 1,
                node=node,
                contribution=contribution,
                reason=f"Step {status}",
            ))
        
        analysis = AttributionAnalysis(
            task_id=task_id,
            overall_success=success,
            step_attributions=attributions,
        )
        
        analysis.analyze()
        
        return analysis


def create_metrics_collector() -> AgentMetricsCollector:
    """Convenience function to create metrics collector.
    
    Returns:
        Metrics collector
    """
    return AgentMetricsCollector()


def create_attribution_analyzer() -> AttributionAnalyzer:
    """Convenience function to create attribution analyzer.
    
    Returns:
        Attribution analyzer
    """
    return AttributionAnalyzer()
