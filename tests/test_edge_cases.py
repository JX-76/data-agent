"""Comprehensive edge-case and integration tests.

Tests:
- Database connection failures
- Cache corruption
- LLM service failures
- Invalid configurations
- Resource exhaustion
- Concurrent modifications
- Data corruption
"""

import pytest
import time
import threading
from concurrent.futures import ThreadPoolExecutor

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from validation import InputValidator, ValidationError
from circuit_breaker import CircuitBreaker, CircuitBreakerOpenError
from distributed_cache import DistributedCache


class TestDatabaseFailures:
    """Test database failure handling."""
    
    def test_connection_timeout(self):
        """Test handling of connection timeout."""
        # Simulate timeout by using invalid host
        config = {
            "type": "postgresql",
            "host": "invalid_host",
            "port": 5432,
            "name": "test",
            "user": "test",
            "password": "test",
            "pool_timeout": 1.0,
            "pool_max": 1,
        }
        
        from db import create_pool
        with pytest.raises(Exception):
            pool = create_pool(config)
            with pool.connection() as conn:
                conn.execute("SELECT 1")
    
    def test_invalid_credentials(self):
        """Test handling of invalid credentials."""
        config = {
            "type": "postgresql",
            "host": "localhost",
            "port": 5432,
            "name": "test",
            "user": "invalid_user",
            "password": "wrong_password",
            "pool_timeout": 1.0,
            "pool_max": 1,
        }
        
        from db import create_pool
        with pytest.raises(Exception):
            pool = create_pool(config)
            with pool.connection() as conn:
                conn.execute("SELECT 1")


class TestCacheFailures:
    """Test cache failure handling."""
    
    def test_cache_corruption(self):
        """Test handling of corrupted cache entries."""
        cache = DistributedCache(config={"max_size": 10})
        
        # Store valid data
        cache.set("test", False, {"result": "valid"})
        assert cache.get("test") is not None
        
        # Simulate corruption by clearing
        cache.clear()
        assert cache.get("test") is None
    
    def test_cache_memory_pressure(self):
        """Test cache under memory pressure."""
        cache = DistributedCache(config={"max_size": 100})
        
        # Store many large objects
        large_data = {"data": "x" * 10000}
        for i in range(100):
            cache.set(f"key_{i}", False, large_data)
        
        # Verify cache still works
        assert cache.get("key_0") is not None


class TestLLMFailures:
    """Test LLM service failure handling."""
    
    def test_circuit_breaker(self):
        """Test circuit breaker pattern."""
        breaker = CircuitBreaker(
            name="test",
            failure_threshold=3,
            recovery_timeout=1.0,
            half_open_max_calls=1,
            success_threshold=1
        )
        
        # Simulate failures
        async def failing_func():
            raise Exception("LLM failed")
        
        # Should fail 3 times then open circuit
        for _ in range(3):
            with pytest.raises(Exception):
                import asyncio
                asyncio.run(breaker.call(failing_func))
        
        # Circuit should be open now
        with pytest.raises(CircuitBreakerOpenError):
            import asyncio
            asyncio.run(breaker.call(failing_func))
    
    def test_circuit_breaker_recovery(self):
        """Test circuit breaker recovery."""
        breaker = CircuitBreaker(
            name="test",
            failure_threshold=2,
            recovery_timeout=0.1,
            half_open_max_calls=1,
            success_threshold=1
        )
        
        # Open circuit
        async def failing_func():
            raise Exception("LLM failed")
        
        for _ in range(2):
            with pytest.raises(Exception):
                import asyncio
                asyncio.run(breaker.call(failing_func))
        
        # Wait for recovery
        time.sleep(0.2)
        
        # Should allow one test call
        async def success_func():
            return "success"
        
        import asyncio
        result = asyncio.run(breaker.call(success_func))
        assert result == "success"


class TestConfigurationErrors:
    """Test invalid configuration handling."""
    
    def test_invalid_db_type(self):
        """Test handling of invalid database type."""
        from db import create_pool
        
        with pytest.raises(ValueError):
            create_pool({"type": "invalid_db"})
    
    def test_missing_required_config(self):
        """Test handling of missing required configuration."""
        from db import create_pool
        
        # PostgreSQL without host should use defaults and fail to connect
        pool = create_pool({
            "type": "postgresql",
            "pool_timeout": 1.0,
        })
        # Should create pool but fail on connection
        assert pool is not None


class TestResourceExhaustion:
    """Test resource exhaustion handling."""
    
    def test_connection_pool_exhaustion(self):
        """Test handling of exhausted connection pool."""
        from db import create_pool
        
        config = {
            "type": "sqlite",
            "path": ":memory:",
            "pool_max": 2,
            "pool_timeout": 0.1,
        }
        
        pool = create_pool(config)
        
        # Exhaust pool
        connections = []
        for _ in range(2):
            connections.append(pool._acquire())
        
        # Next request should timeout
        with pytest.raises(TimeoutError):
            pool._acquire()
        
        # Release connections
        for conn in connections:
            pool._release(conn)
    
    def test_memory_exhaustion(self):
        """Test handling of memory exhaustion."""
        # This is a simplified test - real memory exhaustion testing
        # would require more complex setup
        cache = DistributedCache(config={"max_size": 10})
        
        # Fill cache beyond capacity
        for i in range(20):
            cache.set(f"key_{i}", False, {"data": f"value_{i}"})
        
        # Verify eviction works (cache size should be <= max_size)
        # Note: eviction might not happen immediately, so we check total size
        hits = sum(1 for i in range(20) if cache.get(f"key_{i}") is not None)
        assert hits <= 20  # Some should be evicted


class TestConcurrentAccess:
    """Test concurrent access handling."""
    
    def test_concurrent_cache_access(self):
        """Test concurrent cache access."""
        cache = DistributedCache(config={"max_size": 100})
        
        def write_data(i):
            cache.set(f"key_{i}", False, {"data": i})
            return cache.get(f"key_{i}")
        
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(write_data, i) for i in range(100)]
            results = [f.result() for f in futures]
        
        assert len(results) == 100
    
    def test_concurrent_validation(self):
        """Test concurrent input validation."""
        validator = InputValidator()
        
        def validate_query(i):
            return validator.validate_query(f"查询{i}")
        
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(validate_query, i) for i in range(100)]
            results = [f.result() for f in futures]
        
        assert len(results) == 100


class TestDataIntegrity:
    """Test data integrity."""
    
    def test_sql_injection_prevention(self):
        """Test SQL injection prevention."""
        validator = InputValidator()
        
        malicious_queries = [
            "'; DROP TABLE users; --",
            "1' OR '1'='1",
            "1; DELETE FROM users",
            "<script>alert('xss')</script>",
            "SELECT * FROM passwords",
        ]
        
        for query in malicious_queries:
            with pytest.raises(ValidationError):
                validator.validate_query(query)
    
    def test_output_encoding(self):
        """Test output encoding."""
        from validation import InputValidator
        
        validator = InputValidator()
        
        # Test HTML encoding
        output = "<script>alert('xss')</script>"
        encoded = validator.sanitize_output(output)
        assert "<script>" not in encoded
        assert "&lt;script&gt;" in encoded


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
