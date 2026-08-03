"""Funnel Analysis — 漏斗分析与转化率计算。

P2-4: 漏斗分析
- 支持自定义步骤序列
- 每步转化率自动计算
- 步骤间流失率分析
- 支持动态调整步骤定义

使用方式:
    engine = FunnelEngine()
    result = engine.analyze(data, steps=[
        {"name": "浏览", "filter": "event='view'"},
        {"name": "加购", "filter": "event='add_cart'"},
        {"name": "下单", "filter": "event='order'"},
        {"name": "支付", "filter": "event='pay'"},
    ])
"""

from __future__ import annotations

from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
import structlog

logger = structlog.get_logger("funnel_analysis")


@dataclass
class FunnelStep:
    """漏斗步骤定义。"""
    name: str
    filter_expr: str  # SQL 过滤条件或关键词
    user_count: int = 0
    conversion_rate: float = 0.0  # 相对上一步
    overall_rate: float = 0.0  # 相对第一步
    drop_count: int = 0  # 流失用户数
    drop_rate: float = 0.0  # 流失率


@dataclass
class FunnelResult:
    """漏斗分析结果。"""
    steps: List[FunnelStep] = field(default_factory=list)
    total_entrants: int = 0
    total_completers: int = 0
    overall_conversion: float = 0.0
    bottleneck_step: Optional[str] = None
    bottleneck_drop_rate: float = 0.0
    insights: List[str] = field(default_factory=list)


class FunnelEngine:
    """漏斗分析引擎。
    
    以纯内存计算为主，不依赖SQL生成。
    输入为预处理后的数据列表。
    """

    def analyze(self, 
                data: List[Dict[str, Any]], 
                steps: List[Dict[str, Any]],
                id_column: str = "user_id") -> FunnelResult:
        """执行漏斗分析。
        
        Args:
            data: 原始数据列表，每行含 user_id 和步骤相关信息
            steps: 漏斗步骤定义列表
            id_column: 用户唯一标识列名
            
        Returns:
            FunnelResult
        """
        if not data or not steps:
            return FunnelResult(insights=["数据不足，无法分析"])
        
        result = FunnelResult()
        result.total_entrants = len(set(r.get(id_column) for r in data if r.get(id_column)))
        
        prev_users = None
        for i, step_def in enumerate(steps):
            step = FunnelStep(
                name=step_def.get("name", f"Step{i+1}"),
                filter_expr=step_def.get("filter", ""),
            )
            
            # 统计符合当前步骤的用户
            step_users = self._count_step_users(data, step_def, id_column)
            step.user_count = len(step_users)
            
            # 计算转化率
            if i == 0:
                step.conversion_rate = 1.0
                step.overall_rate = 1.0
            else:
                prev_step = result.steps[-1]
                if prev_step.user_count > 0:
                    step.conversion_rate = step.user_count / prev_step.user_count
                    step.drop_count = prev_step.user_count - step.user_count
                    step.drop_rate = step.drop_count / prev_step.user_count
                step.overall_rate = step.user_count / result.total_entrants if result.total_entrants > 0 else 0.0
            
            result.steps.append(step)
            prev_users = step_users
        
        # 最终转化
        if result.steps:
            result.total_completers = result.steps[-1].user_count
            result.overall_conversion = result.total_completers / result.total_entrants if result.total_entrants > 0 else 0.0
        
        # 找瓶颈步骤
        max_drop = 0
        for step in result.steps:
            if step.drop_rate > max_drop:
                max_drop = step.drop_rate
                result.bottleneck_step = step.name
                result.bottleneck_drop_rate = step.drop_rate
        
        # 生成洞察
        result.insights = self._generate_insights(result)
        
        return result

    def _count_step_users(self, data: List[Dict[str, Any]], 
                          step_def: Dict[str, Any], 
                          id_column: str) -> set:
        """统计满足某步骤条件的用户集合。
        
        简单实现：通过 filter_expr 匹配行数据中的关键词。
        """
        filter_expr = step_def.get("filter", "")
        keywords = step_def.get("keywords", [])
        
        matching_users = set()
        for row in data:
            user_id = row.get(id_column)
            if user_id is None:
                continue
            
            # 关键词匹配（在任意列中搜索）
            if keywords:
                row_str = " ".join(str(v) for v in row.values()).lower()
                if any(kw.lower() in row_str for kw in keywords):
                    matching_users.add(user_id)
            elif filter_expr:
                row_str = " ".join(str(v) for v in row.values()).lower()
                if filter_expr.lower() in row_str:
                    matching_users.add(user_id)
            else:
                # 无过滤条件：所有用户
                matching_users.add(user_id)
        
        return matching_users

    def _generate_insights(self, result: FunnelResult) -> List[str]:
        """根据漏斗数据生成分析洞察。"""
        insights = []
        
        if not result.steps:
            return ["无漏斗数据"]
        
        # 整体转化
        if result.overall_conversion < 0.1:
            insights.append(f"🔴 整体转化率仅 {result.overall_conversion:.1%}，需重点优化")
        elif result.overall_conversion < 0.3:
            insights.append(f"🟡 整体转化率 {result.overall_conversion:.1%}，有提升空间")
        else:
            insights.append(f"🟢 整体转化率 {result.overall_conversion:.1%}，表现良好")
        
        # 瓶颈步骤
        if result.bottleneck_step:
            insights.append(
                f"🔍 最大流失在'{result.bottleneck_step}'步骤"
                f"（流失率 {result.bottleneck_drop_rate:.1%}）"
            )
        
        # 步骤间对比
        for i in range(1, len(result.steps)):
            prev = result.steps[i - 1]
            curr = result.steps[i]
            if curr.conversion_rate < 0.5:
                insights.append(
                    f"📉 '{prev.name}'→'{curr.name}' 转化率仅 {curr.conversion_rate:.1%}，"
                    f"流失 {curr.drop_count} 用户"
                )
        
        return insights

    def format_markdown(self, result: FunnelResult) -> str:
        """将漏斗结果格式化为Markdown文本。"""
        if not result.steps:
            return "无漏斗分析数据"
        
        lines = ["## 漏斗分析\n"]
        
        # 步骤表格
        lines.append("| 步骤 | 用户数 | 步骤转化率 | 整体转化率 | 流失数 |")
        lines.append("|------|--------|-----------|-----------|--------|")
        for step in result.steps:
            lines.append(
                f"| {step.name} | {step.user_count:,} | "
                f"{step.conversion_rate:.1%} | {step.overall_rate:.1%} | "
                f"{step.drop_count:,} |"
            )
        
        lines.append(f"\n**整体转化率**: {result.overall_conversion:.1%}")
        if result.bottleneck_step:
            lines.append(f"**最大瓶颈**: {result.bottleneck_step}（流失率 {result.bottleneck_drop_rate:.1%}）")
        
        lines.append("\n### 分析洞察\n")
        for insight in result.insights:
            lines.append(f"- {insight}")
        
        return "\n".join(lines)


# ── 预设漏斗模板 ──

PRESET_FUNNELS = {
    "ecommerce_purchase": {
        "name": "电商购买漏斗",
        "steps": [
            {"name": "浏览商品", "keywords": ["view", "浏览"]},
            {"name": "加入购物车", "keywords": ["add_cart", "cart", "加购"]},
            {"name": "下单", "keywords": ["order", "下单"]},
            {"name": "支付成功", "keywords": ["pay", "paid", "支付"]},
        ],
    },
    "user_onboarding": {
        "name": "用户注册漏斗",
        "steps": [
            {"name": "访问首页", "keywords": ["visit", "访问"]},
            {"name": "注册", "keywords": ["register", "注册"]},
            {"name": "首次使用", "keywords": ["first_use", "首次"]},
            {"name": "7日留存", "keywords": ["retained", "留存"]},
        ],
    },
    "content_engagement": {
        "name": "内容互动漏斗",
        "steps": [
            {"name": "展示", "keywords": ["impression", "展示"]},
            {"name": "点击", "keywords": ["click", "点击"]},
            {"name": "深度阅读", "keywords": ["read", "阅读"]},
            {"name": "分享/收藏", "keywords": ["share", "fav", "分享", "收藏"]},
        ],
    },
}
