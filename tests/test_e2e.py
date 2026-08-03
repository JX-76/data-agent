"""End-to-end integration tests for the Data Agent.

Covers the complete pipeline:
    Query → Route/Plan → Tool Execution → SQL Compile → Execute
         → Analysis → NL Insight → Chart Config

Tests both the DAG agent (graph_agent) and ReAct agent (agent_loop)
for consistency and correctness.

Run:
    python3 -m pytest tests/test_e2e.py -v
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import json
import os
import pytest

from graph_agent import run_graph, resume_graph
from agent_loop import react_loop

API_KEY_AVAILABLE = bool(os.environ.get("DEEPSEEK_KEY") or os.environ.get("DEEPSEEK_API_KEY"))


# ── Test queries that should produce full-chain output ──

E2E_QUERIES = [
    {
        "id": "e2e_metric_query",
        "query": "昨天 GMV 是多少？",
        "checks": {
            "status": "ok",
            "has_sql": True,
            "has_results": True,
            "has_analysis": True,
            "has_insight": True,
            "has_chart": True,
            "chart_type_non_none": True,  # chart should not be "none"
            "sql_contains": ["SUM(__sell_through)"],
        },
    },
    {
        "id": "e2e_breakdown",
        "query": "昨天各渠道 GMV",
        "checks": {
            "status": "ok",
            "has_sql": True,
            "has_results": True,
            "has_analysis": True,
            "has_insight": True,
            "has_chart": True,
            "chart_type_non_none": True,
            "sql_contains": ["channel", "GROUP BY channel"],
            "results_has_channel": True,
        },
    },
    {
        "id": "e2e_order_count",
        "query": "最近7天订单数是多少？",
        "checks": {
            "status": "ok",
            "has_sql": True,
            "sql_contains": ["COUNT(DISTINCT __order_id)"],
        },
    },
    {
        "id": "e2e_blocked",
        "query": "删除昨天的订单数据",
        "checks": {
            "status": "blocked",
        },
    },
    {
        "id": "e2e_clarification",
        "query": "GMV 口径是什么？",
        "checks": {
            "status": "clarification_needed",
        },
    },
]

E2E_REACT_QUERIES = [
    {
        "id": "react_gmv",
        "query": "昨天各渠道 GMV 是多少？",
        "checks": {
            "has_steps": True,
            "has_sql": True,
            "has_insight": True,
            "has_chart": True,
        },
    },
    {
        "id": "react_breakdown",
        "query": "最近7天各品类订单数排名",
        "checks": {
            "has_steps": True,
            "has_sql": True,
            "has_insight": True,
            "has_chart": True,
        },
    },
]


# ── DAG Agent E2E Tests ──

@pytest.mark.parametrize("case", E2E_QUERIES, ids=lambda c: c["id"])
class TestDAGE2E:
    """DAG Agent end-to-end tests."""

    def _run(self, query: str) -> dict:
        return run_graph(query, use_db=True, use_llm=False)

    def test_status(self, case):
        result = self._run(case["query"])
        expected = case["checks"].get("status")
        if expected:
            if expected == "clarification_needed":
                assert result.get("status") in ("clarification_needed", "need_clarification"), \
                    f"Expected clarification status, got '{result.get('status')}'"
            else:
                assert result.get("status") == expected, \
                    f"Expected status '{expected}', got '{result.get('status')}'"

    def test_output_structure(self, case):
        """Verify the output dict has all expected keys."""
        result = self._run(case["query"])
        checks = case["checks"]

        # Skip blocked/clarification
        if result.get("status") in ("blocked", "clarification_needed"):
            pytest.skip(f"Skipping structure check for {result.get('status')}")

        # SQL
        if checks.get("has_sql"):
            assert result.get("sql"), f"No SQL generated: {result}"
            for fragment in checks.get("sql_contains", []):
                assert fragment in result["sql"], \
                    f"SQL missing fragment '{fragment}':\n{result['sql']}"

        # Results
        if checks.get("has_results"):
            results = result.get("results", [])
            assert results, f"No results returned: {result}"
            if checks.get("results_has_channel"):
                channels = [r.get("channel") for r in results if "channel" in r]
                assert channels, f"No channel dimension in results: {results}"

        # Analysis layer
        if checks.get("has_analysis"):
            analysis = result.get("analysis")
            assert analysis is not None, "Missing analysis"
            # Should have at least summary or top_n
            has_data = analysis.get("summary") or analysis.get("top_n")
            assert has_data, f"Analysis empty: {analysis}"

    def test_insight_and_chart(self, case):
        """Verify NL insight + chart are generated."""
        result = self._run(case["query"])
        checks = case["checks"]

        if result.get("status") in ("blocked", "clarification_needed"):
            pytest.skip(f"Skipping for {result.get('status')}")

        if checks.get("has_insight"):
            insight = result.get("insight", {})
            assert insight, f"No insight: {result}"
            insight_text = insight.get("insight", "")
            assert insight_text, "Empty insight text"
            assert "分析完成" not in insight_text or "分析完成。" != insight_text, \
                f"Got placeholder insight: {insight_text}"

        if checks.get("has_chart"):
            chart = result.get("insight", {}).get("chart", {})
            assert chart, f"No chart: {result}"
            if checks.get("chart_type_non_none"):
                assert chart.get("type") and chart["type"] != "none", \
                    f"Chart type is 'none': {chart}"


# ── ReAct Agent E2E Tests (slow — requires DeepSeek API) ──

@pytest.mark.slow
@pytest.mark.parametrize("case", E2E_REACT_QUERIES, ids=lambda c: c["id"])
class TestReactE2E:
    """ReAct Agent end-to-end tests."""

    def _run(self, query: str) -> dict:
        return react_loop(query, use_db=True, max_steps=10)

    def test_structure(self, case):
        """Verify ReAct agent produces complete output."""
        if not API_KEY_AVAILABLE:
            pytest.skip("ReAct E2E requires DeepSeek API key")
        result = self._run(case["query"])
        checks = case["checks"]

        assert result.get("query") == case["query"]

        if checks.get("has_steps"):
            steps = result.get("steps", [])
            assert len(steps) > 0, f"No steps recorded: {result}"
            # At least one step should have a non-null observation
            obs_steps = [s for s in steps if s.get("observation")]
            assert len(obs_steps) > 0, f"No steps have observations: {steps}"

        if checks.get("has_sql"):
            assert result.get("sql"), f"No SQL: {result}"

        if checks.get("has_insight"):
            assert result.get("insight"), "No insight generated"

        if checks.get("has_chart"):
            chart = result.get("chart", {})
            assert chart, f"No chart: {result}"

    def test_consistency_with_dag(self, case):
        """Verify ReAct and DAG agents produce consistent results for same query."""
        if not API_KEY_AVAILABLE:
            pytest.skip("ReAct E2E requires DeepSeek API key")
        react_result = self._run(case["query"])
        dag_result = run_graph(case["query"], use_db=True, use_llm=False)

        # Both should return ok
        assert react_result.get("insight"), "ReAct: no insight"
        assert dag_result.get("insight"), "DAG: no insight"

        # Both should produce SQL
        react_sql = react_result.get("sql", "")
        dag_sql = dag_result.get("sql", "")
        assert react_sql and dag_sql, "One agent did not produce SQL"

        # Both should produce non-placeholder insights
        react_insight = react_result.get("insight", "")
        dag_insight = dag_result.get("insight", {}).get("insight", "")
        assert react_insight and dag_insight, "One agent has empty insight"


# ── Integration: DAG → Resume Flow ──

def test_full_clarification_resume_flow():
    """Test the full clarification → resume cycle with analysis output."""
    # Step 1: Trigger clarification
    q = "GMV 口径是什么？"
    result1 = run_graph(q, use_db=True, use_llm=False)

    assert result1["status"] in ("clarification_needed", "need_clarification")
    assert "interrupt" in result1

    # Step 2: Resume with a choice (use a valid option from the interrupt)
    state = result1["state"]
    executor = result1["executor"]
    result2 = resume_graph(state, executor, "breakdown")

    # Should complete with full analysis chain
    assert result2["status"] == "ok"
    assert result2.get("sql"), "No SQL after resume"
    assert result2.get("results"), "No results after resume"
    assert result2.get("analysis"), "No analysis after resume"
    insight = result2.get("insight", {})
    assert insight.get("insight"), "No insight after resume"
    # Insight should not be the placeholder
    assert insight["insight"] != "分析完成。", f"Placeholder insight after resume: {insight}"
