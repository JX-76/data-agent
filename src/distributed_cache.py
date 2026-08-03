"""Distributed cache with Redis backend and local fallback.

Supports:
- Redis for distributed caching (production)
- In-memory LRU cache (development/fallback)
- TTL-based expiration
- Cache statistics
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Optional, Any

# Try to import redis, but don't fail if not available
try:
    import redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False


class CacheBackend:
    """Abstract cache backend interface."""
    
    def get(self, key: str) -> Optional[dict]:
        raise NotImplementedError
    
    def set(self, key: str, value: dict, ttl: int = 300) -> None:
        raise NotImplementedError
    
    def delete(self, key: str) -> None:
        raise NotImplementedError
    
    def clear(self) -> None:
        raise NotImplementedError
    
    def stats(self) -> dict:
        raise NotImplementedError


class InMemoryCache(CacheBackend):
    """Thread-safe in-memory cache with TTL."""
    
    def __init__(self, max_size: int = 500):
        self.max_size = max_size
        self._store: dict[str, tuple[float, dict]] = {}
        self._hits = 0
        self._misses = 0
        self._lock = __import__('threading').Lock()
    
    def get(self, key: str) -> Optional[dict]:
        with self._lock:
            entry = self._store.get(key)
            if entry:
                expiry, result = entry
                if time.time() < expiry:
                    self._hits += 1
                    return result
                del self._store[key]
            self._misses += 1
            return None
    
    def set(self, key: str, value: dict, ttl: int = 300) -> None:
        with self._lock:
            self._store[key] = (time.time() + ttl, value)
            # LFU eviction
            if len(self._store) > self.max_size:
                oldest = min(self._store.keys(), key=lambda k: self._store[k][0])
                del self._store[oldest]
    
    def delete(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)
    
    def clear(self) -> None:
        with self._lock:
            self._store.clear()
    
    def stats(self) -> dict:
        with self._lock:
            total = self._hits + self._misses
            return {
                "type": "in_memory",
                "size": len(self._store),
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": f"{self._hits / max(1, total):.1%}",
            }


class RedisCache(CacheBackend):
    """Redis-backed distributed cache."""
    
    def __init__(self, host: str = "localhost", port: int = 6379, db: int = 0,
                 password: Optional[str] = None, max_connections: int = 10):
        if not REDIS_AVAILABLE:
            raise ImportError("redis package not installed. Run: pip install redis")
        
        self._pool = redis.ConnectionPool(
            host=host, port=port, db=db, password=password,
            max_connections=max_connections
        )
        self._client = redis.Redis(connection_pool=self._pool)
        self._hits = 0
        self._misses = 0
    
    def get(self, key: str) -> Optional[dict]:
        try:
            data = self._client.get(key)
            if data:
                self._hits += 1
                return json.loads(data)
            self._misses += 1
            return None
        except redis.RedisError:
            self._misses += 1
            return None
    
    def set(self, key: str, value: dict, ttl: int = 300) -> None:
        try:
            self._client.setex(key, ttl, json.dumps(value))
        except redis.RedisError:
            pass
    
    def delete(self, key: str) -> None:
        try:
            self._client.delete(key)
        except redis.RedisError:
            pass
    
    def clear(self) -> None:
        try:
            self._client.flushdb()
        except redis.RedisError:
            pass
    
    def stats(self) -> dict:
        try:
            info = self._client.info()
            return {
                "type": "redis",
                "host": self._pool.connection_kwargs["host"],
                "port": self._pool.connection_kwargs["port"],
                "hits": self._hits,
                "misses": self._misses,
                "connected_clients": info.get("connected_clients", 0),
            }
        except redis.RedisError:
            return {"type": "redis", "error": "connection_failed"}


class DistributedCache:
    """Cache manager with automatic backend selection.
    
    Priority:
    1. Redis (if configured and available)
    2. In-memory (fallback)
    """
    
    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}
        self._backend = self._create_backend()
    
    def _create_backend(self) -> CacheBackend:
        """Create appropriate cache backend based on configuration."""
        # Check for Redis configuration
        redis_host = self.config.get("redis_host") or __import__('os').getenv("REDIS_HOST")
        
        if redis_host and REDIS_AVAILABLE:
            try:
                backend = RedisCache(
                    host=redis_host,
                    port=self.config.get("redis_port", 6379),
                    db=self.config.get("redis_db", 0),
                    password=self.config.get("redis_password"),
                    max_connections=self.config.get("redis_max_connections", 10)
                )
                print(f"Using Redis cache: {redis_host}:{self.config.get('redis_port', 6379)}")
                return backend
            except Exception as e:
                print(f"Redis connection failed: {e}, falling back to in-memory cache")
        
        # Fallback to in-memory
        return InMemoryCache(max_size=self.config.get("max_size", 500))
    
    def _key(self, query: str, use_llm: bool = False) -> str:
        """Generate cache key from query parameters."""
        return hashlib.sha256(f"{query}|{use_llm}".encode()).hexdigest()[:16]
    
    def get(self, query: str, use_llm: bool = False) -> Optional[dict]:
        key = self._key(query, use_llm)
        return self._backend.get(key)
    
    def set(self, query: str, use_llm: bool, result: dict, ttl: int = 300) -> None:
        key = self._key(query, use_llm)
        self._backend.set(key, result, ttl)
    
    def invalidate(self, query: str, use_llm: bool = False) -> None:
        key = self._key(query, use_llm)
        self._backend.delete(key)
    
    def clear(self) -> None:
        self._backend.clear()
    
    @property
    def stats(self) -> dict:
        return self._backend.stats()


# Singleton instance
cache = DistributedCache()
