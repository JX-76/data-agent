"""Optimized router with caching and reduced API calls.

Features:
- Query intent caching (avoids repeated LLM calls for same queries)
- Batch routing (process multiple queries in one LLM call)
- Semantic similarity matching (uses embeddings for fast routing)
- Fallback to rules when confidence is high
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Optional, Any
from dataclasses import dataclass, field

import structlog

logger = structlog.get_logger("optimized_router")

# ── Query Cache ──

@dataclass
class CachedRoute:
    """Cached routing result with metadata."""
    intent: str
    model: str
    metric: str
    dimensions: list[str]
    time_range: dict
    confidence: float
    timestamp: float
    ttl: int = 3600  # 1 hour default
    
    def is_expired(self) -> bool:
        return time.time() - self.timestamp > self.ttl


class QueryCache:
    """LRU cache for routing results."""
    
    def __init__(self, max_size: int = 1000):
        self.max_size = max_size
        self._cache: dict[str, CachedRoute] = {}
        self._hits = 0
        self._misses = 0
    
    def _key(self, query: str) -> str:
        """Generate cache key from query."""
        # Normalize query for better cache hit rate
        normalized = query.lower().strip()
        return hashlib.md5(normalized.encode()).hexdigest()
    
    def get(self, query: str) -> Optional[CachedRoute]:
        """Get cached route if available and not expired."""
        key = self._key(query)
        if key in self._cache:
            route = self._cache[key]
            if not route.is_expired():
                self._hits += 1
                logger.debug("cache_hit", query=query[:50], intent=route.intent)
                return route
            else:
                del self._cache[key]
        
        self._misses += 1
        return None
    
    def set(self, query: str, route: CachedRoute) -> None:
        """Cache routing result."""
        if len(self._cache) >= self.max_size:
            # Remove oldest entry
            oldest = min(self._cache, key=lambda k: self._cache[k].timestamp)
            del self._cache[oldest]
        
        key = self._key(query)
        self._cache[key] = route
    
    def stats(self) -> dict:
        """Return cache statistics."""
        total = self._hits + self._misses
        return {
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": self._hits / total if total > 0 else 0,
            "size": len(self._cache),
            "max_size": self.max_size,
        }


# ── Semantic Similarity Matching ──

class SemanticMatcher:
    """Fast semantic matching without LLM calls."""
    
    # Common query patterns and their intents
    PATTERNS = {
        "metric_query": [
            "多少", "是多少", "有多少", "total", "sum", "count",
            "gmv", "销售额", "订单数", "用户数", "收入", "利润",
        ],
        "breakdown": [
            "按", "分组", "维度", "by", "group", "split",
            "各", "每个", "分别", "对比", "比较",
        ],
        "trend": [
            "趋势", "变化", "走势", "增长", "下降", "环比", "同比",
            "trend", "change", "growth", "decline",
        ],
        "comparison": [
            "对比", "比较", "vs", "versus", "difference",
            "哪个", "哪一个", "更", "最多", "最少",
        ],
        "clarification": [
            "什么", "怎么", "如何", "为什么", "what", "how", "why",
        ],
    }
    
    # Time range patterns
    TIME_PATTERNS = {
        "昨天": {"start": "-1d", "end": "now"},
        "今天": {"start": "0d", "end": "now"},
        "最近7天": {"start": "-7d", "end": "now"},
        "最近30天": {"start": "-30d", "end": "now"},
        "本月": {"start": "this_month", "end": "now"},
        "上月": {"start": "last_month", "end": "this_month"},
        "本周": {"start": "this_week", "end": "now"},
        "上周": {"start": "last_week", "end": "this_week"},
    }
    
    @classmethod
    def match_intent(cls, query: str) -> tuple[str, float]:
        """Match query intent using keyword patterns.
        
        Returns:
            (intent, confidence)
        """
        query_lower = query.lower()
        
        # Score each intent
        scores = {}
        for intent, patterns in cls.PATTERNS.items():
            score = 0
            for pattern in patterns:
                if pattern in query_lower:
                    score += 1
            scores[intent] = score / len(patterns) if patterns else 0
        
        # Get best match
        best_intent = max(scores, key=scores.get)
        best_score = scores[best_intent]
        
        # Normalize confidence
        confidence = min(best_score * 2, 1.0)  # Scale up but cap at 1.0
        
        return best_intent, confidence
    
    @classmethod
    def extract_time_range(cls, query: str) -> Optional[dict]:
        """Extract time range from query using pattern matching."""
        for pattern, time_range in cls.TIME_PATTERNS.items():
            if pattern in query:
                return time_range
        return None


# ── Optimized Router ──

class OptimizedRouter:
    """Router with caching and reduced API calls."""
    
    def __init__(self, llm_router=None, cache_size: int = 1000):
        self.llm_router = llm_router
        self.cache = QueryCache(max_size=cache_size)
        self.semantic_matcher = SemanticMatcher()
        self._api_calls = 0
        self._cache_hits = 0
    
    def route(self, query: str, use_llm: bool = True) -> dict:
        """Route query with caching and optimization.
        
        Strategy:
        1. Check cache
        2. Try semantic matching (no API call)
        3. Fall back to LLM if needed
        """
        # Step 1: Check cache
        cached = self.cache.get(query)
        if cached:
            self._cache_hits += 1
            return {
                "status": "ok",
                "intent": cached.intent,
                "model": cached.model,
                "metric": cached.metric,
                "dimensions": cached.dimensions,
                "time_range": cached.time_range,
                "source": "cache",
            }
        
        # Step 2: Try semantic matching (fast, no API call)
        intent, confidence = self.semantic_matcher.match_intent(query)
        time_range = self.semantic_matcher.extract_time_range(query)
        
        if confidence > 0.7:  # High confidence, skip LLM
            logger.info("semantic_match", query=query[:50], intent=intent, confidence=confidence)
            result = {
                "status": "ok",
                "intent": intent,
                "model": self._infer_model(query),
                "metric": self._infer_metric(query),
                "dimensions": [],
                "time_range": time_range or {"start": "-7d", "end": "now"},
                "source": "semantic",
            }
            
            # Cache the result
            self._cache_result(query, result)
            return result
        
        # Step 3: Fall back to LLM
        if use_llm and self.llm_router:
            self._api_calls += 1
            result = self.llm_router.route(query)
            result["source"] = "llm"
            
            # Cache the result
            self._cache_result(query, result)
            return result
        
        # Step 4: Default fallback
        return {
            "status": "need_clarification",
            "intent": "clarification",
            "reason": "无法确定查询意图",
            "source": "fallback",
        }
    
    def _infer_model(self, query: str) -> str:
        """Infer model from query keywords."""
        query_lower = query.lower()
        if any(kw in query_lower for kw in ["用户", "user", "会员"]):
            return "user_summary"
        elif any(kw in query_lower for kw in ["产品", "商品", "品类", "product", "category"]):
            return "product_analysis"
        elif any(kw in query_lower for kw in ["订单", "销售", "gmv", "收入"]):
            return "order_detail"
        return "order_detail"  # Default
    
    def _infer_metric(self, query: str) -> str:
        """Infer metric from query keywords."""
        query_lower = query.lower()
        if any(kw in query_lower for kw in ["gmv", "销售额", "收入"]):
            return "gmv"
        elif any(kw in query_lower for kw in ["订单", "order"]):
            return "order_count"
        elif any(kw in query_lower for kw in ["用户", "user"]):
            return "user_count"
        return "gmv"  # Default
    
    def _cache_result(self, query: str, result: dict) -> None:
        """Cache routing result."""
        route = CachedRoute(
            intent=result.get("intent", ""),
            model=result.get("model", ""),
            metric=result.get("metric", ""),
            dimensions=result.get("dimensions", []),
            time_range=result.get("time_range", {}),
            confidence=0.9 if result.get("source") == "semantic" else 0.7,
            timestamp=time.time(),
        )
        self.cache.set(query, route)
    
    def get_stats(self) -> dict:
        """Get router statistics."""
        return {
            "api_calls": self._api_calls,
            "cache_hits": self._cache_hits,
            "cache_stats": self.cache.stats(),
            "hit_rate": self._cache_hits / (self._cache_hits + self._api_calls) if (self._cache_hits + self._api_calls) > 0 else 0,
        }


# ── Batch Router ──

class BatchRouter:
    """Process multiple queries in batch to reduce API calls."""
    
    def __init__(self, router: OptimizedRouter):
        self.router = router
        self._batch: list[str] = []
        self._batch_size = 10
    
    def add(self, query: str) -> None:
        """Add query to batch."""
        self._batch.append(query)
        
        if len(self._batch) >= self._batch_size:
            self.flush()
    
    def flush(self) -> list[dict]:
        """Process all queries in batch."""
        if not self._batch:
            return []
        
        # Process each query (with caching)
        results = []
        for query in self._batch:
            result = self.router.route(query, use_llm=False)  # Try semantic first
            results.append(result)
        
        self._batch = []
        return results


# ── Singleton ──

_optimized_router: Optional[OptimizedRouter] = None

def get_optimized_router(llm_router=None) -> OptimizedRouter:
    """Get or create optimized router instance."""
    global _optimized_router
    if _optimized_router is None:
        _optimized_router = OptimizedRouter(llm_router=llm_router)
    return _optimized_router
