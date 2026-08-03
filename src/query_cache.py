"""Query Cache — 基于 SQL 哈希的结果缓存，支持 TTL 和临时视图。

P2-1: 结果缓存与复用
- 相同查询短时间不重复执行
- 支持将结果保存为"临时视图"供后续分析
- 文件持久化，重启后保留

设计原则：
- 零外部依赖，纯文件存储
- SQL 哈希去重
- TTL 过期策略
- 临时视图命名保存
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, asdict
import structlog

logger = structlog.get_logger("query_cache")


@dataclass
class CacheEntry:
    """单条缓存记录。"""
    sql_hash: str
    sql: str
    results: List[Dict[str, Any]]
    created_at: float
    ttl_seconds: int
    hit_count: int = 0
    last_hit_at: float = 0.0
    view_name: Optional[str] = None  # 临时视图名


class QueryCache:
    """查询结果缓存管理器。
    
    使用方式:
        cache = QueryCache(cache_dir=".cache")
        
        # 写入缓存
        cache.set(sql, results, ttl=300)
        
        # 读取缓存
        cached = cache.get(sql)
        
        # 保存为临时视图
        cache.save_view(sql, results, "我的视图")
        
        # 加载临时视图
        view = cache.get_view("我的视图")
    """

    def __init__(self, cache_dir: str = ".cache", default_ttl: int = 300):
        self.cache_dir = Path(cache_dir)
        self.default_ttl = default_ttl
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
        # 内存索引（加速查找）
        self._index: Dict[str, CacheEntry] = {}
        self._views: Dict[str, CacheEntry] = {}
        self._load_index()

    def _hash(self, sql: str) -> str:
        """计算 SQL 的 SHA256 哈希。"""
        return hashlib.sha256(sql.encode("utf-8")).hexdigest()[:16]

    def _cache_path(self, sql_hash: str) -> Path:
        """缓存文件路径。"""
        return self.cache_dir / f"cache_{sql_hash}.json"

    def _load_index(self):
        """从磁盘加载缓存索引。"""
        try:
            for f in self.cache_dir.glob("cache_*.json"):
                try:
                    with open(f, "r", encoding="utf-8") as fp:
                        data = json.load(fp)
                    entry = CacheEntry(**data)
                    # 检查是否过期
                    if time.time() - entry.created_at < entry.ttl_seconds:
                        self._index[entry.sql_hash] = entry
                        if entry.view_name:
                            self._views[entry.view_name] = entry
                except (json.JSONDecodeError, TypeError) as e:
                    logger.warning("cache_index_load_failed", file=str(f), error=str(e)[:200])
                    f.unlink(missing_ok=True)
        except Exception as e:
            logger.warning("cache_index_load_error", error=str(e)[:200])

    def get(self, sql: str) -> Optional[List[Dict[str, Any]]]:
        """获取缓存的查询结果。
        
        Args:
            sql: SQL 查询语句
            
        Returns:
            缓存的结果，如果过期或未命中返回 None
        """
        sql_hash = self._hash(sql)
        
        # 内存索引命中
        entry = self._index.get(sql_hash)
        if entry:
            if time.time() - entry.created_at < entry.ttl_seconds:
                entry.hit_count += 1
                entry.last_hit_at = time.time()
                logger.info("cache_hit", sql_hash=sql_hash, hit_count=entry.hit_count)
                return entry.results
            else:
                # 过期清理
                del self._index[sql_hash]
                self._cache_path(sql_hash).unlink(missing_ok=True)
                logger.info("cache_expired", sql_hash=sql_hash)
        
        # 尝试从磁盘加载
        cache_path = self._cache_path(sql_hash)
        if cache_path.exists():
            try:
                with open(cache_path, "r", encoding="utf-8") as fp:
                    data = json.load(fp)
                entry = CacheEntry(**data)
                if time.time() - entry.created_at < entry.ttl_seconds:
                    entry.hit_count += 1
                    entry.last_hit_at = time.time()
                    self._index[sql_hash] = entry
                    logger.info("cache_hit_disk", sql_hash=sql_hash)
                    return entry.results
                else:
                    cache_path.unlink(missing_ok=True)
            except Exception as e:
                logger.warning("cache_read_failed", sql_hash=sql_hash, error=str(e)[:200])
                cache_path.unlink(missing_ok=True)
        
        logger.info("cache_miss", sql_hash=sql_hash)
        return None

    def set(self, sql: str, results: List[Dict[str, Any]], ttl: Optional[int] = None) -> str:
        """写入缓存。
        
        Args:
            sql: SQL 查询语句
            results: 查询结果
            ttl: 过期时间（秒），默认 300 秒（5分钟）
            
        Returns:
            缓存的 sql_hash
        """
        sql_hash = self._hash(sql)
        ttl_seconds = ttl or self.default_ttl
        
        entry = CacheEntry(
            sql_hash=sql_hash,
            sql=sql[:500],  # 截断长SQL
            results=results,
            created_at=time.time(),
            ttl_seconds=ttl_seconds,
        )
        
        # 写入磁盘
        cache_path = self._cache_path(sql_hash)
        try:
            with open(cache_path, "w", encoding="utf-8") as fp:
                json.dump(asdict(entry), fp, ensure_ascii=False, default=str)
        except Exception as e:
            logger.warning("cache_write_failed", sql_hash=sql_hash, error=str(e)[:200])
        
        # 内存索引
        self._index[sql_hash] = entry
        logger.info("cache_set", sql_hash=sql_hash, ttl=ttl_seconds, rows=len(results))
        return sql_hash

    def save_view(self, sql: str, results: List[Dict[str, Any]], view_name: str) -> str:
        """保存查询结果为临时视图。
        
        Args:
            sql: SQL 查询语句
            results: 查询结果
            view_name: 视图名称
            
        Returns:
            缓存的 sql_hash
        """
        sql_hash = self._hash(sql)
        entry = CacheEntry(
            sql_hash=sql_hash,
            sql=sql[:500],
            results=results,
            created_at=time.time(),
            ttl_seconds=86400,  # 视图默认24小时有效
            view_name=view_name,
        )
        
        cache_path = self._cache_path(sql_hash)
        with open(cache_path, "w", encoding="utf-8") as fp:
            json.dump(asdict(entry), fp, ensure_ascii=False, default=str)
        
        self._index[sql_hash] = entry
        self._views[view_name] = entry
        logger.info("view_saved", view_name=view_name, rows=len(results))
        return sql_hash

    def get_view(self, view_name: str) -> Optional[List[Dict[str, Any]]]:
        """加载已保存的临时视图。
        
        Args:
            view_name: 视图名称
            
        Returns:
            视图数据，如果不存在返回 None
        """
        entry = self._views.get(view_name)
        if entry:
            if time.time() - entry.created_at < entry.ttl_seconds:
                return entry.results
            else:
                del self._views[view_name]
                logger.info("view_expired", view_name=view_name)
        return None

    def list_views(self) -> List[Dict[str, Any]]:
        """列出所有临时视图。
        
        Returns:
            视图列表，每项包含：name, rows, created_at, sql_preview
        """
        now = time.time()
        views = []
        for name, entry in list(self._views.items()):
            if now - entry.created_at < entry.ttl_seconds:
                views.append({
                    "name": name,
                    "rows": len(entry.results),
                    "created_at": entry.created_at,
                    "sql_preview": entry.sql[:100],
                    "hit_count": entry.hit_count,
                })
            else:
                del self._views[name]
        return sorted(views, key=lambda v: v["created_at"], reverse=True)

    def stats(self) -> Dict[str, Any]:
        """缓存统计信息。
        
        Returns:
            字典包含：total_entries, total_hits, total_views, cache_dir
        """
        total_hits = sum(e.hit_count for e in self._index.values())
        return {
            "total_entries": len(self._index),
            "total_hits": total_hits,
            "total_views": len(self._views),
            "cache_dir": str(self.cache_dir),
        }

    def clear(self, older_than: Optional[int] = None):
        """清理缓存。
        
        Args:
            older_than: 清理超过N秒前的缓存，None=全部清理
        """
        now = time.time()
        for sql_hash, entry in list(self._index.items()):
            if older_than is None or (now - entry.created_at) > older_than:
                del self._index[sql_hash]
                if entry.view_name:
                    self._views.pop(entry.view_name, None)
                self._cache_path(sql_hash).unlink(missing_ok=True)
        logger.info("cache_cleared", older_than=older_than, remaining=len(self._index))


# 全局单例
_cache: Optional[QueryCache] = None

def get_cache(cache_dir: str = ".cache") -> QueryCache:
    """获取全局缓存实例。"""
    global _cache
    if _cache is None:
        _cache = QueryCache(cache_dir=cache_dir)
    return _cache
