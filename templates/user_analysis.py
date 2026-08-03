"""用户分析模板 - 用户留存、LTV、RFM分群。

Features:
- 用户留存分析（Cohort）
- 用户LTV计算
- RFM分群
- 用户画像
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

import structlog

logger = structlog.get_logger("user_analysis")


@dataclass
class CohortRetention:
    """Cohort留存"""
    cohort_date: str
    new_users: int = 0
    retention_1d: float = 0.0
    retention_7d: float = 0.0
    retention_30d: float = 0.0
    retention_90d: float = 0.0


@dataclass
class UserLTV:
    """用户LTV"""
    user_segment: str
    avg_ltv: float = 0.0
    avg_orders: float = 0.0
    avg_aov: float = 0.0
    user_count: int = 0


@dataclass
class RFMGroup:
    """RFM分群"""
    group_name: str
    user_count: int = 0
    percentage: float = 0.0
    avg_order_value: float = 0.0
    avg_order_count: float = 0.0


class UserAnalysisTemplate:
    """用户分析模板"""
    
    def __init__(self, db_executor=None):
        self.db = db_executor
        self.logger = structlog.get_logger("user_analysis")
    
    def generate(self, date: Optional[str] = None) -> Dict[str, Any]:
        """生成用户分析报告"""
        if date is None:
            date = datetime.datetime.now().strftime("%Y-%m-%d")
        
        self.logger.info("generating_user_report", date=date)
        
        # 1. Cohort留存
        cohorts = self._get_cohort_retention(date)
        
        # 2. 用户LTV
        ltv = self._get_user_ltv(date)
        
        # 3. RFM分群
        rfm = self._get_rfm_groups(date)
        
        return {
            "date": date,
            "cohorts": cohorts,
            "ltv": ltv,
            "rfm": rfm,
        }
    
    def _get_cohort_retention(self, date: str) -> List[CohortRetention]:
        """获取Cohort留存"""
        # 模拟数据
        return [
            CohortRetention("2026-06-01", 1000, 0.35, 0.25, 0.15, 0.10),
            CohortRetention("2026-06-08", 1200, 0.38, 0.28, 0.18, 0.12),
            CohortRetention("2026-06-15", 1100, 0.36, 0.26, 0.16, 0.11),
            CohortRetention("2026-06-22", 1300, 0.40, 0.30, 0.20, 0.14),
            CohortRetention("2026-06-29", 1400, 0.42, 0.32, 0.22, 0.15),
        ]
    
    def _get_user_ltv(self, date: str) -> List[UserLTV]:
        """获取用户LTV"""
        # 模拟数据
        return [
            UserLTV("高价值用户", 5000, 50, 100, 100),
            UserLTV("中价值用户", 2000, 20, 100, 500),
            UserLTV("低价值用户", 500, 5, 100, 2000),
            UserLTV("新用户", 100, 1, 100, 5000),
        ]
    
    def _get_rfm_groups(self, date: str) -> List[RFMGroup]:
        """获取RFM分群"""
        # 模拟数据
        return [
            RFMGroup("重要价值客户", 500, 5.0, 500, 10),
            RFMGroup("重要发展客户", 800, 8.0, 300, 5),
            RFMGroup("重要保持客户", 600, 6.0, 400, 8),
            RFMGroup("重要挽留客户", 400, 4.0, 200, 3),
            RFMGroup("一般价值客户", 1000, 10.0, 150, 2),
        ]
    
    def render_markdown(self, data: Dict[str, Any]) -> str:
        """渲染Markdown报告"""
        cohorts = data["cohorts"]
        ltv = data["ltv"]
        rfm = data["rfm"]
        
        report = f"""# 👥 用户分析报告 - {data['date']}

## 【Cohort留存】

| Cohort | 新增用户 | 次日留存 | 7日留存 | 30日留存 | 90日留存 |
|--------|----------|----------|---------|----------|----------|
"""
        
        for cohort in cohorts:
            report += f"| {cohort.cohort_date} | {cohort.new_users:,} | {cohort.retention_1d*100:.1f}% | {cohort.retention_7d*100:.1f}% | {cohort.retention_30d*100:.1f}% | {cohort.retention_90d*100:.1f}% |\n"
        
        report += "\n## 【用户LTV】\n\n| 用户分层 | 平均LTV | 平均订单数 | 平均客单价 | 用户数 |\n|----------|---------|-----------|-----------|--------|\n"
        
        for user in ltv:
            report += f"| {user.user_segment} | ¥{user.avg_ltv:,.2f} | {user.avg_orders:.1f} | ¥{user.avg_aov:,.2f} | {user.user_count:,} |\n"
        
        report += "\n## 【RFM分群】\n\n| 分群 | 用户数 | 占比 | 平均订单价值 | 平均订单数 |\n|------|--------|------|-------------|-----------|\n"
        
        for group in rfm:
            report += f"| {group.group_name} | {group.user_count:,} | {group.percentage:.1f}% | ¥{group.avg_order_value:,.2f} | {group.avg_order_count:.1f} |\n"
        
        return report


# ── 快捷函数 ──

def generate_user_report(date: Optional[str] = None, db_executor=None) -> str:
    """生成用户分析报告"""
    template = UserAnalysisTemplate(db_executor)
    data = template.generate(date)
    return template.render_markdown(data)


if __name__ == "__main__":
    # 测试
    report = generate_user_report()
    print(report)
