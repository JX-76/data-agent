"""Root Cause / Anomaly Attribution — 异动归因与贡献度拆解。

P2-5: 异动归因增强
- 维度贡献度计算（如"A省下跌贡献60%"）
- 自动定位最大波动因子
- 归因报告自动生成
- 支持同比/环比/前后对比三种基准

使用方式:
    engine = RootCauseEngine()
    result = engine.analyze(
        current=[{"channel":"taobao","gmv":100}, ...],
        baseline=[{"channel":"taobao","gmv":120}, ...],
        metric="gmv",
        dimensions=["channel"],
    )
"""

from __future__ import annotations

from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field
import structlog

logger = structlog.get_logger("root_cause")


@dataclass
class DimensionContribution:
    """单个维度的贡献度。"""
    dimension: str  # 维度名
    value: str  # 维度值
    current: float  # 当前值
    baseline: float  # 基准值
    change: float  # 变化量
    change_pct: float  # 变化率 (0.0~1.0)
    contribution_pct: float  # 贡献度 (占总变化的百分比)
    direction: str  # "up" or "down"


@dataclass
class RootCauseResult:
    """异动归因结果。"""
    metric: str
    total_current: float
    total_baseline: float
    total_change: float
    total_change_pct: float
    direction: str  # "up" or "down"
    contributions: List[DimensionContribution] = field(default_factory=list)
    top_driver: Optional[DimensionContribution] = None
    insights: List[str] = field(default_factory=list)


class RootCauseEngine:
    """异动归因引擎。
    
    核心算法：对于每个维度的每个值，计算其变化量占总变化量的比例。
    贡献度 = 该维度值的变化量 / 总变化量的绝对值。
    """

    def analyze(self, 
                current: List[Dict[str, Any]], 
                baseline: List[Dict[str, Any]],
                metric: str,
                dimensions: List[str]) -> RootCauseResult:
        """执行异动归因分析。
        
        Args:
            current: 当前期数据
            baseline: 基准期数据
            metric: 指标列名
            dimensions: 维度列名列表
            
        Returns:
            RootCauseResult
        """
        # 按维度值聚合
        current_map = self._aggregate_by_dims(current, metric, dimensions)
        baseline_map = self._aggregate_by_dims(baseline, metric, dimensions)
        
        # 计算总量
        total_current = sum(row.get(metric, 0) for row in current)
        total_baseline = sum(row.get(metric, 0) for row in baseline)
        total_change = total_current - total_baseline
        total_change_pct = total_change / total_baseline if total_baseline != 0 else 0.0
        
        result = RootCauseResult(
            metric=metric,
            total_current=total_current,
            total_baseline=total_baseline,
            total_change=total_change,
            total_change_pct=total_change_pct,
            direction="up" if total_change >= 0 else "down",
        )
        
        # 收集所有维度值
        all_keys = set(current_map.keys()) | set(baseline_map.keys())
        
        contributions = []
        for key in all_keys:
            curr_val = current_map.get(key, 0.0)
            base_val = baseline_map.get(key, 0.0)
            change = curr_val - base_val
            
            if change == 0:
                continue
            
            change_pct = change / base_val if base_val != 0 else (1.0 if curr_val > 0 else -1.0)
            contribution_pct = abs(change) / abs(total_change) if total_change != 0 else 0.0
            
            # 解析维度值
            dim_parts = key.split("||")
            
            contributions.append(DimensionContribution(
                dimension=dimensions[0] if len(dimensions) == 1 else "|".join(dimensions),
                value=dim_parts[0] if len(dim_parts) == 1 else "|".join(dim_parts),
                current=curr_val,
                baseline=base_val,
                change=change,
                change_pct=change_pct,
                contribution_pct=contribution_pct,
                direction="up" if change >= 0 else "down",
            ))
        
        # 按贡献度降序排列
        contributions.sort(key=lambda c: c.contribution_pct, reverse=True)
        result.contributions = contributions[:20]  # TOP 20
        
        # 找出最大驱动因子
        if contributions:
            result.top_driver = contributions[0]
        
        # 生成洞察
        result.insights = self._generate_insights(result)
        
        return result

    def _aggregate_by_dims(self, data: List[Dict[str, Any]], 
                           metric: str, dimensions: List[str]) -> Dict[str, float]:
        """按维度值聚合指标。"""
        agg = {}
        for row in data:
            key_parts = []
            for dim in dimensions:
                key_parts.append(str(row.get(dim, "unknown")))
            key = "||".join(key_parts)
            
            val = row.get(metric, 0) or 0
            try:
                val = float(val)
            except (TypeError, ValueError):
                val = 0.0
            
            agg[key] = agg.get(key, 0.0) + val
        
        return agg

    def _generate_insights(self, result: RootCauseResult) -> List[str]:
        """生成归因洞察。"""
        insights = []
        
        direction_text = "增长" if result.direction == "up" else "下降"
        insights.append(
            f"📊 {result.metric}整体{direction_text} {abs(result.total_change):,.1f}"
            f"（{result.total_change_pct:+.1%}）"
        )
        
        if result.top_driver:
            driver_dir = "增长" if result.top_driver.direction == "up" else "下降"
            insights.append(
                f"🔍 最大驱动因子: {result.top_driver.dimension}='{result.top_driver.value}'"
                f" {driver_dir} {abs(result.top_driver.change):,.1f}"
                f"（贡献度 {result.top_driver.contribution_pct:.0%}）"
            )
        
        # TOP 3 贡献
        top3 = [c for c in result.contributions[:3] if c.contribution_pct > 0.05]
        if len(top3) > 1:
            contributors = ", ".join(
                f"{c.value}({c.contribution_pct:.0%})" for c in top3
            )
            insights.append(f"💡 TOP 3 贡献因子: {contributors}")
        
        # 与总量方向相反的因子
        opposite = [
            c for c in result.contributions 
            if c.direction != result.direction and c.contribution_pct > 0.01
        ][:3]
        if opposite:
            oppo_list = ", ".join(f"{c.value}(+{abs(c.change):,.1f})" for c in opposite)
            insights.append(f"✅ 对冲因子（与整体方向相反）: {oppo_list}")
        
        return insights

    def format_markdown(self, result: RootCauseResult) -> str:
        """格式化为Markdown。"""
        lines = [f"## 异动归因分析: {result.metric}\n"]
        lines.append(f"**总变化**: {result.total_baseline:,.1f} → {result.total_current:,.1f} "
                    f"（{result.total_change_pct:+.1%}）\n")
        
        lines.append("### 维度贡献排名\n")
        lines.append("| 维度值 | 当期 | 基期 | 变化 | 变化率 | 贡献度 |")
        lines.append("|--------|------|------|------|--------|--------|")
        
        for c in result.contributions[:10]:
            arrows = "↑" if c.direction == "up" else "↓"
            lines.append(
                f"| {c.value} | {c.current:,.1f} | {c.baseline:,.1f} | "
                f"{arrows} {abs(c.change):,.1f} | {c.change_pct:+.1%} | {c.contribution_pct:.0%} |"
            )
        
        lines.append("\n### 分析洞察\n")
        for insight in result.insights:
            lines.append(f"- {insight}")
        
        return "\n".join(lines)


    def compare_two_periods(self,
                            current: List[Dict[str, Any]],
                            baseline: List[Dict[str, Any]],
                            metric: str,
                            dimensions: List[str]) -> Dict[str, Any]:
        """简单API：比较两个时期的指标并返回归因结果。
        
        Returns:
            字典包含归因结果、格式化的markdown
        """
        result = self.analyze(current, baseline, metric, dimensions)
        return {
            "metric": metric,
            "total_current": result.total_current,
            "total_baseline": result.total_baseline,
            "total_change": result.total_change,
            "total_change_pct": result.total_change_pct,
            "direction": result.direction,
            "top_driver": {
                "dimension": result.top_driver.dimension,
                "value": result.top_driver.value,
                "change": result.top_driver.change,
                "contribution_pct": result.top_driver.contribution_pct,
            } if result.top_driver else None,
            "contributions": [
                {
                    "value": c.value,
                    "change": c.change,
                    "contribution_pct": c.contribution_pct,
                }
                for c in result.contributions[:5]
            ],
            "insights": result.insights,
            "markdown": self.format_markdown(result),
        }

# ── 向后兼容别名 ──

RootCauseAnalyzer = RootCauseEngine  # 旧API兼容

def find_root_cause(*args, **kwargs):
    """旧API兼容函数。
    
    支持两种调用方式:
        旧: find_root_cause(metric, current, baseline, dimension)
        新: find_root_cause(current, baseline, metric, dimensions)
    """
    engine = RootCauseEngine()
    
    # 检测调用方式：如果第一个参数是字符串，按旧API解析
    if args and isinstance(args[0], str):
        metric = args[0]
        current = args[1] if len(args) > 1 else []
        baseline = args[2] if len(args) > 2 else []
        dimensions = [args[3]] if len(args) > 3 and isinstance(args[3], str) else (args[3] if len(args) > 3 else [])
    else:
        current = args[0] if args else []
        baseline = args[1] if len(args) > 1 else []
        metric = args[2] if len(args) > 2 else "gmv"
        dimensions = args[3] if len(args) > 3 else []
    
    rc_result = engine.compare_two_periods(current, baseline, metric, list(dimensions) if dimensions else [])
    rc_result["metric"] = metric
    return _LegacyResult(rc_result)


# ── 兼容旧 API 的包装类 ──

class _RootCauseAnalyzerCompat:
    """向后兼容 RootCauseAnalyzer（旧API）。"""
    
    def analyze(self, metric, current, previous, dimension):
        """旧API: analyze(metric, current, previous, dimension)"""
        engine = RootCauseEngine()
        dims = [dimension] if isinstance(dimension, str) else dimension
        return engine.compare_two_periods(current, previous, metric, dims)

# 覆盖之前导入的 RootCauseAnalyzer（确保兼容）
RootCauseAnalyzer = _RootCauseAnalyzerCompat


class _LegacyResult:
    """包装 RootCauseResult 为旧API期望的格式。"""
    
    def __init__(self, rc_result: dict):
        self.metric = rc_result["metric"]
        self.primary_cause = type('_Cause', (), {
            'dimension': rc_result.get("top_driver", {}).get("dimension", ""),
            'value': rc_result.get("top_driver", {}).get("value", ""),
            'contribution_pct': rc_result.get("top_driver", {}).get("contribution_pct", 0),
        })() if rc_result.get("top_driver") else None
        self.findings = rc_result.get("insights", [])
        self.recommendations = rc_result.get("insights", [])
        self.contributions = [
            type('_Contribution', (), {
                'dimension': c.get("dimension", ""),
                'value': c.get("value", ""),
                'contribution_pct': c.get("contribution_pct", 0),
            })() for c in rc_result.get("contributions", [])
        ]


# 覆盖之前的 RootCauseAnalyzer
class RootCauseAnalyzerCompat:
    """向后兼容旧API。"""
    
    def analyze(self, metric, current, previous, dimension):
        engine = RootCauseEngine()
        dims = [dimension] if isinstance(dimension, str) else dimension
        rc_result = engine.compare_two_periods(current, previous, metric, dims)
        rc_result["metric"] = metric  # 确保有metric字段
        return _LegacyResult(rc_result)
    
    def multi_dimension_analysis(self, metric, current_data, previous_data):
        engine = RootCauseEngine()
        results = {}
        for dim, curr in current_data.items():
            prev = previous_data.get(dim, [])
            rc = engine.compare_two_periods(curr, prev, metric, [dim])
            rc["metric"] = metric
            results[dim] = _LegacyResult(rc)
        return results


RootCauseAnalyzer = RootCauseAnalyzerCompat
