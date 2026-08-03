"""供应链分析模板 - 履约、物流、退货分析。

Features:
- 履约率分析
- 物流时效分析
- 退货分析
- 供应商分析
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

import structlog

logger = structlog.get_logger("supply_chain")


@dataclass
class FulfillmentMetrics:
    """履约指标"""
    warehouse: str
    total_orders: int = 0
    fulfilled_orders: int = 0
    fulfillment_rate: float = 0.0
    avg_fulfillment_time: float = 0.0
    on_time_rate: float = 0.0


@dataclass
class LogisticsMetrics:
    """物流指标"""
    logistics_provider: str
    total_orders: int = 0
    avg_delivery_time: float = 0.0
    on_time_rate: float = 0.0
    damage_rate: float = 0.0
    cost_per_order: float = 0.0


@dataclass
class ReturnMetrics:
    """退货指标"""
    return_reason: str
    count: int = 0
    percentage: float = 0.0
    avg_return_time: float = 0.0
    refund_amount: float = 0.0


class SupplyChainTemplate:
    """供应链分析模板"""
    
    def __init__(self, db_executor=None):
        self.db = db_executor
        self.logger = structlog.get_logger("supply_chain")
    
    def generate(self, date: Optional[str] = None) -> Dict[str, Any]:
        """生成供应链分析报告"""
        if date is None:
            date = datetime.datetime.now().strftime("%Y-%m-%d")
        
        self.logger.info("generating_supply_chain_report", date=date)
        
        # 1. 履约分析
        fulfillment = self._get_fulfillment_metrics(date)
        
        # 2. 物流分析
        logistics = self._get_logistics_metrics(date)
        
        # 3. 退货分析
        returns = self._get_return_metrics(date)
        
        return {
            "date": date,
            "fulfillment": fulfillment,
            "logistics": logistics,
            "returns": returns,
        }
    
    def _get_fulfillment_metrics(self, date: str) -> List[FulfillmentMetrics]:
        """获取履约指标"""
        # 模拟数据
        return [
            FulfillmentMetrics("华东仓", 5000, 4900, 0.98, 2.5, 0.95),
            FulfillmentMetrics("华南仓", 3000, 2940, 0.98, 2.3, 0.96),
            FulfillmentMetrics("华北仓", 2000, 1960, 0.98, 2.8, 0.94),
        ]
    
    def _get_logistics_metrics(self, date: str) -> List[LogisticsMetrics]:
        """获取物流指标"""
        # 模拟数据
        return [
            LogisticsMetrics("顺丰", 5000, 1.5, 0.98, 0.001, 15.0),
            LogisticsMetrics("京东", 3000, 2.0, 0.95, 0.002, 12.0),
            LogisticsMetrics("中通", 2000, 2.5, 0.90, 0.003, 8.0),
        ]
    
    def _get_return_metrics(self, date: str) -> List[ReturnMetrics]:
        """获取退货指标"""
        # 模拟数据
        return [
            ReturnMetrics("质量问题", 100, 50.0, 3.5, 50000),
            ReturnMetrics("尺寸不符", 50, 25.0, 2.0, 25000),
            ReturnMetrics("不喜欢", 30, 15.0, 2.5, 15000),
            ReturnMetrics("物流损坏", 20, 10.0, 4.0, 10000),
        ]
    
    def render_markdown(self, data: Dict[str, Any]) -> str:
        """渲染Markdown报告"""
        fulfillment = data["fulfillment"]
        logistics = data["logistics"]
        returns = data["returns"]
        
        report = f"""# 🚚 供应链分析报告 - {data['date']}

## 【履约分析】

| 仓库 | 总订单 | 履约订单 | 履约率 | 平均履约时间 | 准时率 |
|------|--------|----------|--------|-------------|--------|
"""
        
        for f in fulfillment:
            report += f"| {f.warehouse} | {f.total_orders:,} | {f.fulfilled_orders:,} | {f.fulfillment_rate*100:.1f}% | {f.avg_fulfillment_time:.1f}天 | {f.on_time_rate*100:.1f}% |\n"
        
        report += "\n## 【物流分析】\n\n| 物流商 | 总订单 | 平均配送时间 | 准时率 | 破损率 | 单均成本 |\n|--------|--------|-------------|--------|--------|----------|\n"
        
        for log in logistics:
            report += f"| {log.logistics_provider} | {log.total_orders:,} | {log.avg_delivery_time:.1f}天 | {log.on_time_rate*100:.1f}% | {log.damage_rate*100:.2f}% | ¥{log.cost_per_order:.2f} |\n"
        
        report += "\n## 【退货分析】\n\n| 退货原因 | 数量 | 占比 | 平均退货时间 | 退款金额 |\n|----------|------|------|-------------|----------|\n"
        
        for ret in returns:
            report += f"| {ret.return_reason} | {ret.count:,} | {ret.percentage:.1f}% | {ret.avg_return_time:.1f}天 | ¥{ret.refund_amount:,.2f} |\n"
        
        return report


# ── 快捷函数 ──

def generate_supply_chain_report(date: Optional[str] = None, db_executor=None) -> str:
    """生成供应链分析报告"""
    template = SupplyChainTemplate(db_executor)
    data = template.generate(date)
    return template.render_markdown(data)


if __name__ == "__main__":
    # 测试
    report = generate_supply_chain_report()
    print(report)
