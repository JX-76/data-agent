"""LogMetricQuery: Log and metric query integration.

Provides interfaces for querying logs and metrics.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger("log_metric_query")


@dataclass
class LogEntry:
    """A single log entry."""
    timestamp: str
    level: str
    message: str
    source: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class MetricValue:
    """A single metric value."""
    timestamp: str
    name: str
    value: float
    tags: dict[str, str] = field(default_factory=dict)


class LogMetricQuery:
    """Queries logs and metrics."""
    
    def __init__(self):
        self._log_store: list[LogEntry] = []
        self._metric_store: list[MetricValue] = []
    
    def query_logs(self, 
        level: str | None = None,
        source: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        limit: int = 100,
    ) -> list[LogEntry]:
        """Query logs with filters.
        
        Args:
            level: Log level filter
            source: Source filter
            start_time: Start time filter
            end_time: End time filter
            limit: Maximum results
        
        Returns:
            List of log entries
        """
        results = self._log_store
        
        if level:
            results = [e for e in results if e.level == level]
        
        if source:
            results = [e for e in results if e.source == source]
        
        if start_time:
            results = [e for e in results if e.timestamp >= start_time]
        
        if end_time:
            results = [e for e in results if e.timestamp <= end_time]
        
        return results[:limit]
    
    def query_metrics(self,
        name: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        tags: dict[str, str] | None = None,
        limit: int = 100,
    ) -> list[MetricValue]:
        """Query metrics with filters.
        
        Args:
            name: Metric name filter
            start_time: Start time filter
            end_time: End time filter
            tags: Tags filter
            limit: Maximum results
        
        Returns:
            List of metric values
        """
        results = self._metric_store
        
        if name:
            results = [m for m in results if m.name == name]
        
        if start_time:
            results = [m for m in results if m.timestamp >= start_time]
        
        if end_time:
            results = [m for m in results if m.timestamp <= end_time]
        
        if tags:
            for key, value in tags.items():
                results = [m for m in results if m.tags.get(key) == value]
        
        return results[:limit]
    
    def add_log(self, entry: LogEntry) -> None:
        """Add a log entry.
        
        Args:
            entry: Log entry
        """
        self._log_store.append(entry)
        logger.debug("log_added", level=entry.level, source=entry.source)
    
    def add_metric(self, metric: MetricValue) -> None:
        """Add a metric value.
        
        Args:
            metric: Metric value
        """
        self._metric_store.append(metric)
        logger.debug("metric_added", name=metric.name, value=metric.value)
    
    def get_log_stats(self) -> dict[str, Any]:
        """Get log statistics.
        
        Returns:
            Log statistics
        """
        levels = {}
        for entry in self._log_store:
            levels[entry.level] = levels.get(entry.level, 0) + 1
        
        return {
            "total": len(self._log_store),
            "levels": levels,
        }
    
    def get_metric_stats(self) -> dict[str, Any]:
        """Get metric statistics.
        
        Returns:
            Metric statistics
        """
        names = {}
        for metric in self._metric_store:
            if metric.name not in names:
                names[metric.name] = []
            names[metric.name].append(metric.value)
        
        stats = {}
        for name, values in names.items():
            if values:
                stats[name] = {
                    "count": len(values),
                    "avg": sum(values) / len(values),
                    "min": min(values),
                    "max": max(values),
                }
        
        return stats


def create_log_metric_query() -> LogMetricQuery:
    """Convenience function to create log/metric query.
    
    Returns:
        Log/metric query
    """
    return LogMetricQuery()
