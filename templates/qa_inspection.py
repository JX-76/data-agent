"""客服质检模板 - 对话质量评分与热点问题识别。

Features:
- 对话质量评分
- 响应时长统计
- 问题解决率
- 情感分析
- 热点问题识别
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

import structlog

logger = structlog.get_logger("qa_inspection")


@dataclass
class QAStats:
    """质检统计"""
    total_chats: int = 0
    avg_response_time: float = 0.0
    avg_resolution_time: float = 0.0
    satisfaction_rate: float = 0.0
    resolution_rate: float = 0.0
    escalated_rate: float = 0.0


@dataclass
class AgentScore:
    """客服评分"""
    agent_id: str
    agent_name: str
    total_chats: int = 0
    avg_response_time: float = 0.0
    satisfaction_rate: float = 0.0
    resolution_rate: float = 0.0
    score: float = 0.0


@dataclass
class HotIssue:
    """热点问题"""
    issue_type: str
    count: int = 0
    percentage: float = 0.0
    avg_resolution_time: float = 0.0


class QAInspectionTemplate:
    """客服质检模板"""
    
    def __init__(self, db_executor=None):
        self.db = db_executor
        self.logger = structlog.get_logger("qa_inspection")
    
    def generate(self, date: Optional[str] = None) -> Dict[str, Any]:
        """生成质检报告"""
        if date is None:
            date = datetime.datetime.now().strftime("%Y-%m-%d")
        
        self.logger.info("generating_qa_report", date=date)
        
        # 1. 质检统计
        stats = self._get_qa_stats(date)
        
        # 2. 客服评分
        agent_scores = self._get_agent_scores(date)
        
        # 3. 热点问题
        hot_issues = self._get_hot_issues(date)
        
        return {
            "date": date,
            "stats": stats,
            "agent_scores": agent_scores,
            "hot_issues": hot_issues,
        }
    
    def _get_qa_stats(self, date: str) -> QAStats:
        """获取质检统计"""
        # 模拟数据
        return QAStats(
            total_chats=1000,
            avg_response_time=2.5,
            avg_resolution_time=15.3,
            satisfaction_rate=0.92,
            resolution_rate=0.88,
            escalated_rate=0.05,
        )
    
    def _get_agent_scores(self, date: str) -> List[AgentScore]:
        """获取客服评分"""
        # 模拟数据
        return [
            AgentScore("AG001", "张三", 150, 2.1, 0.95, 0.92, 95.0),
            AgentScore("AG002", "李四", 120, 2.8, 0.90, 0.85, 88.0),
            AgentScore("AG003", "王五", 180, 3.2, 0.88, 0.80, 84.0),
        ]
    
    def _get_hot_issues(self, date: str) -> List[HotIssue]:
        """获取热点问题"""
        # 模拟数据
        return [
            HotIssue("订单查询", 300, 30.0, 5.2),
            HotIssue("退换货", 200, 20.0, 12.5),
            HotIssue("物流查询", 150, 15.0, 8.3),
            HotIssue("产品咨询", 100, 10.0, 6.7),
            HotIssue("投诉建议", 50, 5.0, 18.9),
        ]
    
    def render_markdown(self, data: Dict[str, Any]) -> str:
        """渲染Markdown报告"""
        stats = data["stats"]
        agent_scores = data["agent_scores"]
        hot_issues = data["hot_issues"]
        
        report = f"""# 🎧 客服质检报告 - {data['date']}

## 【质检概览】

| 指标 | 数值 |
|------|------|
| 总对话数 | {stats.total_chats:,} |
| 平均响应时长 | {stats.avg_response_time:.1f}分钟 |
| 平均解决时长 | {stats.avg_resolution_time:.1f}分钟 |
| 满意度 | {stats.satisfaction_rate*100:.1f}% |
| 解决率 | {stats.resolution_rate*100:.1f}% |
| 升级率 | {stats.escalated_rate*100:.1f}% |

## 【客服评分TOP】

| 排名 | 客服 | 对话数 | 平均响应 | 满意度 | 解决率 | 综合评分 |
|------|------|--------|----------|--------|--------|----------|
"""
        
        for i, agent in enumerate(agent_scores, 1):
            report += f"| {i} | {agent.agent_name} | {agent.total_chats} | {agent.avg_response_time:.1f}分钟 | {agent.satisfaction_rate*100:.1f}% | {agent.resolution_rate*100:.1f}% | {agent.score:.1f} |\n"
        
        report += "\n## 【热点问题】\n\n| 问题类型 | 数量 | 占比 | 平均解决时长 |\n|----------|------|------|-------------|\n"
        
        for issue in hot_issues:
            report += f"| {issue.issue_type} | {issue.count} | {issue.percentage:.1f}% | {issue.avg_resolution_time:.1f}分钟 |\n"
        
        return report


# ── 快捷函数 ──

def generate_qa_report(date: Optional[str] = None, db_executor=None) -> str:
    """生成客服质检报告"""
    template = QAInspectionTemplate(db_executor)
    data = template.generate(date)
    return template.render_markdown(data)


if __name__ == "__main__":
    # 测试
    report = generate_qa_report()
    print(report)
