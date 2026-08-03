"""BI Dashboard API endpoints.

Provides:
- Metrics aggregation
- Query analytics
- System health
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/bi", tags=["bi"])

# In-memory storage for demo (replace with proper database in production)
_query_history = []
_metrics = {
    "total_queries": 0,
    "total_latency": 0,
    "cache_hits": 0,
    "cache_misses": 0,
    "errors": 0,
}


class QueryRecord(BaseModel):
    timestamp: str
    query: str
    intent: str
    status: str
    latency: int


class MetricsResponse(BaseModel):
    total_queries: int
    avg_latency: float
    cache_hit_rate: float
    error_rate: float
    active_sessions: int
    queries_per_minute: float


class DashboardData(BaseModel):
    metrics: MetricsResponse
    recent_queries: list[dict]
    components: list[dict]
    alerts: list[dict]


@router.get("/metrics", response_model=MetricsResponse)
async def get_metrics():
    """Get current metrics."""
    total = _metrics["total_queries"]
    errors = _metrics["errors"]
    
    return MetricsResponse(
        total_queries=total,
        avg_latency=_metrics["total_latency"] / max(1, total),
        cache_hit_rate=_metrics["cache_hits"] / max(1, _metrics["cache_hits"] + _metrics["cache_misses"]) * 100,
        error_rate=errors / max(1, total) * 100,
        active_sessions=42,  # Placeholder
        queries_per_minute=total / max(1, (time.time() - _query_history[0]["timestamp"]) / 60) if _query_history else 0
    )


@router.get("/dashboard", response_model=DashboardData)
async def get_dashboard():
    """Get full dashboard data."""
    metrics = await get_metrics()
    
    return DashboardData(
        metrics=metrics,
        recent_queries=_query_history[-10:],
        components=[
            {"name": "API Server", "status": "healthy", "metric": "CPU 45%"},
            {"name": "Database", "status": "healthy", "metric": "连接数 12/50"},
            {"name": "Cache", "status": "healthy", "metric": "命中率 87%"},
            {"name": "LLM Service", "status": "warning", "metric": "延迟 2.5s"},
        ],
        alerts=[
            {"severity": "warn", "message": "LLM 服务响应时间超过 2s"},
        ]
    )


@router.get("/queries/history")
async def get_query_history(limit: int = 100, offset: int = 0):
    """Get query history."""
    return {
        "queries": _query_history[offset:offset + limit],
        "total": len(_query_history)
    }


@router.get("/queries/trends")
async def get_query_trends(period: str = "24h"):
    """Get query trends."""
    # Generate hourly data for the last 24 hours
    hours = 24 if period == "24h" else 168 if period == "7d" else 24
    
    trends = []
    for i in range(hours):
        hour_time = datetime.now() - timedelta(hours=hours - i)
        trends.append({
            "hour": hour_time.strftime("%H:00"),
            "queries": len([q for q in _query_history if q["timestamp"] > (time.time() - (hours - i) * 3600)]),
        })
    
    return {"trends": trends}


@router.get("/intents/distribution")
async def get_intent_distribution():
    """Get intent distribution."""
    intents = {}
    for query in _query_history:
        intent = query.get("intent", "unknown")
        intents[intent] = intents.get(intent, 0) + 1
    
    return {
        "distribution": [
            {"intent": intent, "count": count}
            for intent, count in sorted(intents.items(), key=lambda x: x[1], reverse=True)
        ]
    }


def record_query(query: str, intent: str, status: str, latency: int, cached: bool = False):
    """Record a query for analytics."""
    _query_history.append({
        "timestamp": time.time(),
        "query": query,
        "intent": intent,
        "status": status,
        "latency": latency,
        "cached": cached,
    })
    
    _metrics["total_queries"] += 1
    _metrics["total_latency"] += latency
    
    if cached:
        _metrics["cache_hits"] += 1
    else:
        _metrics["cache_misses"] += 1
    
    if status != "ok":
        _metrics["errors"] += 1
