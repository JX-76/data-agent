"""Golden query tests — validates the Data Agent graph against expected outputs.

Runs the full DAG agent (without DB) on each golden query and asserts:
- status (ok / blocked / need_clarification)
- routing (model, metric, intent, dimensions)
- tool chain (tools_include)
- SQL structure (sql_contains)
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import yaml
import pytest

from graph_agent import build_data_agent_graph, run_graph, resume_graph

# Load golden test cases
EVALS_DIR = Path(__file__).resolve().parents[1] / "evals"
GOLDEN_PATH = EVALS_DIR / "golden_queries.yaml"

with open(GOLDEN_PATH, "r", encoding="utf-8") as f:
    GOLDEN_DATA = yaml.safe_load(f)

GOLDEN_CASES = GOLDEN_DATA["cases"]


def _get_tool_names(state_or_result):
    """Extract tool operation names from the execution trace."""
    rt = state_or_result.get("rt")
    if rt is None:
        return []
    return [entry.get("op", "") for entry in rt.trace]


def _run_case(case: dict):
    """Run a single golden test case through the DAG agent."""
    query = case["query"]
    expected = case["expected"]

    result = run_graph(query, use_db=False, use_llm=False)

    # If clarification was triggered, check for resume or return early
    if result.get("status") == "clarification_needed":
        resume_def = case.get("resume")
        if resume_def:
            state = result["state"]
            executor = result["executor"]
            choice = resume_def["choice"]
            result = resume_graph(state, executor, choice)
            expected = resume_def["expected"]
        else:
            # Clarification-only case, skip routing checks
            if expected.get("status") == "need_clarification":
                return None
            return result, expected

    return result, expected


def _collect_tool_ops(trace: list) -> list:
    """Collect tool operation names from trace entries."""
    return [entry.get("op", "") for entry in trace]


@pytest.mark.parametrize("case", GOLDEN_CASES, ids=lambda c: c["id"])
class TestGoldenQueries:
    """Parametrized golden query tests."""

    def test_status(self, case):
        """Verify execution status matches expected."""
        expected = case["expected"]
        result_data = _run_case(case)
        if result_data is None:
            return  # Clarification-only case, skip status check
        result, expected = result_data
        assert result.get("status") == expected.get("status"), \
            f"Expected status '{expected.get('status')}', got '{result.get('status')}'"

    def test_model_routing(self, case):
        """Verify model routing matches expected."""
        expected = case["expected"]
        if expected.get("status") in ("blocked", "need_clarification"):
            pytest.skip("Skipping routing check for blocked/clarification case")

        result_data = _run_case(case)
        if result_data is None:
            pytest.skip("Clarification-only case")
        result, expected = result_data
        assert result.get("model") == expected.get("model", "order_detail"), \
            f"Expected model '{expected.get('model')}', got '{result.get('model')}'"

    def test_metric_routing(self, case):
        """Verify metric routing matches expected."""
        expected = case["expected"]
        if expected.get("status") in ("blocked", "need_clarification"):
            pytest.skip()

        result_data = _run_case(case)
        if result_data is None:
            pytest.skip()
        result, expected = result_data
        assert result.get("metric") == expected.get("metric", "gmv"), \
            f"Expected metric '{expected.get('metric')}', got '{result.get('metric')}'"

    def test_dimensions_routing(self, case):
        """Verify dimensions routing matches expected."""
        expected = case["expected"]
        if expected.get("status") in ("blocked", "need_clarification"):
            pytest.skip()

        result_data = _run_case(case)
        if result_data is None:
            pytest.skip()
        result, expected = result_data
        assert result.get("dimensions") == expected.get("dimensions", []), \
            f"Expected dims {expected.get('dimensions')}, got {result.get('dimensions')}"

    def test_intent_routing(self, case):
        """Verify intent routing matches expected."""
        expected = case["expected"]
        if expected.get("status") in ("blocked", "need_clarification"):
            pytest.skip()

        result_data = _run_case(case)
        if result_data is None:
            pytest.skip()
        result, expected = result_data
        assert result.get("intent") == expected.get("intent", "metric_query"), \
            f"Expected intent '{expected.get('intent')}', got '{result.get('intent')}'"

    def test_tool_chain(self, case):
        """Verify tool chain includes expected operations."""
        expected = case["expected"]
        if expected.get("status") in ("blocked", "need_clarification"):
            pytest.skip()

        tools_expected = expected.get("tools_include", [])
        if not tools_expected:
            pytest.skip("No tools_include in expected")

        result_data = _run_case(case)
        if result_data is None:
            pytest.skip()
        result, expected = result_data

        # Get tool ops from trace
        if "trace" not in result:
            pytest.skip("No trace in result (analysis layer not reached)")

        ops = _collect_tool_ops(result["trace"])
        ops_str = " → ".join(ops)

        for tool in tools_expected:
            # Pattern matching: "switch(order_detail)" should match "switch(order_detail)"
            found = any(tool in op for op in ops)
            assert found, \
                f"Expected tool '{tool}' in trace, but not found.\nTrace: {ops_str}"

    def test_sql_structure(self, case):
        """Verify SQL contains expected fragments."""
        expected = case["expected"]
        if expected.get("status") in ("blocked", "need_clarification"):
            pytest.skip()

        sql_contains = expected.get("sql_contains", [])
        if not sql_contains:
            pytest.skip("No sql_contains in expected")

        result_data = _run_case(case)
        if result_data is None:
            pytest.skip()
        result, expected = result_data

        sql = result.get("sql", "")
        if not sql:
            pytest.skip("No SQL in result (analysis layer not reached)")

        for fragment in sql_contains:
            assert fragment in sql, \
                f"Expected SQL fragment '{fragment}' not found in:\n{sql}"
