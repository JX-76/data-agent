# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from human_gate import HumanGatePolicy
from risk_policy import RiskPolicy
from dag_routing import route_and_plan


def test_read_only_diagnostic_analysis_does_not_require_human_review():
    plan = route_and_plan("昨天GMV下降原因是什么，给出结论", use_llm=False)
    assessment = RiskPolicy().assess("昨天GMV下降原因是什么，给出结论", plan)
    assert assessment.level in ("low", "medium")
    assert assessment.requires_human_review is False

    gate = HumanGatePolicy().evaluate("昨天GMV下降原因是什么，给出结论", plan)
    assert gate.status == "approved"
    assert gate.requires_human_review is False


def test_actual_write_or_intervention_request_is_blocked_by_router_governance():
    plan = route_and_plan("把低价商品全部改成9.9并发短信", use_llm=False)
    assert plan["status"] == "blocked"
    assert plan["blocked_reason"]


def test_sensitive_export_still_requires_human_review_or_blocking():
    plan = route_and_plan("导出所有客户手机号", use_llm=False)
    assessment = RiskPolicy().assess("导出所有客户手机号", plan)
    assert assessment.level == "critical"
    assert assessment.requires_human_review is True
