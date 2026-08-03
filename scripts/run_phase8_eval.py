# -*- coding: utf-8 -*-
"""Phase 8 Eval Harness — execution loop, observability, and trace diagnostics.

Suites:
  - execution: retry / validation / confidence / stage diagnostics
  - observability: trace_id propagation, trace_summary, failure_stage
  - facade: end-to-end ask() path with diagnostics and trace

Usage:
    python scripts/run_phase8_eval.py [suite]
    python scripts/run_phase8_eval.py all
"""
from __future__ import unicode_literals

import json
import os
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.join(ROOT, "src"))

from eval_baseline import EvalBaseline, evaluate_gate


class _RT(object):
    def __init__(self, sql="SELECT 1"):
        self.trace = []
        self.datasets = {}
        self.sql = sql

    def switch(self, model):
        self.datasets["d1"] = {}
        return "d1"

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


def _suite_execution():
    from execution_engine import ExecutionEngine
    from observability import ObservationRecorder

    results = []

    # 1. Normal execution includes trace_id and stage in diagnostics
    obs = ObservationRecorder()
    engine = ExecutionEngine(executor=_OkExec(), runtime_factory=lambda: _RT(), validator=lambda sql: (True, None), max_retries=0, observer=obs)
    r = engine.execute({"model": "order_detail", "metric": "gmv"}, trace_id="t-exec-1", task_id="task-1")
    diag = r.get("diagnostics", {})
    results.append({
        "query": "exec_has_trace_id",
        "passed": r.get("trace_id") == "t-exec-1" and diag.get("stage") == "execute",
        "status": r["status"],
        "expected": "ok",
    })

    # 2. Error result includes trace_id and stage
    obs2 = ObservationRecorder()
    engine2 = ExecutionEngine(executor=_OkExec(), runtime_factory=lambda: _RT(), validator=lambda sql: (False, "blocked"), max_retries=0, observer=obs2)
    r2 = engine2.execute({"model": "order_detail", "metric": "gmv"}, trace_id="t-exec-2")
    diag2 = r2.get("diagnostics", {})
    results.append({
        "query": "error_has_trace_id_and_stage",
        "passed": r2.get("trace_id") == "t-exec-2" and diag2.get("stage") == "validate_sql",
        "status": r2["status"],
        "expected": "error",
    })

    # 3. Retry exhausted includes proper stage
    obs3 = ObservationRecorder()
    engine3 = ExecutionEngine(executor=_FailExec(), runtime_factory=lambda: _RT(), validator=lambda sql: (True, None), max_retries=1, observer=obs3)
    r3 = engine3.execute({"model": "order_detail", "metric": "gmv"}, trace_id="t-exec-3")
    diag3 = r3.get("diagnostics", {})
    results.append({
        "query": "retry_exhausted_stage",
        "passed": r3["status"] == "error" and diag3.get("retry_exhausted") is True and diag3.get("stage") == "execute",
        "status": r3["status"],
        "expected": "error",
    })

    # 4. Unsupported task type has correct stage
    obs4 = ObservationRecorder()
    engine4 = ExecutionEngine(executor=_OkExec(), runtime_factory=lambda: _RT(), validator=lambda sql: (True, None), max_retries=0, observer=obs4)
    r4 = engine4.execute({"model": "order_detail", "metric": "gmv", "task_type": "funnel"}, trace_id="t-exec-4")
    diag4 = r4.get("diagnostics", {})
    results.append({
        "query": "unsupported_stage",
        "passed": r4["status"] == "error" and diag4.get("stage") == "strategy_dispatch",
        "status": r4["status"],
        "expected": "error",
    })

    return results


def _suite_observability():
    from execution_engine import ExecutionEngine
    from observability import ObservationRecorder

    results = []

    # 1. Observer records events for successful execution
    obs = ObservationRecorder()
    engine = ExecutionEngine(executor=_OkExec(), runtime_factory=lambda: _RT(), validator=lambda sql: (True, None), max_retries=0, observer=obs)
    engine.execute({"model": "order_detail", "metric": "gmv"}, trace_id="t-obs-1", task_id="task-obs-1")
    events = obs.events(trace_id="t-obs-1")
    results.append({
        "query": "observer_records_events",
        "passed": len(events) >= 1 and events[0].name == "execute",
        "status": "ok",
        "expected": "ok",
    })

    # 2. Observer summary captures failure stage
    obs2 = ObservationRecorder()
    engine2 = ExecutionEngine(executor=_FailExec(), runtime_factory=lambda: _RT(), validator=lambda sql: (True, None), max_retries=0, observer=obs2)
    engine2.execute({"model": "order_detail", "metric": "gmv"}, trace_id="t-obs-2")
    summary = obs2.summarize("t-obs-2")
    results.append({
        "query": "observer_failure_summary",
        "passed": summary["failed"] is True and summary["failure_stage"] is not None,
        "status": "ok",
        "expected": "ok",
    })

    # 3. trace_summary is present in diagnostics
    obs3 = ObservationRecorder()
    engine3 = ExecutionEngine(executor=_OkExec(), runtime_factory=lambda: _RT(), validator=lambda sql: (True, None), max_retries=0, observer=obs3)
    r3 = engine3.execute({"model": "order_detail", "metric": "gmv"}, trace_id="t-obs-3")
    results.append({
        "query": "trace_summary_in_diagnostics",
        "passed": r3["diagnostics"].get("trace_summary") is not None,
        "status": "ok",
        "expected": "ok",
    })

    # 4. No observer still works (graceful degradation)
    engine4 = ExecutionEngine(executor=_OkExec(), runtime_factory=lambda: _RT(), validator=lambda sql: (True, None), max_retries=0, observer=None)
    r4 = engine4.execute({"model": "order_detail", "metric": "gmv"}, trace_id="t-obs-4")
    results.append({
        "query": "no_observer_graceful",
        "passed": r4["status"] == "ok" and r4["diagnostics"].get("trace_summary") is None,
        "status": "ok",
        "expected": "ok",
    })

    return results


def _suite_facade():
    from agent_facade import AgentFacade

    results = []

    # 1. Facade ask returns diagnostics with retry_count
    facade = AgentFacade(session_id="phase8-eval")
    r = facade.ask("GMV")
    results.append({
        "query": "facade_diagnostics_present",
        "passed": "diagnostics" in r and "retry_count" in r.get("diagnostics", {}),
        "status": r.get("status", "unknown"),
        "expected": "ok",
    })

    # 2. Facade provides trace events
    trace = facade.get_trace()
    results.append({
        "query": "facade_trace_events",
        "passed": len(trace) >= 2,
        "status": "ok",
        "expected": "ok",
    })

    # 3. Trace events have standard fields
    if trace:
        first = trace[0]
        has_fields = "trace_id" in first and "name" in first and "status" in first
        results.append({
            "query": "trace_event_fields",
            "passed": has_fields,
            "status": "ok",
            "expected": "ok",
        })
    else:
        results.append({"query": "trace_event_fields", "passed": False, "status": "error", "expected": "ok"})

    return results


SUITES = {
    "execution": _suite_execution,
    "observability": _suite_observability,
    "facade": _suite_facade,
}


def run_eval(suite_name=None):
    if suite_name and suite_name != "all":
        suites_to_run = {suite_name: SUITES[suite_name]}
    else:
        suites_to_run = SUITES

    all_results = []
    for name, fn in sorted(suites_to_run.items()):
        suite_results = fn()
        for r in suite_results:
            r["suite"] = name
        all_results.extend(suite_results)
        passed = sum(1 for r in suite_results if r["passed"])
        print("[%s] %d/%d passed" % (name.upper(), passed, len(suite_results)))

    total = len(all_results)
    total_passed = sum(1 for r in all_results if r["passed"])
    gate = evaluate_gate(all_results)
    print("\nPhase 8 Eval: %d/%d passed" % (total_passed, total))
    print("GATE passed=%s" % gate)
    return all_results, gate


if __name__ == "__main__":
    # DEPRECATION: historical Phase 8 compatibility evaluator. New features
    # must be represented in harness/cases and gated by run_harness_gate.py.
    sys.stderr.write(
        "[deprecation] run_phase8_eval is legacy-compatible; "
        "prefer scripts/run_harness_gate.py for new work.\n")
    suite = sys.argv[1] if len(sys.argv) > 1 else "all"
    results, gate = run_eval(suite)

    if not gate:
        sys.exit(1)
