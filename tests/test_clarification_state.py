# -*- coding: utf-8 -*-
"""Regression tests for recoverable clarification and credibility contracts."""
from __future__ import unicode_literals

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, "src"))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _pending_plan():
    return {
        "query": u"看GMV",
        "status": "need_clarification",
        "intent": "clarification",
        "model": "order_detail",
        "metric": "gmv",
        "dimensions": [],
        "time_range": ["2026-07-01", "2026-07-07"],
        "task_id": "original-task",
        "clarification": {
            "question": u"请选择分析口径",
            "options": [
                {"id": "metric_query", "label": u"整体数值"},
                {"id": "breakdown", "label": u"按维度拆分"},
            ],
        },
    }


def test_state_machine_resolves_only_known_choice():
    from clarification_state import ClarificationStateMachine

    machine = ClarificationStateMachine()
    pending = machine.begin("s1", u"看GMV", _pending_plan(), task_id="original-task")
    assert pending["pending"] is True
    assert pending["contract"] == "clarification_state_v1"

    invalid = machine.resolve("s1", "unknown")
    assert invalid["status"] == "need_clarification"
    assert machine.has_pending("s1") is True

    resolved = machine.resolve("s1", "breakdown")
    assert resolved["status"] == "ok"
    assert resolved["plan"]["status"] == "ok"
    assert resolved["plan"]["clarification"] is None
    assert resolved["plan"]["intent"] == "breakdown"
    assert resolved["plan"]["dimensions"] == ["channel"]
    assert resolved["plan"]["resume_payload"]["original_task_id"] == "original-task"
    assert machine.has_pending("s1") is False


def test_credibility_is_factual_and_marks_pending():
    from credibility import build_credibility

    pending = build_credibility(_pending_plan(), {"status": "need_clarification", "diagnostics": {}})
    assert pending["contract"] == "credibility_v1"
    assert pending["requires_user_confirmation"] is True
    assert "clarification_pending" in pending["limitations"]

    final = build_credibility(
        {"status": "ok", "model": "order_detail", "metric": "gmv", "time_range": ["a", "b"]},
        {"status": "ok", "sql": "WITH d1 AS (SELECT 1) SELECT * FROM d1",
         "results": [], "execution": {"used_db": True},
         "diagnostics": {"sql_preflight": {"valid": True}}},
    )
    assert final["requires_user_confirmation"] is False
    assert "compiled_sql" in final["evidence"]
    assert "sql_preflight" in final["evidence"]
    assert "query_execution" in final["evidence"]


def test_facade_keeps_pending_clarification_in_session():
    from agent_facade import AgentFacade

    facade = AgentFacade(session_id="clarification-facade")
    facade._route = lambda query, use_llm=False: _pending_plan()
    result = facade.ask(u"看GMV")

    assert result["status"] == "need_clarification"
    pending = facade.get_pending_clarification()
    assert pending["pending"] is True
    assert pending["task_id"] == result["task_id"]
    assert result["credibility"]["requires_user_confirmation"] is True

    invalid = facade.resume_clarification("not-an-option")
    assert invalid["status"] == "need_clarification"
    assert facade.get_pending_clarification()["pending"] is True


if __name__ == "__main__":
    test_state_machine_resolves_only_known_choice()
    test_credibility_is_factual_and_marks_pending()
    test_facade_keeps_pending_clarification_in_session()
    print("All clarification state tests passed!")
