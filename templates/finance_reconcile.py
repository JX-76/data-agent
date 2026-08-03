"""财务对账模板 - 三方数据比对与差异分析。

Features:
- 平台账单 vs 银行流水 vs 订单数据
- 差异自动标记
- 差异原因分析
- 调整建议
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

import structlog

logger = structlog.get_logger("finance_reconcile")


@dataclass
class ReconcileSummary:
    """对账汇总"""
    total_orders: int = 0
    total_amount: float = 0.0
    matched_orders: int = 0
    matched_amount: float = 0.0
    unmatched_orders: int = 0
    unmatched_amount: float = 0.0
    discrepancy_rate: float = 0.0


@dataclass
class Discrepancy:
    """差异明细"""
    order_id: str
    platform_amount: float = 0.0
    bank_amount: float = 0.0
    order_amount: float = 0.0
    difference: float = 0.0
    reason: str = ""
    suggested_action: str = ""


class FinanceReconcileTemplate:
    """财务对账模板"""
    
    def __init__(self, db_executor=None):
        self.db = db_executor
        self.logger = structlog.get_logger("finance_reconcile")
    
    def generate(self, date: Optional[str] = None) -> Dict[str, Any]:
        """生成对账报告"""
        if date is None:
            date = datetime.datetime.now().strftime("%Y-%m-%d")
        
        self.logger.info("generating_reconcile_report", date=date)
        
        # 1. 对账汇总
        summary = self._get_reconcile_summary(date)
        
        # 2. 差异明细
        discrepancies = self._get_discrepancies(date)
        
        return {
            "date": date,
            "summary": summary,
            "discrepancies": discrepancies,
        }
    
    def _get_reconcile_summary(self, date: str) -> ReconcileSummary:
        """获取对账汇总"""
        # 模拟数据
        return ReconcileSummary(
            total_orders=1000,
            total_amount=1000000,
            matched_orders=950,
            matched_amount=950000,
            unmatched_orders=50,
            unmatched_amount=50000,
            discrepancy_rate=0.05,
        )
    
    def _get_discrepancies(self, date: str) -> List[Discrepancy]:
        """获取差异明细"""
        # 模拟数据
        return [
            Discrepancy("ORD001", 1000, 980, 1000, -20, "银行手续费", "确认手续费"),
            Discrepancy("ORD002", 500, 500, 480, 20, "平台优惠", "确认优惠金额"),
            Discrepancy("ORD003", 2000, 2000, 2000, 0, "数据延迟", "等待银行确认"),
        ]
    
    def render_markdown(self, data: Dict[str, Any]) -> str:
        """渲染Markdown报告"""
        summary = data["summary"]
        discrepancies = data["discrepancies"]
        
        report = f"""# 💰 财务对账报告 - {data['date']}

## 【对账汇总】

| 指标 | 数值 |
|------|------|
| 总订单数 | {summary.total_orders:,} |
| 总金额 | ¥{summary.total_amount:,.2f} |
| 匹配订单数 | {summary.matched_orders:,} |
| 匹配金额 | ¥{summary.matched_amount:,.2f} |
| 未匹配订单数 | {summary.unmatched_orders:,} |
| 未匹配金额 | ¥{summary.unmatched_amount:,.2f} |
| 差异率 | {summary.discrepancy_rate*100:.2f}% |

## 【差异明细】

| 订单号 | 平台金额 | 银行金额 | 订单金额 | 差异 | 原因 | 建议操作 |
|--------|----------|----------|----------|------|------|----------|
"""
        
        for disc in discrepancies:
            report += f"| {disc.order_id} | ¥{disc.platform_amount:,.2f} | ¥{disc.bank_amount:,.2f} | ¥{disc.order_amount:,.2f} | ¥{disc.difference:,.2f} | {disc.reason} | {disc.suggested_action} |\n"
        
        return report


# ── 快捷函数 ──

def generate_reconcile_report(date: Optional[str] = None, db_executor=None) -> str:
    """生成财务对账报告"""
    template = FinanceReconcileTemplate(db_executor)
    data = template.generate(date)
    return template.render_markdown(data)


if __name__ == "__main__":
    # 测试
    report = generate_reconcile_report()
    print(report)
