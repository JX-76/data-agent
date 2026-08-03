"""APM (Application Performance Monitoring) integration.

Provides:
- Prometheus metrics endpoint
- Custom business metrics
- Performance tracking
- Health checks
"""

from __future__ import annotations

import time
from typing import Optional, Callable
from functools import wraps

# Try to import prometheus client
try:
    from prometheus_client import Counter, Histogram, Gauge, Info, generate_latest, CONTENT_TYPE_LATEST
    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False


class MetricsCollector:
    """Collects application metrics."""
    
    def __init__(self):
        self._metrics = {}
        self._enabled = PROMETHEUS_AVAILABLE
        
        if self._enabled:
            # Request metrics
            self.request_count = Counter(
                'data_agent_requests_total',
                'Total requests',
                ['method', 'endpoint', 'status']
            )
            self.request_duration = Histogram(
                'data_agent_request_duration_seconds',
                'Request duration',
                ['method', 'endpoint']
            )
            
            # Business metrics
            self.query_count = Counter(
                'data_agent_queries_total',
                'Total queries processed',
                ['intent', 'status']
            )
            self.llm_calls = Counter(
                'data_agent_llm_calls_total',
                'Total LLM calls',
                ['model', 'status']
            )
            self.cache_hits = Counter(
                'data_agent_cache_hits_total',
                'Cache hits'
            )
            self.cache_misses = Counter(
                'data_agent_cache_misses_total',
                'Cache misses'
            )
            
            # System metrics
            self.active_connections = Gauge(
                'data_agent_active_connections',
                'Current active connections'
            )
            self.queue_size = Gauge(
                'data_agent_queue_size',
                'Current queue size'
            )
    
    def record_request(self, method: str, endpoint: str, status: int, duration: float):
        """Record a request."""
        if self._enabled:
            self.request_count.labels(method=method, endpoint=endpoint, status=status).inc()
            self.request_duration.labels(method=method, endpoint=endpoint).observe(duration)
    
    def record_query(self, intent: str, status: str):
        """Record a query."""
        if self._enabled:
            self.query_count.labels(intent=intent, status=status).inc()
    
    def record_llm_call(self, model: str, status: str):
        """Record an LLM call."""
        if self._enabled:
            self.llm_calls.labels(model=model, status=status).inc()
    
    def record_cache_hit(self):
        """Record a cache hit."""
        if self._enabled:
            self.cache_hits.inc()
    
    def record_cache_miss(self):
        """Record a cache miss."""
        if self._enabled:
            self.cache_misses.inc()
    
    def set_active_connections(self, count: int):
        """Set active connections."""
        if self._enabled:
            self.active_connections.set(count)
    
    def set_queue_size(self, size: int):
        """Set queue size."""
        if self._enabled:
            self.queue_size.set(size)
    
    def get_metrics(self) -> bytes:
        """Get metrics in Prometheus format."""
        if self._enabled:
            return generate_latest()
        return b""


class PerformanceTracker:
    """Track performance of functions."""
    
    def __init__(self):
        self._metrics = []
    
    def track(self, name: str):
        """Decorator to track function performance."""
        def decorator(func):
            @wraps(func)
            def wrapper(*args, **kwargs):
                start = time.time()
                try:
                    result = func(*args, **kwargs)
                    duration = time.time() - start
                    self._metrics.append({
                        "name": name,
                        "duration": duration,
                        "status": "success"
                    })
                    return result
                except Exception as e:
                    duration = time.time() - start
                    self._metrics.append({
                        "name": name,
                        "duration": duration,
                        "status": "error",
                        "error": str(e)
                    })
                    raise
            return wrapper
        return decorator
    
    def get_stats(self) -> dict:
        """Get performance statistics."""
        if not self._metrics:
            return {}
        
        total = len(self._metrics)
        durations = [m["duration"] for m in self._metrics]
        errors = [m for m in self._metrics if m["status"] == "error"]
        
        return {
            "total_calls": total,
            "avg_duration": sum(durations) / len(durations),
            "max_duration": max(durations),
            "min_duration": min(durations),
            "error_count": len(errors),
            "error_rate": len(errors) / total,
        }


# Global instances
metrics = MetricsCollector()
tracker = PerformanceTracker()
