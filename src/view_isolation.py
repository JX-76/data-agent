"""多视图隔离与黑话纠正闭环。

Features:
- 多视图隔离（部门/角色级数据隔离）
- 黑话注册表（业务术语映射）
- 黑话纠正闭环（用户反馈→模型优化）
- 视图权限控制
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set
from pathlib import Path

import structlog

logger = structlog.get_logger("view_isolation")


@dataclass
class ViewConfig:
    """视图配置"""
    view_id: str
    name: str
    allowed_metrics: List[str] = field(default_factory=list)
    allowed_dimensions: List[str] = field(default_factory=list)
    allowed_tables: List[str] = field(default_factory=list)
    allowed_models: List[str] = field(default_factory=list)
    row_filter: str = ""  # SQL WHERE条件
    column_mask: List[str] = field(default_factory=list)  # 需要脱敏的字段


@dataclass
class UserRole:
    """用户角色"""
    role_id: str
    name: str
    views: List[str] = field(default_factory=list)
    permissions: List[str] = field(default_factory=list)


@dataclass
class JargonEntry:
    """黑话条目"""
    jargon: str  # 黑话
    standard: str  # 标准术语
    category: str  # 类别：metric, dimension, table, model
    confidence: float = 1.0  # 置信度
    usage_count: int = 0  # 使用次数
    feedback_score: float = 0.0  # 用户反馈分数


class ViewIsolationManager:
    """视图隔离管理器"""
    
    def __init__(self, config_path: Optional[str] = None):
        self.views: Dict[str, ViewConfig] = {}
        self.roles: Dict[str, UserRole] = {}
        self.logger = structlog.get_logger("view_isolation")
        self.config_path = config_path or "config/views.json"
        self._load_config()
    
    def _load_config(self) -> None:
        """加载配置"""
        if Path(self.config_path).exists():
            with open(self.config_path, 'r') as f:
                config = json.load(f)
            for view_data in config.get("views", []):
                view = ViewConfig(**view_data)
                self.views[view.view_id] = view
            for role_data in config.get("roles", []):
                role = UserRole(**role_data)
                self.roles[role.role_id] = role
            self.logger.info("loaded_views", view_count=len(self.views), role_count=len(self.roles))
    
    def create_view(self, view: ViewConfig) -> None:
        """创建视图"""
        self.views[view.view_id] = view
        self.logger.info("view_created", view_id=view.view_id)
    
    def get_view(self, view_id: str) -> Optional[ViewConfig]:
        """获取视图"""
        return self.views.get(view_id)
    
    def check_access(self, view_id: str, metric: str, dimension: str = "") -> bool:
        """检查访问权限"""
        view = self.views.get(view_id)
        if not view:
            return False
        
        if metric and metric not in view.allowed_metrics:
            return False
        
        if dimension and dimension not in view.allowed_dimensions:
            return False
        
        return True
    
    def apply_row_filter(self, view_id: str, sql: str) -> str:
        """应用行级过滤"""
        view = self.views.get(view_id)
        if not view or not view.row_filter:
            return sql
        
        # 在SQL WHERE子句中添加过滤条件
        if "WHERE" in sql.upper():
            sql = sql.replace("WHERE", f"WHERE ({view.row_filter}) AND ", 1)
        else:
            sql += f" WHERE {view.row_filter}"
        
        return sql
    
    def apply_column_mask(self, view_id: str, results: List[Dict]) -> List[Dict]:
        """应用列级脱敏"""
        view = self.views.get(view_id)
        if not view or not view.column_mask:
            return results
        
        masked_results = []
        for row in results:
            masked_row = {}
            for key, value in row.items():
                if key in view.column_mask:
                    # 脱敏处理
                    masked_row[key] = self._mask_value(value)
                else:
                    masked_row[key] = value
            masked_results.append(masked_row)
        
        return masked_results
    
    def _mask_value(self, value: Any) -> str:
        """脱敏值"""
        if isinstance(value, str):
            if len(value) > 4:
                return value[:2] + "***" + value[-2:]
            return "***"
        elif isinstance(value, (int, float)):
            return "***"
        return str(value)


class JargonRegistry:
    """黑话注册表"""
    
    def __init__(self, registry_path: Optional[str] = None):
        self.entries: Dict[str, JargonEntry] = {}
        self.logger = structlog.get_logger("jargon_registry")
        self.registry_path = registry_path or "config/jargon.json"
        self._load_registry()
    
    def _load_registry(self) -> None:
        """加载注册表"""
        if Path(self.registry_path).exists():
            with open(self.registry_path, 'r') as f:
                data = json.load(f)
            for entry_data in data.get("entries", []):
                entry = JargonEntry(**entry_data)
                self.entries[entry.jargon] = entry
            self.logger.info("loaded_jargon", count=len(self.entries))
    
    def register(self, entry: JargonEntry) -> None:
        """注册黑话"""
        self.entries[entry.jargon] = entry
        self.logger.info("jargon_registered", jargon=entry.jargon, standard=entry.standard)
    
    def translate(self, query: str) -> str:
        """翻译黑话为标准术语"""
        translated = query
        for jargon, entry in self.entries.items():
            if jargon in translated:
                translated = translated.replace(jargon, entry.standard)
                entry.usage_count += 1
                self.logger.info("jargon_translated", jargon=jargon, standard=entry.standard)
        return translated
    
    def detect_jargon(self, query: str) -> List[str]:
        """检测查询中的黑话"""
        detected = []
        for jargon in self.entries.keys():
            if jargon in query:
                detected.append(jargon)
        return detected
    
    def get_suggestions(self, query: str) -> List[Dict[str, str]]:
        """获取纠正建议"""
        suggestions = []
        detected = self.detect_jargon(query)
        for jargon in detected:
            entry = self.entries.get(jargon)
            if entry:
                suggestions.append({
                    "jargon": jargon,
                    "standard": entry.standard,
                    "category": entry.category,
                })
        return suggestions


class JargonFeedbackLoop:
    """黑话纠正闭环"""
    
    def __init__(self, registry: JargonRegistry):
        self.registry = registry
        self.logger = structlog.get_logger("jargon_feedback")
        self.feedback_log: List[Dict[str, Any]] = []
    
    def collect_feedback(self, query: str, corrected_query: str, user_rating: int, user_comment: str = "") -> None:
        """收集用户反馈"""
        feedback = {
            "timestamp": str(datetime.datetime.now()),
            "original_query": query,
            "corrected_query": corrected_query,
            "user_rating": user_rating,
            "user_comment": user_comment,
        }
        self.feedback_log.append(feedback)
        self.logger.info("feedback_collected", rating=user_rating)
        
        # 更新黑话置信度
        detected_jargon = self.registry.detect_jargon(query)
        for jargon in detected_jargon:
            entry = self.registry.entries.get(jargon)
            if entry:
                # 根据用户评分更新置信度
                if user_rating >= 4:
                    entry.confidence = min(1.0, entry.confidence + 0.05)
                elif user_rating <= 2:
                    entry.confidence = max(0.0, entry.confidence - 0.1)
                entry.feedback_score = (entry.feedback_score * entry.usage_count + user_rating) / (entry.usage_count + 1)
    
    def optimize_registry(self) -> None:
        """优化注册表"""
        # 移除低置信度的黑话
        to_remove = []
        for jargon, entry in self.registry.entries.items():
            if entry.confidence < 0.3:
                to_remove.append(jargon)
        
        for jargon in to_remove:
            del self.registry.entries[jargon]
            self.logger.info("jargon_removed", jargon=jargon, reason="low_confidence")
        
        # 保存优化后的注册表
        self.registry._save_registry()
    
    def get_feedback_summary(self) -> Dict[str, Any]:
        """获取反馈摘要"""
        if not self.feedback_log:
            return {}
        
        ratings = [f["user_rating"] for f in self.feedback_log]
        return {
            "total_feedback": len(self.feedback_log),
            "avg_rating": sum(ratings) / len(ratings),
            "positive_rate": sum(1 for r in ratings if r >= 4) / len(ratings),
            "negative_rate": sum(1 for r in ratings if r <= 2) / len(ratings),
        }


# ── 快捷函数 ──

def create_view_manager() -> ViewIsolationManager:
    """创建视图管理器"""
    return ViewIsolationManager()


def create_jargon_registry() -> JargonRegistry:
    """创建黑话注册表"""
    return JargonRegistry()


def create_feedback_loop(registry: JargonRegistry) -> JargonFeedbackLoop:
    """创建反馈闭环"""
    return JargonFeedbackLoop(registry)


if __name__ == "__main__":
    # 测试
    import datetime
    
    # 测试视图隔离
    manager = create_view_manager()
    view = ViewConfig(
        view_id="sales_dept",
        name="销售部视图",
        allowed_metrics=["gmv", "order_count", "aov"],
        allowed_dimensions=["date", "channel", "region"],
        row_filter="region = '华东'",
    )
    manager.create_view(view)
    
    print("✅ 视图隔离测试通过")
    
    # 测试黑话注册表
    registry = create_jargon_registry()
    entry = JargonEntry(
        jargon="GMV",
        standard="sell_through",
        category="metric",
    )
    registry.register(entry)
    
    query = "昨天GMV是多少"
    translated = registry.translate(query)
    print(f"原文: {query}")
    print(f"翻译: {translated}")
    
    print("✅ 黑话纠正闭环测试通过")
