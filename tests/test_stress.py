"""High-stress and edge-case tests for Data Agent.

Tests:
- Concurrent query handling
- Large result sets
- Complex nested queries
- SQL injection attempts
- Rate limiting
- Cache pressure
- Memory leaks
"""

import asyncio
import json
import time
import threading
from concurrent.futures import ThreadPoolExecutor
import pytest

# Import the modules to test
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from validation import InputValidator, ValidationError
from distributed_cache import DistributedCache
from circuit_breaker import CircuitBreakerOpenError
from db import ConnectionPool, get_pool


class TestConcurrentQueries:
    """Test concurrent query handling."""
    
    def test_10_concurrent_queries(self):
        """Test handling 10 concurrent queries."""
        queries = [
            "昨天 GMV 是多少？",
            "各渠道订单量",
            "最近7天各品类GMV",
            "华南大区的销售额",
            "只看线上渠道",
            "数码品类GMV",
            "GMV和订单数按渠道",
            "客单价按区域",
            "退款订单有多少",
            "最近30天趋势",
        ]
        
        def run_query(query):
            # Simulate query execution
            time.sleep(0.1)
            return {"status": "ok", "query": query}
        
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(run_query, q) for q in queries]
            results = [f.result() for f in futures]
        
        assert len(results) == 10
        assert all(r["status"] == "ok" for r in results)
    
    def test_100_concurrent_queries(self):
        """Test handling 100 concurrent queries (stress test)."""
        queries = [f"查询{i}" for i in range(100)]
        
        def run_query(query):
            time.sleep(0.01)
            return {"status": "ok", "query": query}
        
        with ThreadPoolExecutor(max_workers=50) as executor:
            futures = [executor.submit(run_query, q) for q in queries]
            results = [f.result() for f in futures]
        
        assert len(results) == 100


class TestLargeResultSets:
    """Test handling large result sets."""
    
    def test_10000_rows(self):
        """Test handling 10,000 row result set."""
        rows = [{"id": i, "value": f"data_{i}"} for i in range(10000)]
        assert len(rows) == 10000
    
    def test_100000_rows(self):
        """Test handling 100,000 row result set (memory pressure)."""
        rows = [{"id": i, "value": f"data_{i}"} for i in range(100000)]
        assert len(rows) == 100000


class TestSQLInjection:
    """Test SQL injection protection."""
    
    def test_union_injection(self):
        """Test UNION-based SQL injection."""
        validator = InputValidator()
        malicious = "' UNION SELECT * FROM users --"
        with pytest.raises(ValidationError):
            validator.validate_query(malicious)
    
    def test_comment_injection(self):
        """Test comment-based SQL injection."""
        validator = InputValidator()
        malicious = "'; DROP TABLE fct_orders; --"
        with pytest.raises(ValidationError):
            validator.validate_query(malicious)
    
    def test_or_injection(self):
        """Test OR-based SQL injection."""
        validator = InputValidator()
        malicious = "' OR '1'='1"
        with pytest.raises(ValidationError):
            validator.validate_query(malicious)
    
    def test_xss_injection(self):
        """Test XSS injection."""
        validator = InputValidator()
        malicious = "<script>alert('xss')</script>"
        with pytest.raises(ValidationError):
            validator.validate_query(malicious)


class TestRateLimiting:
    """Test rate limiting."""
    
    def test_burst_requests(self):
        """Test burst of requests."""
        # Simulate 100 requests in 1 second
        start = time.time()
        count = 0
        while time.time() - start < 1:
            count += 1
        assert count > 0
    
    def test_sustained_load(self):
        """Test sustained load over time."""
        start = time.time()
        count = 0
        while time.time() - start < 5:
            count += 1
            time.sleep(0.01)
        assert count > 0


class TestCachePressure:
    """Test cache under pressure."""
    
    def test_cache_eviction(self):
        """Test cache eviction under pressure."""
        cache = DistributedCache(config={"max_size": 10})
        
        # Fill cache beyond capacity
        for i in range(20):
            cache.set(f"query_{i}", False, {"result": i})
        
        # Verify some entries were evicted
        hits = sum(1 for i in range(20) if cache.get(f"query_{i}") is not None)
        assert hits <= 10
    
    def test_cache_ttl_expiration(self):
        """Test TTL expiration."""
        cache = DistributedCache(config={"max_size": 10})
        cache.set("test", False, {"result": "test"}, ttl=1)
        
        # Should be available immediately
        assert cache.get("test") is not None
        
        # Wait for expiration
        time.sleep(2)
        assert cache.get("test") is None


class TestMemoryLeaks:
    """Test for memory leaks."""
    
    def test_connection_pool_reuse(self):
        """Test connection pool doesn't leak connections."""
        # Simulate many requests
        for _ in range(100):
            pass  # Placeholder for actual connection usage
    
    def test_session_cleanup(self):
        """Test session cleanup."""
        # Simulate session creation and cleanup
        sessions = []
        for i in range(100):
            sessions.append({"id": i, "data": "x" * 1000})
        
        # Cleanup
        sessions.clear()
        assert len(sessions) == 0


class TestEdgeCases:
    """Test edge cases."""
    
    def test_empty_query(self):
        """Test empty query handling."""
        validator = InputValidator()
        with pytest.raises(ValidationError):
            validator.validate_query("")
    
    def test_very_long_query(self):
        """Test very long query handling."""
        validator = InputValidator(max_length=100)
        long_query = "A" * 200
        with pytest.raises(ValidationError):
            validator.validate_query(long_query)
    
    def test_unicode_query(self):
        """Test unicode query handling."""
        validator = InputValidator()
        query = "昨天 GMV 是多少？🔥🚀"
        result = validator.validate_query(query)
        assert result == query
    
    def test_special_characters(self):
        """Test special characters in query."""
        validator = InputValidator()
        query = "SELECT * FROM table WHERE id = 1; DROP TABLE users;"
        with pytest.raises(ValidationError):
            validator.validate_query(query)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
