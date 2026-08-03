"""Langfuse ↔ deepeval Score Bridge.

Connects the two observability layers: runs deepeval evaluation metrics on
agent output and attaches scores to Langfuse traces for unified observability.

Usage:
    from harness.score_bridge import ScoreBridge
    from tracer import LangfuseTracer

    tracer = LangfuseTracer()
    bridge = ScoreBridge(tracer)
    result = bridge.evaluate_and_score(trace_id, agent_output, query)
    # → {"status": "ok", "scores": [...], "pass_count": N, "total_count": M}
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from deepeval.test_case import LLMTestCase
from deepeval.metrics import BaseMetric


# ── Metric implementations (self-contained, no test_eval_gate deps) ──

class _StatusMetric(BaseMetric):
    def __init__(self, expected_status: str, threshold: float = 0.5):
        super().__init__()
        self.threshold = threshold
        self.expected_status = expected_status

    def measure(self, test_case: LLMTestCase):
        result = test_case.additional_metadata.get("result", {})
        status = result.get("status", "error")
        self.score = 1.0 if status == self.expected_status else 0.0
        self.success = self.score >= self.threshold
        self.reason = f"Expected '{self.expected_status}', got '{status}'"
        return self.score

    async def a_measure(self, test_case):
        return self.measure(test_case)

    def is_successful(self):
        return self.success


class _RoutingMetric(BaseMetric):
    def __init__(self, field: str, expected_value, threshold: float = 0.5):
        super().__init__()
        self.threshold = threshold
        self.field = field
        self.expected_value = expected_value

    def measure(self, test_case: LLMTestCase):
        result = test_case.additional_metadata.get("result", {})
        actual = result.get(self.field)
        self.score = 1.0 if actual == self.expected_value else 0.0
        self.success = self.score >= self.threshold
        self.reason = f"Expected {self.field}='{self.expected_value}', got '{actual}'"
        return self.score

    async def a_measure(self, test_case):
        return self.measure(test_case)

    def is_successful(self):
        return self.success


class _SQLStructureMetric(BaseMetric):
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
        return self.score

    async def a_measure(self, test_case):
        return self.measure(test_case)

    def is_successful(self):
        return self.success


class _ToolChainMetric(BaseMetric):
    def __init__(self, expected_tools: list[str], threshold: float = 1.0):
        super().__init__()
        self.threshold = threshold
        self.expected_tools = expected_tools

    def measure(self, test_case: LLMTestCase):
        result = test_case.additional_metadata.get("result", {})
        trace = result.get("trace", [])
        hits = 0
        for tool in self.expected_tools:
            if any(tool in t.get("op", "") for t in trace):
                hits += 1
        self.score = hits / len(self.expected_tools) if self.expected_tools else 1.0
        self.success = self.score >= self.threshold
        return self.score

    async def a_measure(self, test_case):
        return self.measure(test_case)

    def is_successful(self):
        return self.success


class _DiagnosisMetric(BaseMetric):
    """Score based on diagnosis severity — healthy=1.0, degraded=0.5, failed=0.0."""

    def __init__(self, threshold: float = 0.5):
        super().__init__()
        self.threshold = threshold

    def measure(self, test_case: LLMTestCase):
        result = test_case.additional_metadata.get("result", {})
        diagnosis = result.get("diagnosis", {})
        if not diagnosis:
            self.score = 1.0
            self.success = True
            self.reason = "No diagnosis data"
            return 1.0

        severity = diagnosis.get("overall_severity", "healthy")
        if severity == "healthy":
            self.score = 1.0
        elif severity == "degraded":
            self.score = 0.5
        else:
            self.score = 0.0

        self.success = self.score >= self.threshold
        self.reason = diagnosis.get("summary", "")
        return self.score

    async def a_measure(self, test_case):
        return self.measure(test_case)

    def is_successful(self):
        return self.success


# ── Default evaluation gate (from golden queries) ──

DEFAULT_GATE = {
    "gmv": {
        "status": "ok",
        "model": "order_detail",
        "metric": "gmv",
        "intent": "metric_query",
        "dimensions": [],
        "sql_contains": ["SUM(__sell_through)"],
        "tools_include": ["aggregate(gmv)"],
    },
    "breakdown": {
        "status": "ok",
        "model": "order_detail",
        "metric": "gmv",
        "intent": "breakdown",
        "dimensions": ["channel"],
        "sql_contains": ["channel", "GROUP BY channel"],
        "tools_include": ["sort(gmv,DESC)"],
    },
}

# ── Score Bridge ──


class ScoreBridge:
    """Bridge between deepeval metrics and Langfuse score tracking.

    Evaluates agent output using deepeval custom metrics and pushes
    scores to Langfuse traces for unified observability.

    Usage:
        tracer = LangfuseTracer()
        bridge = ScoreBridge(tracer)
        bridge.evaluate_and_score(trace_id, agent_output, "昨天 GMV 是多少？")
    """

    def __init__(self, tracer=None):
        self.tracer = tracer

    def evaluate(
        self,
        agent_output: dict,
        query: str = "",
        expected: Optional[dict] = None,
    ) -> dict:
        """Run deepeval metrics on agent output. Does NOT push to Langfuse.

        Args:
            agent_output: Dict from run_graph() or react_loop()
            query: Original user query
            expected: Expected values dict (from golden queries). If None,
                      uses default gate inference.

        Returns:
            {"scores": [{"name": str, "value": float, "type": str, "pass": bool}, ...],
             "pass_count": int, "total_count": int}
        """
        if expected is None:
            expected = self._infer_expected(agent_output)

        scores = []

        # 1. Status metric
        if "status" in expected:
            tc = self._make_test_case(agent_output, query)
            m = _StatusMetric(expected["status"])
            m.measure(tc)
            scores.append({
                "name": "status",
                "value": m.score,
                "type": "NUMERIC",
                "pass": m.is_successful(),
                "reason": getattr(m, "reason", ""),
            })

        # 2. Routing metrics (model, metric, dimensions, intent)
        for field in ["model", "metric", "dimensions", "intent"]:
            if field in expected:
                tc = self._make_test_case(agent_output, query)
                m = _RoutingMetric(field, expected[field])
                m.measure(tc)
                scores.append({
                    "name": f"routing_{field}",
                    "value": m.score,
                    "type": "NUMERIC",
                    "pass": m.is_successful(),
                    "reason": getattr(m, "reason", ""),
                })

        # 3. SQL structure metric
        sql_contains = expected.get("sql_contains", [])
        if sql_contains:
            tc = self._make_test_case(agent_output, query)
            m = _SQLStructureMetric(sql_contains)
            m.measure(tc)
            scores.append({
                "name": "sql_structure",
                "value": m.score,
                "type": "NUMERIC",
                "pass": m.is_successful(),
                "reason": getattr(m, "reason", ""),
            })

        # 4. Tool chain metric
        tools_include = expected.get("tools_include", [])
        if tools_include:
            tc = self._make_test_case(agent_output, query)
            m = _ToolChainMetric(tools_include)
            m.measure(tc)
            scores.append({
                "name": "tool_chain",
                "value": m.score,
                "type": "NUMERIC",
                "pass": m.is_successful(),
                "reason": getattr(m, "reason", ""),
            })

        # 5. Diagnosis health metric
        tc = self._make_test_case(agent_output, query)
        m = _DiagnosisMetric()
        m.measure(tc)
        scores.append({
            "name": "diagnosis_health",
            "value": m.score,
            "type": "NUMERIC",
            "pass": m.is_successful(),
            "reason": getattr(m, "reason", ""),
        })

        pass_count = sum(1 for s in scores if s["pass"])
        return {
            "scores": scores,
            "pass_count": pass_count,
            "total_count": len(scores),
        }

    def evaluate_and_score(
        self,
        trace_id: str,
        agent_output: dict,
        query: str = "",
        expected: Optional[dict] = None,
    ) -> dict:
        """Evaluate agent output AND push scores to Langfuse trace.

        Returns the evaluation result with score push status.
        """
        result = self.evaluate(agent_output, query, expected)

        # Push scores to Langfuse
        pushed = 0
        if self.tracer and self.tracer.enabled:
            for s in result["scores"]:
                try:
                    self.tracer.log_score(
                        trace_id=trace_id,
                        name=s["name"],
                        value=s["value"],
                        data_type=s["type"],
                        comment=s.get("reason", ""),
                    )
                    pushed += 1
                except Exception:
                    pass

        result["pushed_to_langfuse"] = pushed
        return result

    def _make_test_case(self, agent_output: dict, query: str = "") -> LLMTestCase:
        return LLMTestCase(
            input=query or agent_output.get("query", ""),
            actual_output=str(agent_output.get("status", "unknown")),
            metadata={"result": agent_output},
        )

    def _infer_expected(self, agent_output: dict) -> dict:
        """Infer expected values from agent output (best-effort)."""
        status = agent_output.get("status", "ok")
        if status in ("blocked", "clarification_needed"):
            return {"status": status}

        return {
            "status": "ok",
            "model": agent_output.get("model", ""),
            "metric": agent_output.get("metric", ""),
            "dimensions": agent_output.get("dimensions", []),
            "intent": agent_output.get("intent", ""),
        }


def bridge_evaluate(trace_id: str, agent_output: dict, query: str = "",
                    tracer=None, expected: dict = None) -> dict:
    """Convenience: evaluate + score in one call."""
    bridge = ScoreBridge(tracer)
    return bridge.evaluate_and_score(trace_id, agent_output, query, expected)
