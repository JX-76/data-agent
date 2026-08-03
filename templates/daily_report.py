"""运营日报模板 - 每天早上8点自动生成运营数据报告。

Features:
- 昨日核心指标（GMV、订单量、客单价）
- 环比/同比分析
- 渠道分布
- 区域TOP5
- 异常预警
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

import structlog

logger = structlog.get_logger("daily_report")


@dataclass
class DailyMetrics:
    """日报核心指标"""
    date: str
    gmv: float = 0.0
    order_count: int = 0
    aov: float = 0.0
    
    # 环比（与昨天比）
    gmv_wow: float = 0.0  # week over week
    order_count_wow: float = 0.0
    aov_wow: float = 0.0
    
    # 同比（与上周同日比）
    gmv_yoy: float = 0.0  # year over year
    order_count_yoy: float = 0.0
    aov_yoy: float = 0.0


@dataclass
class ChannelMetrics:
    """渠道指标"""
    channel: str
    gmv: float = 0.0
    order_count: int = 0
    percentage: float = 0.0


@dataclass
class RegionMetrics:
    """区域指标"""
    region: str
    gmv: float = 0.0
    order_count: int = 0


@dataclass
class Alert:
    """异常预警"""
    level: str  # warning, critical, info
    metric: str
    message: str
    value: float
    threshold: float


class DailyReportTemplate:
    """运营日报模板"""
    
    def __init__(self, db_executor=None):
        self.db = db_executor
        self.logger = structlog.get_logger("daily_report")
    
    def generate(self, date: Optional[str] = None) -> Dict[str, Any]:
        """生成日报数据"""
        if date is None:
            date = (datetime.datetime.now() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        
        self.logger.info("generating_daily_report", date=date)
        
        # 1. 核心指标
        metrics = self._get_core_metrics(date)
        
        # 2. 渠道分布
        channels = self._get_channel_metrics(date)
        
        # 3. 区域TOP5
        regions = self._get_region_metrics(date)
        
        # 4. 异常检测
        alerts = self._detect_alerts(metrics)
        
        return {
            "date": date,
            "metrics": metrics,
            "channels": channels,
            "regions": regions,
            "alerts": alerts,
        }
    
    def _get_core_metrics(self, date: str) -> DailyMetrics:
        """获取核心指标"""
        # 昨日数据
        yesterday_sql = f"""
            SELECT 
                SUM(sell_through) as gmv,
                COUNT(DISTINCT order_id) as order_count
            FROM fct_orders
            WHERE DATE(paid_at) = '{date}'
            AND order_status IN ('paid', 'completed')
        """
        
        # 前天数据（环比）
        day_before = (datetime.datetime.strptime(date, "%Y-%m-%d") - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        day_before_sql = f"""
            SELECT 
                SUM(sell_through) as gmv,
                COUNT(DISTINCT order_id) as order_count
            FROM fct_orders
            WHERE DATE(paid_at) = '{day_before}'
            AND order_status IN ('paid', 'completed')
        """
        
        # 上周同日数据（同比）
        last_week = (datetime.datetime.strptime(date, "%Y-%m-%d") - datetime.timedelta(days=7)).strftime("%Y-%m-%d")
        last_week_sql = f"""
            SELECT 
                SUM(sell_through) as gmv,
                COUNT(DISTINCT order_id) as order_count
            FROM fct_orders
            WHERE DATE(paid_at) = '{last_week}'
            AND order_status IN ('paid', 'completed')
        """
        
        # 执行查询（模拟）
        # 实际使用时需要调用db_executor
        metrics = DailyMetrics(date=date)
        
        # 模拟数据
        metrics.gmv = 1234567.89
        metrics.order_count = 1234
        metrics.aov = metrics.gmv / metrics.order_count if metrics.order_count > 0 else 0
        
        # 环比
        metrics.gmv_wow = 0.053  # +5.3%
        metrics.order_count_wow = 0.032  # +3.2%
        metrics.aov_wow = 0.021  # +2.1%
        
        # 同比
        metrics.gmv_yoy = 0.121  # +12.1%
        metrics.order_count_yoy = 0.089  # +8.9%
        metrics.aov_yoy = 0.034  # +3.4%
        
        return metrics
    
    def _get_channel_metrics(self, date: str) -> List[ChannelMetrics]:
        """获取渠道分布"""
        sql = f"""
            SELECT 
                channel,
                SUM(sell_through) as gmv,
                COUNT(DISTINCT order_id) as order_count
            FROM fct_orders
            WHERE DATE(paid_at) = '{date}'
            AND order_status IN ('paid', 'completed')
            GROUP BY channel
            ORDER BY gmv DESC
        """
        
        # 模拟数据
        total_gmv = 1234567.89
        channels = [
            ChannelMetrics("线上", 800000, 800, 64.8),
            ChannelMetrics("线下", 300000, 300, 24.3),
            ChannelMetrics("APP", 134567.89, 134, 10.9),
        ]
        return channels
    
    def _get_region_metrics(self, date: str) -> List[RegionMetrics]:
        """获取区域TOP5"""
        sql = f"""
            SELECT 
                dim_store.region,
                SUM(fct_orders.sell_through) as gmv,
                COUNT(DISTINCT fct_orders.order_id) as order_count
            FROM fct_orders
            JOIN dim_store ON fct_orders.store_id = dim_store.store_id
            WHERE DATE(fct_orders.paid_at) = '{date}'
            AND fct_orders.order_status IN ('paid', 'completed')
            GROUP BY dim_store.region
            ORDER BY gmv DESC
            LIMIT 5
        """
        
        # 模拟数据
        regions = [
            RegionMetrics("华东", 500000, 500),
            RegionMetrics("华南", 300000, 300),
            RegionMetrics("华北", 200000, 200),
            RegionMetrics("西南", 150000, 150),
            RegionMetrics("华中", 84567.89, 84),
        ]
        return regions
    
    def _detect_alerts(self, metrics: DailyMetrics) -> List[Alert]:
        """异常检测"""
        alerts = []
        
        # GMV环比下降超过20%
        if metrics.gmv_wow < -0.2:
            alerts.append(Alert(
                level="critical",
                metric="GMV",
                message=f"GMV环比下降{abs(metrics.gmv_wow)*100:.1f}%，超过20%阈值",
                value=metrics.gmv_wow,
                threshold=-0.2,
            ))
        
        # 订单量环比下降超过30%
        if metrics.order_count_wow < -0.3:
            alerts.append(Alert(
                level="critical",
                metric="订单量",
                message=f"订单量环比下降{abs(metrics.order_count_wow)*100:.1f}%，超过30%阈值",
                value=metrics.order_count_wow,
                threshold=-0.3,
            ))
        
        # 客单价环比下降超过15%
        if metrics.aov_wow < -0.15:
            alerts.append(Alert(
                level="warning",
                metric="客单价",
                message=f"客单价环比下降{abs(metrics.aov_wow)*100:.1f}%，超过15%阈值",
                value=metrics.aov_wow,
                threshold=-0.15,
            ))
        
        if not alerts:
            alerts.append(Alert(
                level="info",
                metric="全部指标",
                message="所有指标正常",
                value=0.0,
                threshold=0.0,
            ))
        
        return alerts
    
    def render_markdown(self, data: Dict[str, Any]) -> str:
        """渲染Markdown报告"""
        metrics = data["metrics"]
        channels = data["channels"]
        regions = data["regions"]
        alerts = data["alerts"]
        
        report = f"""# 📊 运营日报 - {data['date']}

## 【昨日概览】

| 指标 | 数值 | 环比 | 同比 |
|------|------|------|------|
| GMV | ¥{metrics.gmv:,.2f} | {'+' if metrics.gmv_wow >= 0 else ''}{metrics.gmv_wow*100:.1f}% | {'+' if metrics.gmv_yoy >= 0 else ''}{metrics.gmv_yoy*100:.1f}% |
| 订单量 | {metrics.order_count:,}单 | {'+' if metrics.order_count_wow >= 0 else ''}{metrics.order_count_wow*100:.1f}% | {'+' if metrics.order_count_yoy >= 0 else ''}{metrics.order_count_yoy*100:.1f}% |
| 客单价 | ¥{metrics.aov:,.2f} | {'+' if metrics.aov_wow >= 0 else ''}{metrics.aov_wow*100:.1f}% | {'+' if metrics.aov_yoy >= 0 else ''}{metrics.aov_yoy*100:.1f}% |

## 【渠道分布】

| 渠道 | GMV | 占比 | 订单量 |
|------|-----|------|--------|
"""
        
        for ch in channels:
            report += f"| {ch.channel} | ¥{ch.gmv:,.2f} | {ch.percentage:.1f}% | {ch.order_count:,}单 |\n"
        
        report += "\n## 【区域TOP5】\n\n| 排名 | 区域 | GMV | 订单量 |\n|------|------|-----|--------|\n"
        
        for i, region in enumerate(regions, 1):
            report += f"| {i} | {region.region} | ¥{region.gmv:,.2f} | {region.order_count:,}单 |\n"
        
        report += "\n## 【异常预警】\n\n"
        
        for alert in alerts:
            icon = "⚠️" if alert.level == "critical" else "🔶" if alert.level == "warning" else "✅"
            report += f"{icon} {alert.message}\n"
        
        return report


# ── 快捷函数 ──

def generate_daily_report(date: Optional[str] = None, db_executor=None) -> str:
    """生成运营日报"""
    template = DailyReportTemplate(db_executor)
    data = template.generate(date)
    return template.render_markdown(data)


if __name__ == "__main__":
    # 测试
    report = generate_daily_report()
    print(report)
