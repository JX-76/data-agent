# -*- coding: utf-8 -*-
"""Tests for execution validation and retry behavior."""

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, "src"))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class _FakeRuntime(object):
    def __init__(self, sql="SELECT 1"):
        self.sql = sql
        self.trace = []
        self.datasets = {}

    def switch(self, model):
        self.trace.append({"op": "switch", "model": model})
        self.datasets["d1"] = {}
        return "d1"

    def filter_time_and_defaults(self, dataid, metric, start, end):
        self.trace.append({"op": "filter_time", "metric": metric})
        return dataid

    def aggregate(self, dataid, metric, dimensions):
        self.trace.append({"op": "aggregate", "metric": metric, "dimensions": dimensions})
        return dataid

    def sort(self, dataid, by, order="DESC"):
        self.trace.append({"op": "sort", "by": by, "order": order})
        return dataid

    def _register(self, model, sql, columns, parent=None, op="tool", sample_rows=None, parents=None):
        dataid = "d%s" % (len(self.datasets) + 1)
        self.datasets[dataid] = {"model": model, "sql": sql, "columns": columns, "parent": parent, "parents": parents}
        self.trace.append({"op": op, "dataid": dataid, "columns": columns})
        return dataid

    def compile_sql(self, dataid):
        return self.sql


class _FlakyExecutor(object):
    def __init__(self):
        self.calls = 0

    def execute(self, sql):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("temporary db error")
        return {"rows": [{"gmv": 100}], "row_count": 1, "source": "fake", "sql": sql}


class _EmptyExecutor(object):
    def execute(self, sql):
        return {"rows": [], "row_count": 0, "source": "fake", "sql": sql}


class _AlwaysFailExecutor(object):
    def __init__(self):
        self.calls = 0

    def execute(self, sql):
        self.calls += 1
        raise RuntimeError("db unavailable")


def test_execution_envelope_contract_builder():
    from contracts import build_execution_envelope

    envelope = build_execution_envelope(
        status="ok", stage="db_execute", query_id="q1", evidence_id="e1",
        dataid="d1", data_version="v1", row_count=3, time_range="last_7_days")

    assert envelope["status"] == "ok"
    assert envelope["stage"] == "db_execute"
    assert envelope["authority"] == "verified_execution"
    assert envelope["evidence_id"] == "e1"
    assert envelope["row_count"] == 3


def test_execution_engine_retries_and_records_diagnostics():
    from execution_engine import ExecutionEngine

    executor = _FlakyExecutor()
    engine = ExecutionEngine(
        executor=executor,
        runtime_factory=lambda: _FakeRuntime("SELECT 1"),
        validator=lambda sql: (True, None),
        max_retries=1,
    )
    result = engine.execute({"model": "order_detail", "metric": "gmv"}, task_id="t1")

    assert result["status"] == "ok"
    assert executor.calls == 2
    assert result["diagnostics"]["retry_count"] == 1
    # After R8, descriptive plans may be compiled via metric_sql_compiler (strategy="metric_sql_compiler")
    # or fall back to the legacy strategy (strategy="descriptive"). Both are correct.
    assert result["diagnostics"]["strategy"] in ("descriptive", "metric_sql_compiler")
    assert result["results_summary"]["row_count"] == 1
    assert result["execution_envelope"]["status"] == "ok"
    assert result["execution_envelope"]["authority"] == "verified_execution"
    assert result["execution_envelope"]["evidence_id"] in result["evidence_refs"]
    assert result["query_id"] == result["execution_envelope"]["query_id"]


def test_execution_engine_validation_error_does_not_execute():
    from execution_engine import ExecutionEngine

    executor = _FlakyExecutor()
    engine = ExecutionEngine(
        executor=executor,
        runtime_factory=lambda: _FakeRuntime("DELETE FROM orders"),
        validator=lambda sql: (False, "dangerous sql"),
        max_retries=1,
    )
    # Attribution uses the legacy strategy path, so the injected validator must
    # still prevent execution even while descriptive plans use the R8 compiler.
    result = engine.execute({"model": "order_detail", "metric": "gmv", "task_type": "attribution"})

    assert result["status"] == "error"
    assert result["errors"][0]["phase"] == "validate_sql"
    assert result["diagnostics"]["retry_count"] == 0
    assert executor.calls == 0


def test_execution_engine_empty_result_quality_diagnostics():
    from execution_engine import ExecutionEngine

    engine = ExecutionEngine(
        executor=_EmptyExecutor(),
        runtime_factory=lambda: _FakeRuntime("SELECT 1"),
        validator=lambda sql: (True, None),
        max_retries=0,
    )
    result = engine.execute({"model": "order_detail", "metric": "gmv"})

    assert result["status"] == "ok"
    assert result["results"] == []
    assert result["diagnostics"]["quality"]["empty_result"] is True
    assert result["diagnostics"]["failure_type"] == "empty_result"


def test_execution_engine_retry_exhausted_diagnostics():
    from execution_engine import ExecutionEngine

    executor = _AlwaysFailExecutor()
    engine = ExecutionEngine(
        executor=executor,
        runtime_factory=lambda: _FakeRuntime("SELECT 1"),
        validator=lambda sql: (True, None),
        max_retries=1,
    )
    result = engine.execute({"model": "order_detail", "metric": "gmv"})

    assert result["status"] == "error"
    assert executor.calls == 2
    assert result["diagnostics"]["phase"] == "execute"
    assert result["diagnostics"]["failure_type"] == "retry_exhausted"
    assert result["diagnostics"]["retry_exhausted"] is True
    assert result["execution_envelope"]["status"] == "error"
    assert result["execution_envelope"]["authority"] == "unverified"
    assert result["execution_envelope"]["evidence_id"] is None
    assert result["execution_envelope"]["error_code"] == "retry_exhausted"


def test_execution_engine_comparison_strategy_metadata():
    from execution_engine import ExecutionEngine

    engine = ExecutionEngine(
        executor=_EmptyExecutor(),
        runtime_factory=lambda: _FakeRuntime("SELECT delta FROM comparison"),
        validator=lambda sql: (True, None),
        max_retries=0,
    )
    result = engine.execute({
        "model": "order_detail",
        "metric": "gmv",
        "task_type": "comparison",
        "dimensions": ["channel"],
        "previous_time_range": ("2026-07-01", "2026-07-02"),
    })

    assert result["status"] == "ok"
    assert result["task_type"] == "comparison"
    # R8 compiles comparison SQL through the shared metric compiler; task_type
    # remains comparison and the execution contract is otherwise unchanged.
    assert result["diagnostics"]["strategy"] == "metric_sql_compiler"
    assert result["diagnostics"]["strategy_metadata"]["compiled_sql"]["contract"] == "compiled_sql_v1"


def test_execution_engine_anomaly_and_attribution_are_supported():
    from execution_engine import ExecutionEngine

    engine = ExecutionEngine(
        executor=_EmptyExecutor(),
        runtime_factory=lambda: _FakeRuntime("SELECT 1"),
        validator=lambda sql: (True, None),
        max_retries=0,
    )
    anomaly = engine.execute({"model": "order_detail", "metric": "gmv", "task_type": "anomaly"})
    attribution = engine.execute({"model": "order_detail", "metric": "gmv", "task_type": "attribution"})

    assert anomaly["status"] == "ok"
    assert anomaly["diagnostics"]["strategy"] == "anomaly"
    assert attribution["status"] == "ok"
    assert attribution["diagnostics"]["strategy"] == "attribution"


def test_execution_engine_unsupported_task_type_returns_stable_error():
    from execution_engine import ExecutionEngine

    executor = _FlakyExecutor()
    engine = ExecutionEngine(
        executor=executor,
        runtime_factory=lambda: _FakeRuntime("SELECT 1"),
        validator=lambda sql: (True, None),
        max_retries=1,
    )
    result = engine.execute({"model": "order_detail", "metric": "gmv", "task_type": "funnel"})

    assert result["status"] == "error"
    assert result["errors"][0]["phase"] == "strategy_dispatch"
    assert result["diagnostics"]["failure_type"] == "unsupported_task_type"
    assert result["diagnostics"]["strategy"] == "funnel"
    assert executor.calls == 0


def test_empty_result_quality_reaches_insight_bundle():
    from result_explainer import build_insight_bundle

    insight = build_insight_bundle(
        {"status": "ok", "metric": "gmv"},
        {
            "status": "ok",
            "diagnostics": {
                "quality": {
                    "empty_result": True,
                    "messages": ["查询结果为空"],
                }
            },
        },
    ).to_dict()

    joined_caveats = b" ".join(insight["caveats"]) if isinstance(insight["caveats"][0], bytes) else u" ".join(insight["caveats"])
    joined_next = b" ".join(insight["next_steps"]) if isinstance(insight["next_steps"][0], bytes) else u" ".join(insight["next_steps"])
    search_caveat = u"本次查询结果为空"
    search_next = u"放宽时间范围"
    if isinstance(joined_caveats, bytes):
        search_caveat = search_caveat.encode("utf-8")
    if isinstance(joined_next, bytes):
        search_next = search_next.encode("utf-8")
    assert search_caveat in joined_caveats
    assert search_next in joined_next


def test_facade_execution_diagnostics_present():
    from agent_facade import AgentFacade
    facade = AgentFacade(session_id="retry-test")
    result = facade.ask("最近7天GMV")
    assert "diagnostics" in result
    assert "retry_count" in result["diagnostics"]
    assert result["diagnostics"]["retry_count"] >= 0


def test_validate_sql_blocks_dangerous():
    from runtime_core import validate_sql
    ok, reason = validate_sql("DELETE FROM fct_orders")
    assert ok is False
    assert reason


if __name__ == "__main__":
    test_execution_engine_retries_and_records_diagnostics()
    test_execution_engine_validation_error_does_not_execute()
    test_execution_engine_empty_result_quality_diagnostics()
    test_execution_engine_retry_exhausted_diagnostics()
    test_execution_engine_comparison_strategy_metadata()
    test_execution_engine_anomaly_and_attribution_are_supported()
    test_execution_engine_unsupported_task_type_returns_stable_error()
    test_empty_result_quality_reaches_insight_bundle()
    test_facade_execution_diagnostics_present()
    test_validate_sql_blocks_dangerous()
    print("All execution retry tests passed!")
