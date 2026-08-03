"""deepeval regression gate for Data Agent.

No LLM required for core checks — uses custom deterministic BaseMetric
subclasses. LLM-as-a-Judge metrics (faithfulness, relevancy) are available
when OPENAI_API_KEY is configured.

Usage:
    python3 -m pytest tests/test_eval_gate.py -v

    # Run only the deterministic gate:
    python3 -m pytest tests/test_eval_gate.py -v -k "deterministic"

    # In CI:
    DEEP_EVAL_GATE_MODE=ci python3 -m pytest tests/test_eval_gate.py
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

# deepeval is an optional evaluation dependency. Keep the deterministic gate
# source intact, but let the repository's normal test suite collect safely in
# lightweight developer/CI environments.
pytest.importorskip(
    "deepeval",
    reason="optional eval dependency missing; install with `py -3 -m pip install deepeval` to run tests/test_eval_gate.py",
)
from deepeval import assert_test
from deepeval.test_case import LLMTestCase
from deepeval.metrics import BaseMetric

from graph_agent import run_graph, resume_graph

# ── Custom Deterministic Metrics (No LLM Required) ──


class StatusMetric(BaseMetric):
    """Verify the execution status matches expectation."""

    def __init__(self, expected_status: str, threshold: float = 0.5):
        super().__init__()
        self.threshold = threshold
        self.expected_status = expected_status

    def measure(self, test_case: LLMTestCase):
        result = test_case.additional_metadata.get("result", {})
        status = result.get("status", "error")
        self.score = 1.0 if status == self.expected_status else 0.0
        self.success = self.score >= self.threshold
        # Store details for assertion message
        self.reason = f"Expected status '{self.expected_status}', got '{status}'"
        if not self.success:
            self.reason += f"\nFull result: {result}"
        return self.score

    async def a_measure(self, test_case):
        return self.measure(test_case)

    def is_successful(self):
        return self.success


class RoutingMetric(BaseMetric):
    """Verify model/metric/dimensions/intent routing correctness."""

    def __init__(self, field: str, expected_value, threshold: float = 0.5):
        super().__init__()
        self.threshold = threshold
        self.field = field
        self.expected_value = expected_value

    def measure(self, test_case: LLMTestCase):
        result = test_case.additional_metadata.get("result", {})
        actual = result.get(self.field)
        expected = self.expected_value

        if isinstance(expected, list):
            self.score = 1.0 if actual == expected else 0.0
        else:
            self.score = 1.0 if actual == expected else 0.0

        self.success = self.score >= self.threshold
        self.reason = f"Expected {self.field}='{expected}', got '{actual}'"
        return self.score

    async def a_measure(self, test_case):
        return self.measure(test_case)

    def is_successful(self):
        return self.success


class SQLStructureMetric(BaseMetric):
    """Verify generated SQL contains required fragments."""

    def __init__(self, required_fragments: list[str], threshold: float = 1.0):
        super().__init__()
        self.threshold = threshold
        self.required_fragments = required_fragments

    def measure(self, test_case: LLMTestCase):
        result = test_case.additional_metadata.get("result", {})
        sql = result.get("sql", "")
        if not sql:
            self.score = 0.0
            self.success = False
            self.reason = "No SQL generated"
            return 0.0

        hits = sum(1 for frag in self.required_fragments if frag in sql)
        self.score = hits / len(self.required_fragments) if self.required_fragments else 1.0
        self.success = self.score >= self.threshold
        if not self.success:
            missing = [f for f in self.required_fragments if f not in sql]
            self.reason = f"SQL missing: {missing}"
        return self.score

    async def a_measure(self, test_case):
        return self.measure(test_case)

    def is_successful(self):
        return self.success


class ToolChainMetric(BaseMetric):
    """Verify the tool chain includes expected operations."""
    
    def __init__(self, expected_tools: list[str], threshold: float = 1.0):
        super().__init__()
        self.threshold = threshold
        self.expected_tools = expected_tools

    def measure(self, test_case: LLMTestCase):
        result = test_case.additional_metadata.get("result", {})
        trace = result.get("trace", [])
        ops_str = " → ".join(t.get("op", "") for t in trace)

        hits = 0
        for tool in self.expected_tools:
            if any(tool in t.get("op", "") for t in trace):
                hits += 1

        self.score = hits / len(self.expected_tools) if self.expected_tools else 1.0
        self.success = self.score >= self.threshold
        if not self.success:
            missing = [t for t in self.expected_tools 
                       if not any(t in op.get("op", "") for op in trace)]
            self.reason = f"Tool chain missing: {missing}\nActual chain: {ops_str}"
        return self.score

    async def a_measure(self, test_case):
        return self.measure(test_case)

    def is_successful(self):
        return self.success


# ── Regression Test Cases ──

REGRESSION_CASES = [
    {
        "name": "metric_query_gmv",
        "query": "昨天 GMV 是多少？",
        "gate": [
            ("status", "ok"),
            ("model", "order_detail"),
            ("metric", "gmv"),
            ("intent", "metric_query"),
            ("dimensions", []),
        ],
        "sql_contains": ["SUM(__sell_through)", "__paid_at >=", "__paid_at <"],
        "tools_include": ["switch(order_detail)", "filter(", "aggregate(gmv)"],
    },
    {
        "name": "metric_query_order_count",
        "query": "最近7天订单数是多少？",
        "gate": [
            ("status", "ok"),
            ("model", "order_detail"),
            ("metric", "order_count"),
            ("intent", "metric_query"),
        ],
        "sql_contains": ["COUNT(DISTINCT __order_id)"],
        "tools_include": ["filter(", "aggregate(order_count)"],
    },
    {
        "name": "breakdown_channel_gmv",
        "query": "昨天各渠道 GMV 分别是多少？",
        "gate": [
            ("status", "ok"),
            ("model", "order_detail"),
            ("metric", "gmv"),
            ("intent", "breakdown"),
            ("dimensions", ["channel"]),
        ],
        "sql_contains": ["channel", "GROUP BY channel"],
        "tools_include": ["sort(gmv,DESC)"],
    },
    {
        "name": "breakdown_region",
        "query": "本月各大区订单数排名",
        "gate": [
            ("status", "ok"),
            ("model", "order_detail"),
            ("metric", "order_count"),
            ("intent", "breakdown"),
            ("dimensions", ["region"]),
        ],
        "sql_contains": ["LEFT JOIN dim_store", "region", "GROUP BY region"],
        "tools_include": ["sort(order_count,DESC)"],
    },
    {
        "name": "switch_user_summary",
        "query": "昨天各渠道用户 GMV",
        "gate": [
            ("status", "ok"),
            ("model", "user_summary"),
            ("metric", "gmv"),
            ("intent", "breakdown"),
            ("dimensions", ["channel"]),
        ],
        "sql_contains": ["channel", "GROUP BY channel"],
        "tools_include": ["switch(user_summary)"],
    },
    {
        "name": "product_breakdown",
        "query": "最近7天各品类 GMV",
        "gate": [
            ("status", "ok"),
            ("model", "product_analysis"),
            ("metric", "gmv"),
            ("intent", "breakdown"),
            ("dimensions", ["category"]),
        ],
        "sql_contains": ["category", "GROUP BY category", "LEFT JOIN dim_product"],
        "tools_include": ["switch(product_analysis)"],
    },
    {
        "name": "product_order_count",
        "query": "昨天各品类订单数",
        "gate": [
            ("status", "ok"),
            ("model", "product_analysis"),
            ("metric", "order_count"),
            ("intent", "breakdown"),
            ("dimensions", ["category"]),
        ],
        "sql_contains": ["category", "GROUP BY category"],
        "tools_include": ["sort(order_count,DESC)"],
    },
    {
        "name": "merge_gmv_order_count",
        "query": "昨天 GMV和订单数按渠道对比",
        "gate": [
            ("status", "ok"),
            ("model", "order_detail"),
            ("intent", "merge"),
        ],
        "sql_contains": ["JOIN", "channel", "gmv", "order_count"],
        "tools_include": ["merge("],
    },
    {
        "name": "blocked_query",
        "query": "删除昨天的订单数据",
        "gate": [
            ("status", "blocked"),
        ],
        "sql_contains": [],
        "tools_include": [],
    },
    {
        "name": "clarification_triggered",
        "query": "GMV 口径是什么？",
        "gate": [
            ("status", "clarification_needed"),
        ],
        "sql_contains": [],
        "tools_include": [],
    },
]


# ── Test Class ──

@pytest.mark.parametrize(
    "case",
    REGRESSION_CASES,
    ids=lambda c: c["name"],
)
class TestDeterministicGate:
    """Deterministic evaluation gate — no LLM required."""

    def _run_and_get_result(self, case: dict) -> dict:
        """Run the agent and return the result dict."""
        return run_graph(case["query"])

    def test_status(self, case):
        """Gate 1: Execution status."""
        result = self._run_and_get_result(case)
        for field, expected in case["gate"]:
            if field == "status":
                test_case = LLMTestCase(
                    input=case["query"],
                    actual_output=str(result.get("status")),
                    additional_metadata={"result": result},
                )
                metric = StatusMetric(expected_status=expected)
                score = metric.measure(test_case)
                assert metric.is_successful(), metric.reason
                return  # Only check status once

    def test_routing(self, case):
        """Gate 2: Model/Metric/Intent/Dimensions routing."""
        result = self._run_and_get_result(case)
        # Skip if clarification or blocked
        if result.get("status") in ("clarification_needed", "blocked"):
            pytest.skip("No routing for clarification/blocked")

        for field, expected in case["gate"]:
            if field == "status":
                continue  # Not a routing field
            test_case = LLMTestCase(
                input=case["query"],
                actual_output=str(result.get(field)),
                additional_metadata={"result": result},
            )
            metric = RoutingMetric(field=field, expected_value=expected)
            score = metric.measure(test_case)
            assert metric.is_successful(), metric.reason

    def test_sql_structure(self, case):
        """Gate 3: SQL contains expected fragments."""
        sql_contains = case.get("sql_contains", [])
        if not sql_contains:
            pytest.skip("No sql_contains expectations")

        result = self._run_and_get_result(case)
        if result.get("status") in ("clarification_needed", "blocked"):
            pytest.skip("No SQL for clarification/blocked")

        test_case = LLMTestCase(
            input=case["query"],
            actual_output=result.get("sql", ""),
            additional_metadata={"result": result},
        )
        metric = SQLStructureMetric(required_fragments=sql_contains)
        score = metric.measure(test_case)
        assert metric.is_successful(), metric.reason

    def test_tool_chain(self, case):
        """Gate 4: Tool chain includes expected operations."""
        tools_include = case.get("tools_include", [])
        if not tools_include:
            pytest.skip("No tools_include expectations")

        result = self._run_and_get_result(case)
        if result.get("status") in ("clarification_needed", "blocked"):
            pytest.skip("No tool chain for clarification/blocked")

        test_case = LLMTestCase(
            input=case["query"],
            actual_output=str(result.get("trace", [])),
            additional_metadata={"result": result},
        )
        metric = ToolChainMetric(expected_tools=tools_include)
        score = metric.measure(test_case)
        assert metric.is_successful(), metric.reason
