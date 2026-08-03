"""定时任务调度器 - 支持cron表达式配置。

Features:
- 支持cron表达式
- 支持多种任务类型（日报、周报、月报）
- 支持邮件/钉钉推送
- 支持任务日志
"""

from __future__ import annotations

import datetime
import json
import os
import subprocess
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Callable

import structlog

logger = structlog.get_logger("scheduler")


@dataclass
class TaskConfig:
    """任务配置"""
    name: str
    cron: str  # cron表达式
    task_type: str  # daily_report, weekly_report, monthly_report
    template: str  # 模板名称
    output_format: str = "markdown"  # markdown, html, pdf
    recipients: List[str] = field(default_factory=list)  # 推送邮箱/手机号
    enabled: bool = True
    description: str = ""


class TaskScheduler:
    """任务调度器"""
    
    def __init__(self, config_path: Optional[str] = None):
        self.tasks: Dict[str, TaskConfig] = {}
        self.logger = structlog.get_logger("scheduler")
        self.config_path = config_path or "config/scheduler.json"
        self._load_config()
    
    def _load_config(self) -> None:
        """加载配置"""
        if os.path.exists(self.config_path):
            with open(self.config_path, 'r') as f:
                config = json.load(f)
            for task_data in config.get("tasks", []):
                task = TaskConfig(**task_data)
                self.tasks[task.name] = task
            self.logger.info("loaded_tasks", count=len(self.tasks))
        else:
            self.logger.warning("config_not_found", path=self.config_path)
    
    def add_task(self, task: TaskConfig) -> None:
        """添加任务"""
        self.tasks[task.name] = task
        self._save_config()
        self.logger.info("task_added", name=task.name)
    
    def remove_task(self, name: str) -> None:
        """移除任务"""
        if name in self.tasks:
            del self.tasks[name]
            self._save_config()
            self.logger.info("task_removed", name=name)
    
    def _save_config(self) -> None:
        """保存配置"""
        config = {
            "tasks": [
                {
                    "name": task.name,
                    "cron": task.cron,
                    "task_type": task.task_type,
                    "template": task.template,
                    "output_format": task.output_format,
                    "recipients": task.recipients,
                    "enabled": task.enabled,
                    "description": task.description,
                }
                for task in self.tasks.values()
            ]
        }
        os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
        with open(self.config_path, 'w') as f:
            json.dump(config, f, indent=2)
    
    def run_task(self, name: str) -> Optional[str]:
        """执行单个任务"""
        if name not in self.tasks:
            self.logger.error("task_not_found", name=name)
            return None
        
        task = self.tasks[name]
        if not task.enabled:
            self.logger.info("task_disabled", name=name)
            return None
        
        self.logger.info("running_task", name=name, type=task.task_type)
        
        # 根据任务类型执行
        if task.task_type == "daily_report":
            return self._run_daily_report(task)
        elif task.task_type == "weekly_report":
            return self._run_weekly_report(task)
        elif task.task_type == "monthly_report":
            return self._run_monthly_report(task)
        else:
            self.logger.error("unknown_task_type", type=task.task_type)
            return None
    
    def _run_daily_report(self, task: TaskConfig) -> str:
        """执行日报任务"""
        from templates.daily_report import generate_daily_report
        
        report = generate_daily_report()
        
        # 保存报告
        output_path = f"reports/daily/{datetime.datetime.now().strftime('%Y-%m-%d')}.md"
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, 'w') as f:
            f.write(report)
        
        self.logger.info("daily_report_generated", path=output_path)
        
        # 推送报告
        if task.recipients:
            self._push_report(report, task.recipients)
        
        return report
    
    def _run_weekly_report(self, task: TaskConfig) -> str:
        """执行周报任务"""
        # TODO: 实现周报逻辑
        return "Weekly report not implemented yet"
    
    def _run_monthly_report(self, task: TaskConfig) -> str:
        """执行月报任务"""
        # TODO: 实现月报逻辑
        return "Monthly report not implemented yet"
    
    def _push_report(self, report: str, recipients: List[str]) -> None:
        """推送报告"""
        for recipient in recipients:
            if "@" in recipient:
                self._send_email(report, recipient)
            elif recipient.startswith("+"):
                self._send_sms(report, recipient)
            else:
                self._send_dingtalk(report, recipient)
    
    def _send_email(self, report: str, email: str) -> None:
        """发送邮件"""
        # TODO: 实现邮件发送
        self.logger.info("sending_email", email=email)
    
    def _send_sms(self, report: str, phone: str) -> None:
        """发送短信"""
        # TODO: 实现短信发送
        self.logger.info("sending_sms", phone=phone)
    
    def _send_dingtalk(self, report: str, webhook: str) -> None:
        """发送钉钉"""
        # TODO: 实现钉钉推送
        self.logger.info("sending_dingtalk", webhook=webhook)
    
    def list_tasks(self) -> List[Dict[str, Any]]:
        """列出所有任务"""
        return [
            {
                "name": task.name,
                "cron": task.cron,
                "task_type": task.task_type,
                "enabled": task.enabled,
                "description": task.description,
            }
            for task in self.tasks.values()
        ]
    
    def get_task(self, name: str) -> Optional[TaskConfig]:
        """获取任务配置"""
        return self.tasks.get(name)


# ── 快捷函数 ──

def create_daily_report_task(name: str, recipients: List[str]) -> TaskConfig:
    """创建日报任务"""
    return TaskConfig(
        name=name,
        cron="0 8 * * *",  # 每天早上8点
        task_type="daily_report",
        template="daily_report",
        output_format="markdown",
        recipients=recipients,
        description="每日运营日报",
    )


def create_weekly_report_task(name: str, recipients: List[str]) -> TaskConfig:
    """创建周报任务"""
    return TaskConfig(
        name=name,
        cron="0 9 * * 1",  # 每周一早上9点
        task_type="weekly_report",
        template="weekly_report",
        output_format="markdown",
        recipients=recipients,
        description="每周运营周报",
    )


def create_monthly_report_task(name: str, recipients: List[str]) -> TaskConfig:
    """创建月报任务"""
    return TaskConfig(
        name=name,
        cron="0 9 1 * *",  # 每月1号早上9点
        task_type="monthly_report",
        template="monthly_report",
        output_format="markdown",
        recipients=recipients,
        description="每月运营月报",
    )


if __name__ == "__main__":
    # 测试
    scheduler = TaskScheduler()
    
    # 添加日报任务
    task = create_daily_report_task("运营日报", ["admin@example.com"])
    scheduler.add_task(task)
    
    # 列出任务
    print("Tasks:")
    for t in scheduler.list_tasks():
        print(f"  - {t['name']}: {t['cron']} ({t['task_type']})")
