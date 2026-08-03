"""LogMetric Integration: Log/metric query integration with DAG.

Integrates LogMetricQuery with the DAG execution.
"""

from __future__ import annotations

from typing import Any

from log_metric_query import LogMetricQuery, LogEntry, MetricValue


class LogMetricIntegration:
    """Integrates log/metric query with DAG execution."""
    
    def __init__(self):
        self.query = LogMetricQuery()
    
    def query_logs(self, 
        level: str | None = None,
        source: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        limit: int = 100,
    ) -> list[LogEntry]:
        """Query logs.
        
        Args:
            level: Log level filter
            source: Source filter
            start_time: Start time filter
            end_time: End time filter
            limit: Maximum results
        
        Returns:
            List of log entries
        """
        return self.query.query_logs(level, source, start_time, end_time, limit)
    
    def query_metrics(self,
        name: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        tags: dict[str, str] | None = None,
        limit: int = 100,
    ) -> list[MetricValue]:
        """Query metrics.
        
        Args:
            name: Metric name filter
            start_time: Start time filter
            end_time: End time filter
            tags: Tags filter
            limit: Maximum results
        
        Returns:
            List of metric values
        """
        return self.query.query_metrics(name, start_time, end_time, tags, limit)
    
    def add_log(self, entry: LogEntry) -> None:
        """Add a log entry.
        
        Args:
            entry: Log entry
        """
        self.query.add_log(entry)
    
    def add_metric(self, metric: MetricValue) -> None:
        """Add a metric value.
        
        Args:
            metric: Metric value
        """
        self.query.add_metric(metric)
    
    def get_log_stats(self) -> dict[str, Any]:
        """Get log statistics.
        
        Returns:
            Log statistics
        """
        return self.query.get_log_stats()
    
    def get_metric_stats(self) -> dict[str, Any]:
        """Get metric statistics.
        
        Returns:
            Metric statistics
        """
        return self.query.get_metric_stats()


def create_log_metric_integration() -> LogMetricIntegration:
    """Convenience function to create log/metric integration.
    
    Returns:
        Log/metric integration
    """
    return LogMetricIntegration()
