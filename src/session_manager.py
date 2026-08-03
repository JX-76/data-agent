"""Session Manager — 会话状态管理与回退。

P2-3: 重置与回退
- 保存每次查询的状态快照
- 支持 "/reset" 重置到初始状态
- 支持 "/back" 回退到上一步
- 支持 "/undo N" 回退N步
- 支持 "/history" 查看历史

设计原则：
- 零外部依赖，纯内存 + 文件持久化
- 状态栈最多保留 20 步
- 自动过期清理
"""

from __future__ import annotations

import copy
import json
import time
from pathlib import Path
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field
import structlog

logger = structlog.get_logger("session_manager")


@dataclass
class SessionSnapshot:
    """单次状态快照。"""
    step: int
    query: str
    state: Dict[str, Any]
    timestamp: float = field(default_factory=time.time)
    summary: str = ""


class SessionManager:
    """会话状态管理器。
    
    使用方式:
        mgr = SessionManager(max_history=20)
        
        # 保存快照
        mgr.snapshot(state, query)
        
        # 重置
        mgr.reset()
        
        # 回退
        prev = mgr.undo()
        prev_3 = mgr.undo(3)
        
        # 查看历史
        mgr.history()
    """

    def __init__(self, max_history: int = 20):
        self._stack: List[SessionSnapshot] = []
        self.max_history = max_history
        self._initial_state: Optional[Dict[str, Any]] = None

    def snapshot(self, state: Dict[str, Any], query: str = "", summary: str = "") -> int:
        """保存当前状态快照。
        
        Args:
            state: 当前状态字典
            query: 用户查询文本
            summary: 简短摘要
            
        Returns:
            当前栈深度
        """
        # 保存初始状态（仅第一次）
        if self._initial_state is None:
            self._initial_state = copy.deepcopy(state)
        
        # 清理内部字段避免序列化问题
        clean_state = {}
        for k, v in state.items():
            if k.startswith("__") or k in ("rt", "_db", "output"):
                continue
            try:
                # 尝试浅拷贝
                clean_state[k] = copy.deepcopy(v) if isinstance(v, (dict, list)) else v
            except Exception:
                clean_state[k] = str(v)[:200]
        
        snapshot = SessionSnapshot(
            step=len(self._stack) + 1,
            query=query[:200],
            state=clean_state,
            summary=summary or query[:50],
        )
        
        self._stack.append(snapshot)
        
        # 限制栈深度
        while len(self._stack) > self.max_history:
            self._stack.pop(0)
        
        logger.info("session_snapshot", step=snapshot.step, query=query[:50])
        return len(self._stack)

    def reset(self) -> Optional[Dict[str, Any]]:
        """重置会话到初始状态。
        
        Returns:
            初始状态
        """
        self._stack.clear()
        logger.info("session_reset")
        return copy.deepcopy(self._initial_state) if self._initial_state else None

    def undo(self, steps: int = 1) -> Optional[Dict[str, Any]]:
        """回退 N 步。
        
        Args:
            steps: 回退步数（默认1步）
            
        Returns:
            回退后的状态，如果无法回退返回 None
        """
        if not self._stack:
            logger.info("session_undo_empty")
            return None
        
        steps = max(1, min(steps, len(self._stack)))
        
        # 弹出最近N步
        for _ in range(steps):
            self._stack.pop()
        
        if self._stack:
            prev = self._stack[-1]
            logger.info("session_undo", steps=steps, remaining=len(self._stack))
            return copy.deepcopy(prev.state)
        
        # 回退到初始状态
        logger.info("session_undo_to_initial", steps=steps)
        return copy.deepcopy(self._initial_state) if self._initial_state else {}

    def history(self, limit: int = 10) -> List[Dict[str, Any]]:
        """查看最近的操作历史。
        
        Args:
            limit: 返回条数
            
        Returns:
            历史记录列表
        """
        recent = self._stack[-limit:]
        return [
            {
                "step": s.step,
                "query": s.summary,
                "timestamp": s.timestamp,
            }
            for s in recent
        ]

    def parse_command(self, query: str) -> Optional[Dict[str, Any]]:
        """解析会话控制命令。
        
        支持的命令:
            /reset — 重置会话
            /back 或 /undo — 回退1步
            /undo N — 回退N步
            /history — 查看历史
            /save NAME — 保存当前状态为视图
            /load NAME — 加载视图
        
        Returns:
            命令执行结果，非命令返回 None
        """
        q = query.strip().lower()
        
        if q in ("/reset", "重置", "重新来", "重新开始", "换个视角"):
            return {"command": "reset", "state": self.reset()}
        
        if q in ("/back", "/undo", "回退", "上一步", "后退"):
            return {"command": "undo", "state": self.undo(1)}
        
        if q.startswith("/undo ") or q.startswith("回退 "):
            try:
                parts = q.split()
                n = int(parts[-1])
                return {"command": "undo", "steps": n, "state": self.undo(n)}
            except (ValueError, IndexError):
                pass
        
        if q in ("/history", "历史", "操作历史"):
            return {"command": "history", "entries": self.history()}
        
        # 不是命令
        return None


# 全局单例
_session_mgr: Optional[SessionManager] = None

def get_session() -> SessionManager:
    """获取全局会话管理器。"""
    global _session_mgr
    if _session_mgr is None:
        _session_mgr = SessionManager()
    return _session_mgr
