"""LLM Router evaluation benchmark.

Measures router quality against a labeled dataset to establish a baseline
and detect regression over time.

Metrics:
- Routing accuracy (correct intent/model/metric/dimension)
- Confusion matrix (per-intent accuracy breakdown)
- Edge case coverage (negations, ambiguous queries, spanglish)

Usage:
    from llm_eval_benchmark import LLMRouterBenchmark
    bench = LLMRouterBenchmark()
    bench.load_dataset("evals/router_test_cases.yaml")
    report = bench.run()
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import structlog

logger = structlog.get_logger("llm-eval")


@dataclass
class TestCase:
    id: str
    query: str
    expected_intent: str
    expected_model: Optional[str] = None
    expected_metric: Optional[str] = None
    expected_dimensions: Optional[list] = None
    category: str = "general"  # general | edge_case | negate | ambiguous | zh_en


@dataclass
class TestResult:
    case_id: str
    passed: bool
    actual_intent: str
    expected_intent: str
    actual_model: Optional[str] = None
    expected_model: Optional[str] = None
    actual_metric: Optional[str] = None
    expected_metric: Optional[str] = None
    duration_ms: float = 0
    error: Optional[str] = None


@dataclass
class BenchmarkReport:
    total_cases: int
    passed: int
    failed: int
    errors: int
    accuracy: float
    accuracy_by_intent: dict   # intent → {correct, total, rate}
    accuracy_by_category: dict  # category → {correct, total, rate}
    failures: list[dict]        # failed case details
    duration_ms: float
    baseline_version: str = "1.0.0"


# ── Default test cases ──

DEFAULT_TEST_CASES = [
    # ── metric_query ──
    TestCase("mq_01", "昨天 GMV 是多少？", "metric_query", "order_detail", "gmv", None, "general"),
    TestCase("mq_02", "上个月订单数", "metric_query", "order_detail", "order_count", None, "general"),
    TestCase("mq_03", "今天客单价", "metric_query", "order_detail", "aov", None, "general"),
    TestCase("mq_04", "平均价格", "metric_query", "order_detail", "avg_price", None, "general"),
    TestCase("mq_05", "total revenue last week", "metric_query", "order_detail", "gmv", None, "zh_en"),

    # ── breakdown ──
    TestCase("bd_01", "最近7天各渠道的 GMV", "breakdown", "order_detail", "gmv", ["channel"], "general"),
    TestCase("bd_02", "各区域订单量", "breakdown", "order_detail", "order_count", ["region"], "general"),
    TestCase("bd_03", "每个品类的客单价", "breakdown", "order_detail", "aov", ["category"], "general"),
    TestCase("bd_04", "按日期看GMV趋势", "breakdown", "order_detail", "gmv", ["date"], "general"),

    # ── filter_value ──
    TestCase("fv_01", "线上渠道的GMV", "filter_value", "order_detail", "gmv", ["channel"], "general"),
    TestCase("fv_02", "只看华南大区的订单", "filter_value", "order_detail", "order_count", ["region"], "general"),
    TestCase("fv_03", "数码品类的销售额", "filter_value", "order_detail", "gmv", ["category"], "general"),

    # ── merge ──
    TestCase("mg_01", "各渠道的GMV和订单数", "merge", "order_detail", None, ["channel"], "general"),
    TestCase("mg_02", "GMV和客单价按区域", "merge", "order_detail", None, ["region"], "general"),

    # ── compare_periods ──
    TestCase("cp_01", "这个月和上个月GMV对比", "compare_periods", "order_detail", "gmv", None, "general"),

    # ── blocked ──
    TestCase("bl_01", "删库跑路", "blocked", None, None, None, "edge_case"),
    TestCase("bl_02", "DROP TABLE users", "blocked", None, None, None, "edge_case"),

    # ── Edge cases ──
    TestCase("ed_01", "牛逼", "blocked", None, None, None, "edge_case"),
    TestCase("ed_02", "how much did we sell yesterday", "metric_query", "order_detail", "gmv", None, "zh_en"),
    TestCase("ed_03", "查一下看看", "metric_query", "order_detail", "gmv", None, "ambiguous"),
    TestCase("ed_04", "没有吗？", "blocked", None, None, None, "edge_case"),
]


class LLMRouterBenchmark:
    """LLM router evaluation benchmark."""

    def __init__(self, router_fn=None):
        self.router_fn = router_fn or self._regex_router
        self.test_cases: list[TestCase] = list(DEFAULT_TEST_CASES)

    @staticmethod
    def _regex_router(query: str) -> dict:
        from dag_agent import route_and_plan
        return route_and_plan(query, use_llm=False)

    def load_dataset(self, path: str):
        """Load additional test cases from YAML/JSON file."""
        p = Path(path)
        if not p.exists():
            logger.warning("dataset_not_found", path=path)
            return

        if p.suffix in (".yaml", ".yml"):
            import yaml
            with open(p) as f:
                data = yaml.safe_load(f)
        elif p.suffix == ".json":
            data = json.loads(p.read_text())
        else:
            return

        for case in data if isinstance(data, list) else data.get("test_cases", []):
            self.test_cases.append(TestCase(
                id=case.get("id", f"loaded_{len(self.test_cases)}"),
                query=case["query"],
                expected_intent=case["expected_intent"],
                expected_model=case.get("expected_model"),
                expected_metric=case.get("expected_metric"),
                expected_dimensions=case.get("expected_dimensions"),
                category=case.get("category", "general"),
            ))

    def add_test_case(self, query: str, expected_intent: str, **kwargs):
        tc = TestCase(
            id=f"manual_{len(self.test_cases)}",
            query=query, expected_intent=expected_intent, **kwargs
        )
        self.test_cases.append(tc)

    def run(self) -> BenchmarkReport:
        """Run all test cases and produce a benchmark report."""
        results: list[TestResult] = []
        t0 = time.time()

        for tc in self.test_cases:
            case_t0 = time.time()
            try:
                plan = self.router_fn(tc.query)
                dt = (time.time() - case_t0) * 1000

                intent = plan.get("intent", "unknown")
                # If query is blocked, intent should be "blocked" regardless of what router returns
                if plan.get("status") == "blocked":
                    intent = "blocked"
                intent_ok = intent == tc.expected_intent
                model_ok = True
                metric_ok = True

                if tc.expected_model and plan.get("model") != tc.expected_model:
                    model_ok = False
                if tc.expected_metric and plan.get("metric") != tc.expected_metric:
                    metric_ok = False

                passed = intent_ok and model_ok and metric_ok

                results.append(TestResult(
                    case_id=tc.id, passed=passed,
                    actual_intent=intent, expected_intent=tc.expected_intent,
                    actual_model=plan.get("model"), expected_model=tc.expected_model,
                    actual_metric=plan.get("metric"), expected_metric=tc.expected_metric,
                    duration_ms=dt,
                ))
            except Exception as e:
                results.append(TestResult(
                    case_id=tc.id, passed=False,
                    actual_intent="error", expected_intent=tc.expected_intent,
                    duration_ms=(time.time() - case_t0) * 1000,
                    error=str(e),
                ))

        dt = (time.time() - t0) * 1000
        total = len(results)
        passed = sum(1 for r in results if r.passed)
        failed = sum(1 for r in results if not r.passed and not r.error)
        errors = sum(1 for r in results if r.error)
        accuracy = passed / total if total > 0 else 0

        # By intent
        by_intent = {}
        for r in results:
            intent = r.expected_intent
            if intent not in by_intent:
                by_intent[intent] = {"correct": 0, "total": 0}
            by_intent[intent]["total"] += 1
            if r.passed:
                by_intent[intent]["correct"] += 1
        for intent in by_intent:
            t = by_intent[intent]
            t["rate"] = round(t["correct"] / t["total"] * 100, 1) if t["total"] else 0

        # By category
        by_category = {}
        for r, tc in zip(results, self.test_cases):
            cat = tc.category
            if cat not in by_category:
                by_category[cat] = {"correct": 0, "total": 0}
            by_category[cat]["total"] += 1
            if r.passed:
                by_category[cat]["correct"] += 1
        for cat in by_category:
            t = by_category[cat]
            t["rate"] = round(t["correct"] / t["total"] * 100, 1) if t["total"] else 0

        # Failure details
        failures = [
            {
                "case_id": r.case_id,
                "query": next((tc.query for tc in self.test_cases if tc.id == r.case_id), "?"),
                "expected": r.expected_intent,
                "actual": r.actual_intent,
                "error": r.error,
            }
            for r in results if not r.passed
        ]

        return BenchmarkReport(
            total_cases=total, passed=passed, failed=failed, errors=errors,
            accuracy=accuracy,
            accuracy_by_intent=by_intent,
            accuracy_by_category=by_category,
            failures=failures,
            duration_ms=dt,
        )


# ── Convenience ──

def run_benchmark() -> dict:
    bench = LLMRouterBenchmark()
    report = bench.run()
    d = report.__dict__
    d["passed_pct"] = f"{d['accuracy']:.1%}"
    return d
