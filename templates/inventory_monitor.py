"""库存监控模板 - 实时库存水位监控与预警。

Features:
- 库存水位监控
- 安全库存计算
- 补货点预警
- 滞销品识别
- 缺货预警
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

import structlog

logger = structlog.get_logger("inventory_monitor")


@dataclass
class InventoryMetrics:
    """库存核心指标"""
    sku_id: str
    sku_name: str
    current_stock: int = 0
    avg_daily_sales: float = 0.0
    safety_stock: int = 0
    reorder_point: int = 0
    days_of_supply: float = 0.0
    status: str = "normal"  # normal, low, out_of_stock, overstock


@dataclass
class ReorderAlert:
    """补货预警"""
    sku_id: str
    sku_name: str
    current_stock: int
    reorder_point: int
    suggested_quantity: int
    urgency: str  # low, medium, high


@dataclass
class OverstockAlert:
    """滞销预警"""
    sku_id: str
    sku_name: str
    current_stock: int
    days_of_supply: float
    last_sale_date: str
    suggested_action: str


class InventoryMonitorTemplate:
    """库存监控模板"""
    
    def __init__(self, db_executor=None):
        self.db = db_executor
        self.logger = structlog.get_logger("inventory_monitor")
    
    def generate(self, date: Optional[str] = None) -> Dict[str, Any]:
        """生成库存监控报告"""
        if date is None:
            date = datetime.datetime.now().strftime("%Y-%m-%d")
        
        self.logger.info("generating_inventory_report", date=date)
        
        # 1. 库存概况
        overview = self._get_inventory_overview(date)
        
        # 2. 补货预警
        reorder_alerts = self._get_reorder_alerts(date)
        
        # 3. 滞销预警
        overstock_alerts = self._get_overstock_alerts(date)
        
        # 4. 缺货预警
        stockout_alerts = self._get_stockout_alerts(date)
        
        return {
            "date": date,
            "overview": overview,
            "reorder_alerts": reorder_alerts,
            "overstock_alerts": overstock_alerts,
            "stockout_alerts": stockout_alerts,
        }
    
    def _get_inventory_overview(self, date: str) -> Dict[str, Any]:
        """获取库存概况"""
        # 模拟数据
        return {
            "total_skus": 1000,
            "total_stock_value": 5000000,
            "normal_skus": 800,
            "low_stock_skus": 50,
            "out_of_stock_skus": 10,
            "overstock_skus": 30,
        }
    
    def _get_reorder_alerts(self, date: str) -> List[ReorderAlert]:
        """获取补货预警"""
        # 模拟数据
        return [
            ReorderAlert("SKU001", "iPhone 15", 10, 20, 50, "high"),
            ReorderAlert("SKU002", "iPhone 15 Pro", 5, 15, 30, "high"),
            ReorderAlert("SKU003", "AirPods Pro", 20, 30, 40, "medium"),
        ]
    
    def _get_overstock_alerts(self, date: str) -> List[OverstockAlert]:
        """获取滞销预警"""
        # 模拟数据
        return [
            OverstockAlert("SKU100", "iPhone 14", 500, 90, "2026-04-01", "促销清仓"),
            OverstockAlert("SKU101", "iPhone 14 Pro", 300, 60, "2026-05-01", "促销清仓"),
        ]
    
    def _get_stockout_alerts(self, date: str) -> List[Dict[str, Any]]:
        """获取缺货预警"""
        # 模拟数据
        return [
            {"sku_id": "SKU200", "sku_name": "iPhone 15 Pro Max", "last_stock_date": "2026-06-20", "lost_sales": 100},
            {"sku_id": "SKU201", "sku_name": "AirPods Max", "last_stock_date": "2026-06-25", "lost_sales": 50},
        ]
    
    def render_markdown(self, data: Dict[str, Any]) -> str:
        """渲染Markdown报告"""
        overview = data["overview"]
        reorder_alerts = data["reorder_alerts"]
        overstock_alerts = data["overstock_alerts"]
        stockout_alerts = data["stockout_alerts"]
        
        report = f"""# 📦 库存监控报告 - {data['date']}

## 【库存概况】

| 指标 | 数值 |
|------|------|
| 总SKU数 | {overview['total_skus']:,} |
| 总库存价值 | ¥{overview['total_stock_value']:,.2f} |
| 库存正常 | {overview['normal_skus']:,} |
| 库存偏低 | {overview['low_stock_skus']:,} |
| 缺货 | {overview['out_of_stock_skus']:,} |
| 滞销 | {overview['overstock_skus']:,} |

## 【补货预警】

| SKU | 名称 | 当前库存 | 补货点 | 建议补货量 | 紧急程度 |
|-----|------|----------|--------|------------|----------|
"""
        
        for alert in reorder_alerts:
            urgency_icon = "🔴" if alert.urgency == "high" else "🟡" if alert.urgency == "medium" else "🟢"
            report += f"| {alert.sku_id} | {alert.sku_name} | {alert.current_stock} | {alert.reorder_point} | {alert.suggested_quantity} | {urgency_icon} {alert.urgency} |\n"
        
        report += "\n## 【滞销预警】\n\n| SKU | 名称 | 当前库存 | 可售天数 | 最后销售日期 | 建议操作 |\n|-----|------|----------|----------|-------------|----------|\n"
        
        for alert in overstock_alerts:
            report += f"| {alert.sku_id} | {alert.sku_name} | {alert.current_stock} | {alert.days_of_supply}天 | {alert.last_sale_date} | {alert.suggested_action} |\n"
        
        report += "\n## 【缺货预警】\n\n| SKU | 名称 | 最后库存日期 | 预估流失销售 |\n|-----|------|-------------|-------------|\n"
        
        for alert in stockout_alerts:
            report += f"| {alert['sku_id']} | {alert['sku_name']} | {alert['last_stock_date']} | {alert['lost_sales']}单 |\n"
        
        return report


# ── 快捷函数 ──

def generate_inventory_report(date: Optional[str] = None, db_executor=None) -> str:
    """生成库存监控报告"""
    template = InventoryMonitorTemplate(db_executor)
    data = template.generate(date)
    return template.render_markdown(data)


if __name__ == "__main__":
    # 测试
    report = generate_inventory_report()
    print(report)
