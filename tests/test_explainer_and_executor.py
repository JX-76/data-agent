# -*- coding: utf-8 -*-
"""Tests for the explainer and readonly executor integration."""

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, "src"))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def test_result_explainer_ok():
    from result_explainer import build_insight_bundle
    from schemas import AnalysisPlan, ExecutionResult

    plan = AnalysisPlan(query="最近7天GMV", metric="gmv", dimensions=["channel"])
    exec_result = ExecutionResult(query="最近7天GMV", status="ok", metric="gmv", dimensions=["channel"], results_summary={"row_count": 1})
    bundle = build_insight_bundle(plan, exec_result)
    data = bundle.to_dict()
    assert "分析已完成" in data["summary"]
    assert data["chart"]["type"] == "bar"
    assert len(data["next_steps"]) >= 1


def test_result_explainer_blocked():
    from result_explainer import build_insight_bundle
    from schemas import AnalysisPlan, ExecutionResult

    plan = AnalysisPlan(query="delete from orders", status="blocked", blocked_reason="blocked")
    exec_result = ExecutionResult(query="delete from orders", status="blocked")
    bundle = build_insight_bundle(plan, exec_result)
    data = bundle.to_dict()
    assert "拦截" in data["summary"]
    assert data["chart"]["type"] == "none"


def test_result_explainer_clarification():
    from result_explainer import build_insight_bundle
    from schemas import AnalysisPlan, ExecutionResult, ClarificationRequest

    clarification = ClarificationRequest(metric="gmv", question="请选择分析口径", reason="需要确认口径")
    plan = AnalysisPlan(query="GMV 口径是什么？", status="need_clarification", clarification=clarification)
    exec_result = ExecutionResult(query="GMV 口径是什么？", status="need_clarification")
    bundle = build_insight_bundle(plan, exec_result)
    data = bundle.to_dict()

    assert "澄清口径" in data["summary"]
    assert data["chart"]["type"] == "none"
    assert data["caveats"]


def test_readonly_mock_executor():
    from db_adapter import MockDBAdapter, ReadonlyQueryExecutor

    mock = MockDBAdapter(schema={"order_detail": ["gmv", "channel"]}, previews={"order_detail": [{"gmv": 1}]})
    executor = ReadonlyQueryExecutor(db=mock)
    result = executor.execute("SELECT 1")
    assert result["source"] == "mock"
    assert result["row_count"] == 0
    assert executor.describe_schema()["order_detail"] == ["gmv", "channel"]
    assert executor.fetch_preview("order_detail", limit=1) == [{"gmv": 1}]


if __name__ == "__main__":
    test_result_explainer_ok()
    test_result_explainer_blocked()
    test_result_explainer_clarification()
    test_readonly_mock_executor()
    print("All explainer/executor tests passed!")
