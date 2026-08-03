# -*- coding: utf-8 -*-
"""Phase 7 Eval Harness — deterministic coverage for execution, follow-up, and analysis.

Suites:
  - core: basic execution contract + insight contract
  - execution: retry / validation / confidence / unsupported task type
  - multiturn: follow-up detection + rule registry
  - advanced: comparison / anomaly / attribution strategies
  - product: report + chart + insight contract

Usage:
    python scripts/run_phase7_eval.py [suite]
    python scripts/run_phase7_eval.py all
"""
from __future__ import unicode_literals

import codecs
import json
import os
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.join(ROOT, "src"))

from eval_baseline import EvalBaseline, evaluate_gate


def _suite_core():
    from execution_engine import ExecutionEngine
    from result_explainer import build_insight_bundle

    class _RT(object):
        def __init__(self):
            self.trace = []
            self.datasets = {}
            self.sql = "SELECT 1"

        def switch(self, model):
            self.datasets["d1"] = {}
            return "d1"

        def _register(self, dataid, label=None):
            self.datasets[dataid] = {"label": label}
            return dataid

        def filter_time_and_defaults(self, dataid, metric, start, end):
            return dataid

        def aggregate(self, dataid, metric, dimensions):
            return dataid

        def sort(self, dataid, by, order="DESC"):
            return dataid

        def compile_sql(self, dataid):
            return self.sql

    class _Exec(object):
        def __init__(self, rows):
            self.rows = rows

        def execute(self, sql):
            return {"rows": self.rows, "row_count": len(self.rows), "source": "fake", "sql": sql}

    results = []

    engine = ExecutionEngine(executor=_Exec([{"gmv": 100}]), runtime_factory=lambda: _RT(), validator=lambda sql: (True, None), max_retries=0)
    ok = engine.execute({"model": "order_detail", "metric": "gmv", "task_type": "descriptive"})
    results.append({"query": "exec_ok", "passed": ok.get("status") == "ok" and ok.get("confidence") is not None, "status": ok.get("status"), "expected": "ok"})

    empty_engine = ExecutionEngine(executor=_Exec([]), runtime_factory=lambda: _RT(), validator=lambda sql: (True, None), max_retries=0)
    empty = empty_engine.execute({"model": "order_detail", "metric": "gmv", "task_type": "descriptive"})
    results.append({"query": "exec_empty", "passed": empty.get("status") == "ok" and empty.get("diagnostics", {}).get("quality", {}).get("empty_result") is True, "status": empty.get("status"), "expected": "ok"})

    blocked_engine = ExecutionEngine(executor=_Exec([{"gmv": 100}]), runtime_factory=lambda: _RT(), validator=lambda sql: (False, "blocked by policy"), max_retries=0)
    blocked = blocked_engine.execute({"model": "order_detail", "metric": "gmv", "task_type": "descriptive"})
    results.append({"query": "exec_blocked", "passed": blocked.get("status") == "error" and blocked.get("diagnostics", {}).get("failure_type") == "sql_validation_error", "status": blocked.get("status"), "expected": "error"})

    insight = build_insight_bundle({"status": "ok", "metric": "gmv", "task_type": "descriptive"}, ok)
    results.append({"query": "insight_bundle", "passed": bool(insight.summary) and isinstance(insight.chart, dict), "status": "ok", "expected": "ok"})

    return results


def _suite_execution():
    from execution_engine import ExecutionEngine
    from runtime_core import validate_sql

    class _RT(object):
        def __init__(self):
            self.trace = []
            self.datasets = {}
            self.sql = "SELECT 1"

        def switch(self, model):
            self.datasets["d1"] = {}
            return "d1"

        def _register(self, dataid, label=None):
            self.datasets[dataid] = {"label": label}
            return dataid

        def filter_time_and_defaults(self, dataid, metric, start, end):
            return dataid

        def aggregate(self, dataid, metric, dimensions):
            return dataid

        def sort(self, dataid, by, order="DESC"):
            return dataid

        def compile_sql(self, dataid):
            return self.sql

    class _OkExec(object):
        def execute(self, sql):
            return {"rows": [{"gmv": 100}], "row_count": 1, "source": "fake", "sql": sql}

    class _EmptyExec(object):
        def execute(self, sql):
            return {"rows": [], "row_count": 0, "source": "fake", "sql": sql}

    class _FailExec(object):
        def __init__(self):
            self.calls = 0

        def execute(self, sql):
            self.calls += 1
            raise RuntimeError("db error")

    results = []

    engine = ExecutionEngine(executor=_OkExec(), runtime_factory=lambda: _RT(), validator=lambda sql: (True, None), max_retries=0)
    r = engine.execute({"model": "order_detail", "metric": "gmv"})
    results.append({"query": "normal_exec", "passed": r.get("confidence") is not None and r["status"] == "ok", "status": r["status"], "expected": "ok"})

    engine2 = ExecutionEngine(executor=_EmptyExec(), runtime_factory=lambda: _RT(), validator=lambda sql: (True, None), max_retries=0)
    r2 = engine2.execute({"model": "order_detail", "metric": "gmv"})
    results.append({"query": "empty_result", "passed": r2["diagnostics"]["quality"]["empty_result"] is True, "status": r2["status"], "expected": "ok"})

    fail_exec = _FailExec()
    engine3 = ExecutionEngine(executor=fail_exec, runtime_factory=lambda: _RT(), validator=lambda sql: (True, None), max_retries=1)
    r3 = engine3.execute({"model": "order_detail", "metric": "gmv"})
    results.append({"query": "retry_exhausted", "passed": r3["status"] == "error" and fail_exec.calls == 2, "status": r3["status"], "expected": "error"})

    ok, reason = validate_sql("DELETE FROM orders")
    results.append({"query": "validate_dangerous", "passed": ok is False and bool(reason), "status": "blocked" if not ok else "ok", "expected": "blocked"})

    engine5 = ExecutionEngine(executor=_OkExec(), runtime_factory=lambda: _RT(), validator=lambda sql: (True, None), max_retries=0)
    r5 = engine5.execute({"model": "order_detail", "metric": "gmv", "task_type": "funnel"})
    results.append({"query": "unsupported_task_type", "passed": r5["status"] == "error" and r5["diagnostics"]["failure_type"] == "unsupported_task_type", "status": r5["status"], "expected": "error"})

    return results


def _suite_multiturn():
    from followup_policy import resolve_followup, register_follow_up_rule, list_follow_up_rules
    from session import Session, Turn

    def _make_session():
        s = Session("eval-mt")
        s.context["model"] = "order_detail"
        s.context["metric"] = "gmv"
        s.context["dimensions"] = ["date"]
        s.context["time_range"] = "last_7_days"
        s.turns.append(Turn(query="look gmv", result={"status": "ok", "model": "order_detail", "metric": "gmv"}))
        return s

    results = []

    register_follow_up_rule("eval_switch_metric", ["switch metric", "orders"], {"metric": "order_count", "is_follow_up": True})
    r1 = resolve_followup("switch metric to orders", _make_session())
    results.append({"query": "metric_switch", "passed": r1 is not None and r1.get("metric") == "order_count", "status": "ok", "expected": "ok"})

    register_follow_up_rule("eval_drilldown", ["drill down", "by category"], {"dimensions": ["category"], "is_follow_up": True})
    r2 = resolve_followup("drill down by category", _make_session())
    results.append({"query": "dimension_drilldown", "passed": r2 is not None and r2.get("dimensions") == ["category"], "status": "ok", "expected": "ok"})

    register_follow_up_rule("eval_filter", ["only", "taobao"], {"filters": {"channel": "taobao"}, "is_follow_up": True})
    r3 = resolve_followup("only taobao", _make_session())
    results.append({"query": "filter_patch", "passed": r3 is not None and r3.get("filters", {}).get("channel") == "taobao", "status": "ok", "expected": "ok"})

    register_follow_up_rule("eval_compare", ["compare", "previous month"], {"task_type": "comparison", "compare_to": "previous_month", "is_follow_up": True})
    r4 = resolve_followup("compare previous month", _make_session())
    results.append({"query": "compare_transition", "passed": r4 is not None and r4.get("task_type") == "comparison", "status": "ok", "expected": "ok"})

    s_empty = Session("eval-empty")
    r5 = resolve_followup("switch metric to orders", s_empty)
    results.append({"query": "no_context_no_followup", "passed": r5 is None, "status": "ok", "expected": "ok"})

    count_before = len(list_follow_up_rules())
    register_follow_up_rule("eval_custom", ["test rule"], {"intent": "custom"})
    results.append({"query": "register_rule", "passed": len(list_follow_up_rules()) >= count_before + 1, "status": "ok", "expected": "ok"})

    return results


def _suite_advanced():
    from execution_engine import ExecutionEngine

    class _RT(object):
        def __init__(self):
            self.trace = []
            self.datasets = {}
            self.sql = "SELECT 1"
            self._counter = 0

        def switch(self, model):
            self.datasets["d1"] = {}
            return "d1"

        def _register(self, *args, **kwargs):
            self._counter += 1
            did = "ds_%d" % self._counter
            self.datasets[did] = {}
            return did

        def filter_time_and_defaults(self, dataid, metric, start, end):
            return dataid

        def aggregate(self, dataid, metric, dimensions):
            return dataid

        def sort(self, dataid, by, order="DESC"):
            return dataid

        def compile_sql(self, dataid):
            return self.sql

    class _OkExec(object):
        def execute(self, sql):
            return {"rows": [{"gmv": 100, "previous_gmv": 80}], "row_count": 1, "source": "fake", "sql": sql}

    results = []
    engine = ExecutionEngine(executor=_OkExec(), runtime_factory=lambda: _RT(), validator=lambda sql: (True, None), max_retries=0)

    for task_type in ["descriptive", "comparison", "anomaly", "attribution"]:
        plan = {"model": "order_detail", "metric": "gmv", "task_type": task_type}
        if task_type == "comparison":
            plan["previous_time_range"] = ["2026-07-01", "2026-07-07"]
        r = engine.execute(plan)
        results.append({
            "query": "strategy_%s" % task_type,
            "passed": r["status"] == "ok" and r["diagnostics"]["strategy"] == task_type,
            "status": r["status"],
            "expected": "ok",
        })

    return results


def _suite_product():
    from result_explainer import build_insight_bundle
    from report_generator import ReportGenerator

    results = []

    plan = {"status": "ok", "metric": "gmv", "task_type": "descriptive", "time_range": "last_7_days"}
    exec_result = {"status": "ok", "results": [{"gmv": 100}], "diagnostics": {"strategy": "descriptive", "quality": {}}}
    insight = build_insight_bundle(plan, exec_result)
    d = insight.to_dict()
    results.append({"query": "insight_structure", "passed": "headline" in d and "next_steps" in d, "status": "ok", "expected": "ok"})

    gen = ReportGenerator()
    report = gen.generate(plan, exec_result)
    results.append({"query": "report_structure", "passed": "headline" in report and "evidence" in report, "status": "ok", "expected": "ok"})

    return results


SUITES = {
    "core": _suite_core,
    "execution": _suite_execution,
    "multiturn": _suite_multiturn,
    "advanced": _suite_advanced,
    "product": _suite_product,
}


def run_suites(selected=None):
    if selected is None or selected == "all":
        selected = list(SUITES.keys())
    elif isinstance(selected, str):
        selected = [selected]

    all_results = {}
    total_pass = 0
    total_fail = 0

    for name in selected:
        if name not in SUITES:
            print("WARN: unknown suite '%s'" % name)
            continue
        t0 = time.time()
        try:
            cases = SUITES[name]()
        except Exception as e:
            print("ERROR running suite '%s': %r" % (name, e))
            all_results[name] = {"error": repr(e)}
            continue
        elapsed = time.time() - t0

        passed = [c for c in cases if c["passed"]]
        failed = [c for c in cases if not c["passed"]]
        total_pass += len(passed)
        total_fail += len(failed)

        print("\n[%s] %d/%d passed (%.1fms)" % (name.upper(), len(passed), len(cases), elapsed * 1000))
        for f in failed:
            print("  FAIL: %s (got=%s expected=%s)" % (f["query"], f["status"], f["expected"]))

        all_results[name] = {
            "total": len(cases),
            "passed": len(passed),
            "failed_items": failed,
            "elapsed_ms": round(elapsed * 1000, 1),
        }

    total = total_pass + total_fail
    rate = float(total_pass) / total if total > 0 else 0.0
    print("\n" + "=" * 50)
    print("PHASE7_EVAL total=%d passed=%d failed=%d rate=%.2f" % (total, total_pass, total_fail, rate))
    print("=" * 50)

    baseline = EvalBaseline(name="phase7-ci", metrics={"pass_rate_min": 0.85})
    gate = evaluate_gate({"metrics": {"pass_rate": rate}}, baseline).to_dict()
    print("GATE passed=%s" % gate["passed"])
    if not gate["passed"]:
        for f in gate.get("failures", []):
            print("  - %s" % f)

    return {"suites": all_results, "total": total, "passed": total_pass, "failed": total_fail, "rate": rate, "gate": gate}


def main():
    # DEPRECATION: historical Phase 7 compatibility evaluator. New features
    # must be represented in harness/cases and gated by run_harness_gate.py.
    sys.stderr.write(
        "[deprecation] run_phase7_eval is legacy-compatible; "
        "prefer scripts/run_harness_gate.py for new work.\n")
    suite = sys.argv[1] if len(sys.argv) > 1 else "all"

    result = run_suites(suite)
    out_path = os.path.join(ROOT, "evals", "phase7_eval_report.json")
    with codecs.open(out_path, "w", "utf-8") as f:
        f.write(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    print("Report saved to %s" % out_path)
    return 0 if result["gate"]["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
