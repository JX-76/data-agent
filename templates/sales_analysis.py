"""销售分析模板 - 多维度销售分析。

Features:
- 时间维度分析（日/周/月/季）
- 渠道维度分析
- 区域维度分析
- 品类维度分析
- 趋势分析
- 对比分析
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

import structlog

logger = structlog.get_logger("sales_analysis")


@dataclass
class SalesTrend:
    """销售趋势"""
    period: str
    gmv: float = 0.0
    order_count: int = 0
    aov: float = 0.0
    gmv_growth: float = 0.0
    order_growth: float = 0.0


@dataclass
class ChannelAnalysis:
    """渠道分析"""
    channel: str
    gmv: float = 0.0
    order_count: int = 0
    percentage: float = 0.0
    growth: float = 0.0


@dataclass
class RegionAnalysis:
    """区域分析"""
    region: str
    gmv: float = 0.0
    order_count: int = 0
    percentage: float = 0.0
    growth: float = 0.0


class SalesAnalysisTemplate:
    """销售分析模板"""
    
    def __init__(self, db_executor=None):
        self.db = db_executor
        self.logger = structlog.get_logger("sales_analysis")
    
    def generate(self, start_date: str, end_date: str) -> Dict[str, Any]:
        """生成销售分析报告"""
        self.logger.info("generating_sales_report", start=start_date, end=end_date)
        
        # 1. 销售趋势
        trends = self._get_sales_trends(start_date, end_date)
        
        # 2. 渠道分析
        channels = self._get_channel_analysis(start_date, end_date)
        
        # 3. 区域分析
        regions = self._get_region_analysis(start_date, end_date)
        
        return {
            "start_date": start_date,
            "end_date": end_date,
            "trends": trends,
            "channels": channels,
            "regions": regions,
        }
    
    def _get_sales_trends(self, start_date: str, end_date: str) -> List[SalesTrend]:
        """获取销售趋势"""
        # 模拟数据
        return [
            SalesTrend("2026-07-01", 1000000, 1000, 1000, 0.05, 0.03),
            SalesTrend("2026-07-02", 1100000, 1100, 1000, 0.10, 0.10),
            SalesTrend("2026-07-03", 1050000, 1050, 1000, -0.05, -0.05),
            SalesTrend("2026-07-04", 1200000, 1200, 1000, 0.14, 0.14),
            SalesTrend("2026-07-05", 1150000, 1150, 1000, -0.04, -0.04),
        ]
    
    def _get_channel_analysis(self, start_date: str, end_date: str) -> List[ChannelAnalysis]:
        """获取渠道分析"""
        # 模拟数据
        return [
            ChannelAnalysis("线上", 5000000, 5000, 50.0, 0.10),
            ChannelAnalysis("线下", 3000000, 3000, 30.0, 0.05),
            ChannelAnalysis("APP", 2000000, 2000, 20.0, 0.15),
        ]
    
    def _get_region_analysis(self, start_date: str, end_date: str) -> List[RegionAnalysis]:
        """获取区域分析"""
        # 模拟数据
        return [
            RegionAnalysis("华东", 4000000, 4000, 40.0, 0.12),
            RegionAnalysis("华南", 2500000, 2500, 25.0, 0.08),
            RegionAnalysis("华北", 2000000, 2000, 20.0, 0.06),
            RegionAnalysis("西南", 1000000, 1000, 10.0, 0.15),
            RegionAnalysis("华中", 500000, 500, 5.0, 0.03),
        ]
    
    def render_markdown(self, data: Dict[str, Any]) -> str:
        """渲染Markdown报告"""
        trends = data["trends"]
        channels = data["channels"]
        regions = data["regions"]
        
        report = f"""# 📈 销售分析报告 - {data['start_date']} 至 {data['end_date']}

## 【销售趋势】

| 日期 | GMV | 订单量 | 客单价 | GMV增长 | 订单增长 |
|------|-----|--------|--------|---------|----------|
"""
        
        for trend in trends:
            report += f"| {trend.period} | ¥{trend.gmv:,.2f} | {trend.order_count:,} | ¥{trend.aov:,.2f} | {'+' if trend.gmv_growth >= 0 else ''}{trend.gmv_growth*100:.1f}% | {'+' if trend.order_growth >= 0 else ''}{trend.order_growth*100:.1f}% |\n"
        
        report += "\n## 【渠道分析】\n\n| 渠道 | GMV | 订单量 | 占比 | 增长 |\n|------|-----|--------|------|------|\n"
        
        for ch in channels:
            report += f"| {ch.channel} | ¥{ch.gmv:,.2f} | {ch.order_count:,} | {ch.percentage:.1f}% | {'+' if ch.growth >= 0 else ''}{ch.growth*100:.1f}% |\n"
        
        report += "\n## 【区域分析】\n\n| 区域 | GMV | 订单量 | 占比 | 增长 |\n|------|-----|--------|------|------|\n"
        
        for region in regions:
            report += f"| {region.region} | ¥{region.gmv:,.2f} | {region.order_count:,} | {region.percentage:.1f}% | {'+' if region.growth >= 0 else ''}{region.growth*100:.1f}% |\n"
        
        return report


# ── 快捷函数 ──

def generate_sales_report(start_date: str, end_date: str, db_executor=None) -> str:
    """生成销售分析报告"""
    template = SalesAnalysisTemplate(db_executor)
    data = template.generate(start_date, end_date)
    return template.render_markdown(data)


if __name__ == "__main__":
    # 测试
    report = generate_sales_report("2026-07-01", "2026-07-05")
    print(report)
