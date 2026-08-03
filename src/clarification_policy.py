# -*- coding: utf-8 -*-
"""Clarification policy for router and DAG planning.

Keeps fallback clarification text and options out of router_core so the
router only decides when clarification is needed.
"""


def build_clarification(metric=None, reason=None, expected_next_step=None):
    return {
        "metric": metric,
        "question": "请选择分析口径",
        "options": [
            {"id": "metric_query", "label": "整体数值", "description": "直接看汇总数据"},
            {"id": "breakdown", "label": "按维度拆分", "description": "按维度拆分查看"},
        ],
        "reason": reason or "信息不足，需要用户确认分析口径",
        "expected_next_step": expected_next_step or "根据用户选择继续执行分析",
    }


__all__ = ["build_clarification"]
