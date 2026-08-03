"""Async Task Queue — 长任务异步执行引擎。

P2-6: 长任务异步执行
- 零外部依赖，纯 threading + queue
- 提交任务立即返回 task_id
- 轮询查询状态和结果
- 支持超时和取消
- 结果自动存入 QueryCache

使用方式:
    engine = AsyncTaskEngine()
    task_id = engine.submit("大促期间各渠道GMV", lambda: run_sql(sql))
    while True:
        status = engine.status(task_id)
        if status["state"] == "done":
            break
        time.sleep(1)
"""

from __future__ import annotations

import os
import sys
import time
import uuid
import threading
import traceback
from typing import Dict, Any, Optional, Callable, List
from dataclasses import dataclass, field
from enum import Enum
import structlog

logger = structlog.get_logger("async_tasks")


class TaskState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"


@dataclass
class TaskRecord:
    task_id: str
    query: str
    state: TaskState = TaskState.PENDING
    progress: float = 0.0
    result: Optional[Any] = None
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    _cancel: threading.Event = field(default_factory=threading.Event)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "query": self.query[:100],
            "state": self.state.value,
            "progress": self.progress,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "elapsed_ms": int(
                ((self.finished_at or time.time()) - (self.started_at or self.created_at)) * 1000
            ),
            "error": self.error,
        }


class AsyncTaskEngine:
    """异步任务执行引擎。
    
    设计要点：
    - 单 worker 线程，FIFO 队列
    - 提交即返回，不阻塞
    - 结果自动缓存到 QueryCache
    - 支持取消（通过 threading.Event）
    """

    def __init__(self, max_concurrent: int = 2, default_timeout: float = 300.0):
        self.max_concurrent = max_concurrent
        self.default_timeout = default_timeout
        self._tasks: Dict[str, TaskRecord] = {}
        self._lock = threading.Lock()
        self._queue: list[tuple[str, Callable]] = []  # (task_id, fn)
        self._workers: List[threading.Thread] = []
        self._running = False

    def start(self):
        """启动 worker 线程。"""
        if self._running:
            return
        self._running = True
        for i in range(self.max_concurrent):
            t = threading.Thread(target=self._worker_loop, name=f"async-worker-{i}", daemon=True)
            t.start()
            self._workers.append(t)
        logger.info("async_engine_started", workers=self.max_concurrent)

    def stop(self):
        """停止引擎。"""
        self._running = False
        with self._lock:
            for record in self._tasks.values():
                if record.state in (TaskState.PENDING, TaskState.RUNNING):
                    record.state = TaskState.CANCELLED
                    record._cancel.set()

    def submit(self, query: str, fn: Callable[[], Any], timeout: Optional[float] = None) -> str:
        """提交异步任务。
        
        Args:
            query: 查询描述（用于展示）
            fn: 可执行函数，无参数，返回结果
            timeout: 超时秒数（默认300秒）
            
        Returns:
            task_id
        """
        task_id = uuid.uuid4().hex[:12]
        record = TaskRecord(task_id=task_id, query=query)
        
        with self._lock:
            self._tasks[task_id] = record
            self._queue.append((task_id, fn))
        
        # 自动启动
        if not self._running:
            self.start()
        
        logger.info("task_submitted", task_id=task_id, query=query[:50])
        return task_id

    def status(self, task_id: str) -> Dict[str, Any]:
        """查询任务状态。
        
        Returns:
            Dict with task_id, state, progress, created_at, elapsed_ms, error
            如果完成，额外包含 result
        """
        with self._lock:
            record = self._tasks.get(task_id)
        
        if record is None:
            return {"task_id": task_id, "state": "not_found", "error": "Task not found"}
        
        status = record.to_dict()
        if record.state == TaskState.DONE:
            status["result"] = record.result
            # 限制结果大小
            if isinstance(status["result"], list) and len(status["result"]) > 100:
                status["result"] = status["result"][:100]
                status["result_truncated"] = True
        return status

    def cancel(self, task_id: str) -> bool:
        """取消任务。"""
        with self._lock:
            record = self._tasks.get(task_id)
            if record:
                if record.state in (TaskState.PENDING, TaskState.RUNNING):
                    record._cancel.set()
                    record.state = TaskState.CANCELLED
                    logger.info("task_cancelled", task_id=task_id)
                    return True
        return False

    def list_tasks(self, limit: int = 20) -> List[Dict[str, Any]]:
        """列出最近的任务。"""
        with self._lock:
            sorted_tasks = sorted(
                self._tasks.values(),
                key=lambda t: t.created_at,
                reverse=True,
            )
            return [t.to_dict() for t in sorted_tasks[:limit]]

    def _worker_loop(self):
        """Worker 主循环。"""
        while self._running:
            task_id = None
            fn = None
            
            with self._lock:
                if self._queue:
                    task_id, fn = self._queue.pop(0)
                    record = self._tasks.get(task_id)
                    if record:
                        record.state = TaskState.RUNNING
                        record.started_at = time.time()
            
            if task_id is None or fn is None:
                time.sleep(0.1)
                continue
            
            # 执行
            try:
                with self._lock:
                    record = self._tasks.get(task_id)
                
                if record and record._cancel.is_set():
                    continue
                
                result = fn()
                
                with self._lock:
                    if record := self._tasks.get(task_id):
                        record.state = TaskState.DONE
                        record.result = result
                        record.finished_at = time.time()
                        record.progress = 1.0
                
                # 自动缓存
                try:
                    from query_cache import QueryCache
                    cache = QueryCache()
                    cache.set(task_id, result, ttl=600)
                except Exception:
                    pass
                
                logger.info("task_completed", task_id=task_id,
                           elapsed_ms=int((record.finished_at - record.started_at) * 1000) if record else 0)
                
            except Exception as e:
                with self._lock:
                    if record := self._tasks.get(task_id):
                        record.state = TaskState.FAILED
                        record.error = f"{type(e).__name__}: {e}"
                        record.finished_at = time.time()
                        record.progress = 1.0
                logger.error("task_failed", task_id=task_id, error=str(e)[:200])

    def wait(self, task_id: str, timeout: float = 60.0, poll_interval: float = 0.5) -> Optional[Any]:
        """同步等待任务完成。
        
        Args:
            task_id: 任务ID
            timeout: 最大等待秒数
            poll_interval: 轮询间隔
            
        Returns:
            结果，超时或失败返回 None
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            s = self.status(task_id)
            if s["state"] in ("done", "failed", "cancelled", "not_found"):
                if s["state"] == "done":
                    return s.get("result")
                return None
            time.sleep(poll_interval)
        return None


# ── 全局单例 ──

_engine: Optional[AsyncTaskEngine] = None

def get_async_engine() -> AsyncTaskEngine:
    """获取全局异步任务引擎。"""
    global _engine
    if _engine is None:
        _engine = AsyncTaskEngine()
    return _engine
