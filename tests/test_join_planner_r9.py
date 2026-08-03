# -*- coding: utf-8 -*-
"""R9 join planning and multi-table metric compiler checks."""
from __future__ import unicode_literals

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from join_planner import plan_joins, referenced_tables
from metric_sql_compiler import compile_metric_sql


CATALOG = {
    "fingerprint": "r9-test",
    "models": {"product_analysis": {"base_table": "fct_orders"}},
    "metrics": {
        "gmv": {"expression": "SUM(fct_orders.sell_through)",
                "allowed_dimensions": ["category"]},
        "avg_price": {"expression": "AVG(dim_product.unit_price)",
                      "allowed_dimensions": ["category"]},
    },
    "dimensions": {
        "category": {"field": "dim_product.category"},
    },
    "tables": {
        "fct_orders": {"columns": ["product_id", "sell_through", "paid_at"]},
        "dim_product": {"columns": ["product_id", "category", "unit_price"]},
    },
    "joins": {
        "orders_to_product": {
            "id": "orders_to_product", "left_table": "fct_orders",
            "right_table": "dim_product",
            "condition": "fct_orders.product_id = dim_product.product_id",
            "cardinality": "many_to_one",
        },
    },
}


def test_referenced_tables():
    assert referenced_tables("SUM(fct_orders.gmv)", "DATE(dim_product.created_at)") == ["fct_orders", "dim_product"]


def test_join_plan_uses_declared_edge():
    result = plan_joins(CATALOG, "fct_orders", ["dim_product"])
    assert result["ok"] is True
    assert result["joins"][0]["id"] == "orders_to_product"


def test_compiler_renders_join_for_dimension():
    result = compile_metric_sql({"metric": "gmv", "model": "product_analysis", "dimensions": ["category"]}, CATALOG)
    assert result.ok is True
    assert 'JOIN "dim_product" ON fct_orders.product_id = dim_product.product_id' in result.sql
    assert result.metadata["joins"] == ["orders_to_product"]


def test_compiler_renders_join_for_metric_expression():
    result = compile_metric_sql({"metric": "avg_price", "model": "product_analysis"}, CATALOG)
    assert result.ok is True
    assert 'JOIN "dim_product" ON fct_orders.product_id = dim_product.product_id' in result.sql


def test_compiler_rejects_fanout_for_additive_metric():
    risky = dict(CATALOG)
    risky["joins"] = dict(CATALOG["joins"])
    risky["joins"]["orders_to_product"] = dict(CATALOG["joins"]["orders_to_product"])
    risky["joins"]["orders_to_product"]["cardinality"] = "one_to_many"
    result = compile_metric_sql({"metric": "gmv", "model": "product_analysis", "dimensions": ["category"]}, risky)
    assert result.ok is False
    assert "fanout risk on join orders_to_product: one_to_many" in result.errors
    assert result.metadata["fanout_safety"]["contract"] == "fanout_safety_v1"


def test_compiler_allows_explicit_fanout_safe_metric():
    safe = dict(CATALOG)
    safe["metrics"] = dict(CATALOG["metrics"])
    safe["metrics"]["gmv"] = dict(CATALOG["metrics"]["gmv"])
    safe["metrics"]["gmv"]["fanout_safe"] = True
    safe["joins"] = dict(CATALOG["joins"])
    safe["joins"]["orders_to_product"] = dict(CATALOG["joins"]["orders_to_product"])
    safe["joins"]["orders_to_product"]["cardinality"] = "one_to_many"
    result = compile_metric_sql({"metric": "gmv", "model": "product_analysis", "dimensions": ["category"]}, safe)
    assert result.ok is True
    assert result.metadata["fanout_safety"]["ok"] is True


def test_compiler_rejects_missing_declared_path():
    broken = dict(CATALOG)
    broken["joins"] = {}
    result = compile_metric_sql({"metric": "avg_price", "model": "product_analysis"}, broken)
    assert result.ok is False
    assert "no declared join path from fct_orders to dim_product" in result.errors


if __name__ == "__main__":
    test_referenced_tables()
    test_join_plan_uses_declared_edge()
    test_compiler_renders_join_for_dimension()
    test_compiler_renders_join_for_metric_expression()
    test_compiler_rejects_fanout_for_additive_metric()
    test_compiler_allows_explicit_fanout_safe_metric()
    test_compiler_rejects_missing_declared_path()
    print("All join planner R9 tests passed!")
