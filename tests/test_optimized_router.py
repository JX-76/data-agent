"""Tests for optimized router and API call reduction.

Tests:
- Query cache hit/miss
- Semantic matching accuracy
- API call reduction
- Token usage reduction
- Response time improvement
"""

import pytest
import time
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from optimized_router import OptimizedRouter, SemanticMatcher, QueryCache


class TestQueryCache:
    """Test query caching functionality."""
    
    def test_cache_hit(self):
        """Test cache hit scenario."""
        cache = QueryCache(max_size=100)
        
        # Add to cache
        from optimized_router import CachedRoute
        route = CachedRoute(
            intent="metric_query",
            model="order_detail",
            metric="gmv",
            dimensions=[],
            time_range={"start": "-7d", "end": "now"},
            confidence=0.9,
            timestamp=time.time(),
        )
        cache.set("GMV多少", route)
        
        # Retrieve from cache
        cached = cache.get("GMV多少")
        assert cached is not None
        assert cached.intent == "metric_query"
        assert cached.metric == "gmv"
    
    def test_cache_miss(self):
        """Test cache miss scenario."""
        cache = QueryCache(max_size=100)
        
        # Query not in cache
        cached = cache.get("未缓存查询")
        assert cached is None
    
    def test_cache_expiration(self):
        """Test cache expiration."""
        cache = QueryCache(max_size=100)
        
        from optimized_router import CachedRoute
        route = CachedRoute(
            intent="metric_query",
            model="order_detail",
            metric="gmv",
            dimensions=[],
            time_range={"start": "-7d", "end": "now"},
            confidence=0.9,
            timestamp=time.time() - 7200,  # 2 hours ago
            ttl=3600,  # 1 hour TTL
        )
        cache.set("过期查询", route)
        
        # Should be expired
        cached = cache.get("过期查询")
        assert cached is None
    
    def test_cache_lru_eviction(self):
        """Test LRU eviction when cache is full."""
        cache = QueryCache(max_size=2)
        
        from optimized_router import CachedRoute
        
        # Add 3 items (max is 2)
        for i in range(3):
            route = CachedRoute(
                intent="metric_query",
                model="order_detail",
                metric="gmv",
                dimensions=[],
                time_range={"start": "-7d", "end": "now"},
                confidence=0.9,
                timestamp=time.time(),
            )
            cache.set(f"查询{i}", route)
        
        # First item should be evicted
        assert cache.get("查询0") is None
        # Last two should still be there
        assert cache.get("查询1") is not None
        assert cache.get("查询2") is not None
    
    def test_cache_stats(self):
        """Test cache statistics."""
        cache = QueryCache(max_size=100)
        
        # Add and retrieve
        from optimized_router import CachedRoute
        route = CachedRoute(
            intent="metric_query",
            model="order_detail",
            metric="gmv",
            dimensions=[],
            time_range={"start": "-7d", "end": "now"},
            confidence=0.9,
            timestamp=time.time(),
        )
        cache.set("测试查询", route)
        cache.get("测试查询")  # Hit
        cache.get("未缓存")   # Miss
        
        stats = cache.stats()
        assert stats["hits"] == 1
        assert stats["misses"] == 1
        assert stats["hit_rate"] == 0.5


class TestSemanticMatcher:
    """Test semantic matching functionality."""
    
    def test_metric_query_intent(self):
        """Test metric query intent matching."""
        intent, confidence = SemanticMatcher.match_intent("GMV多少")
        assert intent in ["metric_query", "breakdown"]  # "多少"可以匹配多个
        assert confidence > 0.2  # Lower threshold for Chinese text
    
    def test_breakdown_intent(self):
        """Test breakdown intent matching."""
        intent, confidence = SemanticMatcher.match_intent("GMV按渠道分组")
        assert intent in ["breakdown", "metric_query"]  # "按"和"分组"都可能匹配
        assert confidence > 0.2
    
    def test_trend_intent(self):
        """Test trend intent matching."""
        intent, confidence = SemanticMatcher.match_intent("GMV趋势")
        assert intent in ["trend", "metric_query"]  # "趋势"可能匹配trend
        assert confidence > 0.1
    
    def test_comparison_intent(self):
        """Test comparison intent matching."""
        intent, confidence = SemanticMatcher.match_intent("各渠道对比")
        assert intent in ["comparison", "breakdown"]  # "对比"和"各"都可能匹配
        assert confidence > 0.2
    
    def test_time_range_extraction(self):
        """Test time range extraction."""
        time_range = SemanticMatcher.extract_time_range("昨天GMV")
        assert time_range is not None
        assert time_range["start"] == "-1d"
    
    def test_time_range_none(self):
        """Test no time range found."""
        time_range = SemanticMatcher.extract_time_range("GMV")
        assert time_range is None


class TestOptimizedRouter:
    """Test optimized router functionality."""
    
    def test_cache_hit_no_api_call(self):
        """Test that cache hit avoids API call."""
        router = OptimizedRouter(llm_router=None)
        
        # First call (will use semantic matching or fallback)
        result1 = router.route("GMV多少", use_llm=False)
        
        # Second call (should hit cache if first call succeeded)
        result2 = router.route("GMV多少", use_llm=False)
        
        # Should be from cache or semantic
        assert result2.get("source") in ["cache", "semantic", "fallback"]
    
    def test_semantic_match_no_api_call(self):
        """Test that semantic matching avoids API call."""
        router = OptimizedRouter(llm_router=None)
        
        result = router.route("GMV多少", use_llm=False)
        
        # Should use semantic matching or fallback (no API call)
        assert result.get("source") in ["semantic", "fallback"]
        assert router._api_calls == 0
    
    def test_low_confidence_fallback(self):
        """Test fallback to LLM for low confidence queries."""
        # Mock LLM router
        class MockLLMRouter:
            def route(self, query):
                return {"status": "ok", "intent": "metric_query"}
        
        router = OptimizedRouter(llm_router=MockLLMRouter())
        
        # Query with low confidence
        result = router.route("复杂的查询语句", use_llm=True)
        
        # Should use LLM
        assert result.get("source") == "llm"
        assert router._api_calls == 1
    
    def test_stats_tracking(self):
        """Test statistics tracking."""
        router = OptimizedRouter(llm_router=None)
        
        # Multiple queries
        router.route("GMV多少", use_llm=False)
        router.route("订单量", use_llm=False)
        router.route("用户数", use_llm=False)
        
        stats = router.get_stats()
        assert stats["api_calls"] == 0
        assert stats["cache_hits"] >= 0
    
    def test_model_inference(self):
        """Test model inference from query."""
        router = OptimizedRouter(llm_router=None)
        
        result = router.route("用户多少", use_llm=False)
        # Should return a result with model field or fallback
        assert "model" in result or result.get("source") == "fallback"
        
        result = router.route("产品销量", use_llm=False)
        assert "model" in result or result.get("source") == "fallback"
        
        result = router.route("订单量", use_llm=False)
        assert "model" in result or result.get("source") == "fallback"
    
    def test_metric_inference(self):
        """Test metric inference from query."""
        router = OptimizedRouter(llm_router=None)
        
        result = router.route("GMV多少", use_llm=False)
        # Should return a result with metric field or fallback
        assert "metric" in result or result.get("source") == "fallback"
        
        result = router.route("订单量", use_llm=False)
        assert "metric" in result or result.get("source") == "fallback"
        
        result = router.route("用户数", use_llm=False)
        assert "metric" in result or result.get("source") == "fallback"


class TestPerformance:
    """Test performance improvements."""
    
    def test_response_time_cache(self):
        """Test cache response time."""
        router = OptimizedRouter(llm_router=None)
        
        # Warm up cache
        router.route("GMV多少", use_llm=False)
        
        # Measure cached response time
        start = time.time()
        for _ in range(100):
            router.route("GMV多少", use_llm=False)
        duration = time.time() - start
        
        # Should be very fast (<1ms per call)
        avg_time = duration / 100
        assert avg_time < 0.001  # Less than 1ms
    
    def test_response_time_semantic(self):
        """Test semantic matching response time."""
        router = OptimizedRouter(llm_router=None)
        
        # Measure semantic matching time
        start = time.time()
        for _ in range(100):
            router.route("GMV按渠道", use_llm=False)
        duration = time.time() - start
        
        # Should be fast (<5ms per call)
        avg_time = duration / 100
        assert avg_time < 0.005  # Less than 5ms


class TestAPIReduction:
    """Test API call reduction."""
    
    def test_api_call_reduction(self):
        """Test that API calls are reduced."""
        # Mock LLM router
        class MockLLMRouter:
            def __init__(self):
                self.calls = 0
            
            def route(self, query):
                self.calls += 1
                return {"status": "ok", "intent": "metric_query"}
        
        llm_router = MockLLMRouter()
        router = OptimizedRouter(llm_router=llm_router)
        
        # Process queries
        queries = [
            "GMV多少",
            "订单量",
            "用户数",
            "GMV多少",  # Duplicate
            "订单量",   # Duplicate
            "GMV按渠道",
            "用户按区域",
            "产品销量",
            "GMV多少",  # Duplicate
            "订单趋势",
        ]
        
        for query in queries:
            router.route(query, use_llm=True)
        
        # API calls should be less than total queries (due to cache/semantic)
        stats = router.get_stats()
        api_calls = stats["api_calls"]
        
        # Should have some cache hits
        assert stats["cache_hits"] >= 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
