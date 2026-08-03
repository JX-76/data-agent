# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, "src"))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from db_adapter import MockDBAdapter, ReadonlyQueryExecutor
from execution_engine import ExecutionEngine
from metadata_catalog import build_metadata_catalog
from metric_sql_compiler import compile_metric_sql
from task_types import DESCRIPTIVE


def test_compiler_generates_metric_dictionary_sql():
    catalog = build_metadata_catalog(physical_schema={"fct_orders": ["order_id", "paid_at", "sell_through", "channel", "order_status"]})
    result = compile_metric_sql({"task_type": DESCRIPTIVE, "model": "order_detail", "metric": "gmv", "dimensions": ["channel"]}, catalog)
    data = result.to_dict()
    assert data["contract"] == "compiled_sql_v1"
    assert data["ok"] is True
    assert "SUM(fct_orders.sell_through)" in data["sql"]
    assert "GROUP BY" in data["sql"]
    assert data["fingerprint"]


def test_compiler_rejects_unknown_dimension():
    catalog = build_metadata_catalog()
    result = compile_metric_sql({"metric": "gmv", "dimensions": ["not_a_dim"]}, catalog)
    assert result.ok is False
    assert any("unknown dimension" in e for e in result.errors)


def test_execution_engine_uses_metric_sql_compiler():
    queries = []
    db = MockDBAdapter(schema={"fct_orders": ["order_id", "paid_at", "sell_through", "channel", "order_status"]}, query_log=queries)
    engine = ExecutionEngine(executor=ReadonlyQueryExecutor(db=db), max_retries=0)
    out = engine.execute({"task_type": DESCRIPTIVE, "model": "order_detail", "metric": "gmv", "dimensions": ["channel"]})
    assert out["status"] == "ok"
    assert out["diagnostics"]["strategy"] == "metric_sql_compiler"
    assert out["diagnostics"]["strategy_metadata"]["compiled_sql"]["contract"] == "compiled_sql_v1"
    assert queries and "FROM \"fct_orders\"" in queries[0]


if __name__ == "__main__":
    test_compiler_generates_metric_dictionary_sql()
    test_compiler_rejects_unknown_dimension()
    test_execution_engine_uses_metric_sql_compiler()
    print("All metric_sql_compiler R8 tests passed!")
