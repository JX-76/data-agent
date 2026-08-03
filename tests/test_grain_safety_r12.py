# -*- coding: utf-8 -*-
"""R12: grain-aware safe aggregate rewrite tests.

Python 2.7 compatible. No f-strings, no dataclasses.
"""
from __future__ import unicode_literals

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, "src"))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from grain_safety import plan_preaggregate_rewrite
from metric_sql_compiler import compile_metric_sql
from schema_introspection import normalize_schema


# ── Shared fixtures ──────────────────────────────────────────────────────────

CATALOG = {
    "fingerprint": "r12-test",
    "models": {"order_detail": {"base_table": "fct_orders"}},
    "metrics": {
        "gmv": {
            "field": "fct_orders.sell_through",
            "aggregation": "SUM",
            "aggregation_type": "additive",
            "allowed_dimensions": ["channel", "region", "category"],
        },
        "aov": {
            "expression": "SUM(fct_orders.sell_through) / NULLIF(COUNT(DISTINCT fct_orders.order_id), 0)",
            "aggregation_type": "ratio",
            "allowed_dimensions": ["channel"],
        },
        "avg_price": {
            "expression": "AVG(dim_product.unit_price)",
            "aggregation_type": "non_additive",
            "allowed_dimensions": ["category"],
        },
    },
    "dimensions": {
        "channel": {"field": "fct_orders.channel"},
        "region": {"field": "dim_store.region"},
        "category": {"field": "dim_product.category"},
    },
    "tables": {
        "fct_orders": {
            "columns": ["order_id", "sell_through", "channel", "store_id", "product_id", "paid_at"],
            "primary_key": ["order_id"],
            "table_role": "fact",
            "grain": ["order_id"],
        },
        "dim_store": {
            "columns": ["store_id", "region"],
            "primary_key": ["store_id"],
            "table_role": "dimension",
            "grain": ["store_id"],
        },
        "dim_product": {
            "columns": ["product_id", "category", "unit_price"],
            "primary_key": ["product_id"],
            "table_role": "dimension",
            "grain": ["product_id"],
        },
    },
    "joins": {
        "orders_to_store": {
            "id": "orders_to_store",
            "left_table": "fct_orders",
            "right_table": "dim_store",
            "condition": "fct_orders.store_id = dim_store.store_id",
            "cardinality": "many_to_one",
        },
        "orders_to_product": {
            "id": "orders_to_product",
            "left_table": "fct_orders",
            "right_table": "dim_product",
            "condition": "fct_orders.product_id = dim_product.product_id",
            "cardinality": "many_to_one",
        },
    },
}

_BASE_JOIN_PLAN = {
    "ok": True,
    "base_table": "fct_orders",
    "joins": [CATALOG["joins"]["orders_to_store"]],
}


# ── grain_safety unit tests ──────────────────────────────────────────────────

def test_grain_rewrite_selected_for_additive_metric_with_dim_join():
    metric_def = CATALOG["metrics"]["gmv"]
    dimension_fields = ["dim_store.region", "fct_orders.channel"]
    result = plan_preaggregate_rewrite(
        CATALOG, "fct_orders", "gmv", metric_def,
        dimension_fields, _BASE_JOIN_PLAN)
    assert result["contract"] == "grain_aggregate_rewrite_v1"
    assert result["selected"] is True
    assert result["strategy"] == "pre_aggregate"
    assert result["grain"] == ["order_id"]
    assert result["aggregation"] == "SUM"
    assert "fct_orders.store_id" in result["base_group_fields"]


def test_grain_rewrite_not_selected_when_metric_not_additive():
    ratio_def = CATALOG["metrics"]["aov"]
    result = plan_preaggregate_rewrite(
        CATALOG, "fct_orders", "aov", ratio_def,
        ["dim_store.region"], _BASE_JOIN_PLAN)
    assert result["selected"] is False
    assert result["reason"] == "metric_not_declared_additive"


def test_grain_rewrite_not_selected_when_no_fact_grain():
    metric_def = dict(CATALOG["metrics"]["gmv"])
    tables_no_grain = dict(CATALOG["tables"])
    tables_no_grain["fct_orders"] = dict(tables_no_grain["fct_orders"])
    del tables_no_grain["fct_orders"]["grain"]
    catalog_no_grain = dict(CATALOG, tables=tables_no_grain)
    result = plan_preaggregate_rewrite(
        catalog_no_grain, "fct_orders", "gmv", metric_def,
        ["dim_store.region"], _BASE_JOIN_PLAN)
    assert result["selected"] is False
    assert result["reason"] == "missing_fact_grain"


def test_grain_rewrite_not_selected_when_base_not_fact():
    metric_def = dict(CATALOG["metrics"]["gmv"])
    tables_not_fact = dict(CATALOG["tables"])
    tables_not_fact["fct_orders"] = dict(tables_not_fact["fct_orders"])
    tables_not_fact["fct_orders"] = dict(tables_not_fact["fct_orders"], table_role="view")
    catalog_not_fact = dict(CATALOG, tables=tables_not_fact)
    result = plan_preaggregate_rewrite(
        catalog_not_fact, "fct_orders", "gmv", metric_def,
        ["dim_store.region"], _BASE_JOIN_PLAN)
    assert result["selected"] is False
    assert result["reason"] == "base_table_not_declared_fact"


def test_grain_rewrite_selects_safe_dimension_filter_semijoin():
    metric_def = CATALOG["metrics"]["gmv"]
    result = plan_preaggregate_rewrite(
        CATALOG, "fct_orders", "gmv", metric_def,
        ["dim_store.region"], _BASE_JOIN_PLAN,
        filter_fields=["dim_store.region"],
        filter_specs=[{"field": "dim_store.region", "op": "=", "value": "east"}])
    assert result["selected"] is True
    assert len(result["semijoin_pushdowns"]) == 1
    pushdown = result["semijoin_pushdowns"][0]
    assert pushdown["base_key"] == "store_id"
    assert pushdown["dimension_key"] == "store_id"
    assert pushdown["filter_field"] == "dim_store.region"


def test_grain_rewrite_keeps_dimension_filter_direct_without_filter_contract():
    metric_def = CATALOG["metrics"]["gmv"]
    result = plan_preaggregate_rewrite(
        CATALOG, "fct_orders", "gmv", metric_def,
        ["dim_store.region"], _BASE_JOIN_PLAN,
        filter_fields=["dim_store.region"])
    assert result["selected"] is False
    assert result["reason"] == "filter_not_sourced_from_base_fact"


def test_grain_rewrite_selected_for_multiple_safe_dimension_joins():
    multi_join = {
        "ok": True,
        "base_table": "fct_orders",
        "joins": [CATALOG["joins"]["orders_to_store"], CATALOG["joins"]["orders_to_product"]],
    }
    metric_def = CATALOG["metrics"]["gmv"]
    result = plan_preaggregate_rewrite(
        CATALOG, "fct_orders", "gmv", metric_def,
        ["dim_store.region", "dim_product.category"], multi_join)
    assert result["selected"] is True
    assert result["reason"] == "declared_additive_fact_preaggregate"
    assert result["join_count"] == 2
    assert "fct_orders.store_id" in result["base_group_fields"]
    assert "fct_orders.product_id" in result["base_group_fields"]
    assert len(result["outer_join_keys"]) == 2


def test_grain_rewrite_not_selected_when_all_dims_on_fact():
    metric_def = CATALOG["metrics"]["gmv"]
    result = plan_preaggregate_rewrite(
        CATALOG, "fct_orders", "gmv", metric_def,
        ["fct_orders.channel"], _BASE_JOIN_PLAN)
    assert result["selected"] is False
    assert result["reason"] == "no_joined_dimension_to_rewrite"


# ── compile_metric_sql integration: rewrite selected path ─────────────────

def test_compiler_emits_cte_rewrite_for_additive_with_dim_join():
    result = compile_metric_sql(
        {"metric": "gmv", "model": "order_detail", "dimensions": ["region"]},
        CATALOG)
    assert result.ok is True
    assert "WITH _fact_agg AS" in result.sql
    assert "FROM _fact_agg" in result.sql
    assert result.metadata["grain_rewrite"]["selected"] is True


def test_compiler_emits_cte_rewrite_with_dimension_filter_semijoin():
    result = compile_metric_sql(
        {"metric": "gmv", "model": "order_detail", "dimensions": ["region"],
         "filters": [{"field": "dim_store.region", "op": "=", "value": "east"}]},
        CATALOG)
    assert result.ok is True
    assert "WITH _fact_agg AS" in result.sql
    assert "IN (SELECT \"dim_store\".\"store_id\"" in result.sql
    assert result.metadata["grain_rewrite"]["selected"] is True
    assert len(result.metadata["grain_rewrite"]["semijoin_pushdowns"]) == 1


def test_compiler_emits_cte_rewrite_for_multiple_safe_dim_joins():
    result = compile_metric_sql(
        {"metric": "gmv", "model": "order_detail", "dimensions": ["region", "category"]},
        CATALOG)
    assert result.ok is True
    assert "WITH _fact_agg AS" in result.sql
    assert "JOIN \"dim_store\"" in result.sql
    assert "JOIN \"dim_product\"" in result.sql
    assert result.metadata["grain_rewrite"]["selected"] is True
    assert result.metadata["grain_rewrite"]["join_count"] == 2


def test_compiler_falls_back_direct_for_fact_only_dim():
    result = compile_metric_sql(
        {"metric": "gmv", "model": "order_detail", "dimensions": ["channel"]},
        CATALOG)
    assert result.ok is True
    assert "WITH _fact_agg" not in result.sql
    assert result.metadata["grain_rewrite"]["selected"] is False


def test_compiler_falls_back_direct_for_non_additive():
    result = compile_metric_sql(
        {"metric": "aov", "model": "order_detail", "dimensions": ["channel"]},
        CATALOG)
    assert result.ok is True
    assert "WITH _fact_agg" not in result.sql
    assert result.metadata["grain_rewrite"]["selected"] is False


# ── schema_introspection: grain/table_role passthrough ─────────────────────

def test_schema_introspection_propagates_grain_and_role():
    schema = normalize_schema({
        "fct_orders": {
            "columns": ["order_id", "sell_through"],
            "primary_key": ["order_id"],
            "table_role": "fact",
            "grain": ["order_id"],
        },
        "dim_store": {
            "columns": ["store_id", "region"],
            "primary_key": "store_id",  # single string form
            "table_role": "dimension",
            "grain": "store_id",  # single string form
        },
    })
    tables = schema["tables"]
    assert tables["fct_orders"]["table_role"] == "fact"
    assert tables["fct_orders"]["grain"] == ["order_id"]
    assert tables["dim_store"]["table_role"] == "dimension"
    assert tables["dim_store"]["grain"] == ["store_id"]
    assert tables["dim_store"]["primary_key"] == ["store_id"]


# ── existing R8/R9 regression guard ─────────────────────────────────────────

def test_existing_r8_r9_direct_path_still_passes():
    """Existing tests must still pass via the direct (non-rewrite) path."""
    result = compile_metric_sql(
        {"model": "order_detail", "metric": "gmv", "dimensions": ["channel"]},
        CATALOG)
    assert result.ok is True
    assert "GROUP BY" in result.sql
    assert result.metadata["contract"] == "compiled_sql_v1"


if __name__ == "__main__":
    test_grain_rewrite_selected_for_additive_metric_with_dim_join()
    test_grain_rewrite_not_selected_when_metric_not_additive()
    test_grain_rewrite_not_selected_when_no_fact_grain()
    test_grain_rewrite_not_selected_when_base_not_fact()
    test_grain_rewrite_selects_safe_dimension_filter_semijoin()
    test_grain_rewrite_keeps_dimension_filter_direct_without_filter_contract()
    test_grain_rewrite_selected_for_multiple_safe_dimension_joins()
    test_grain_rewrite_not_selected_when_all_dims_on_fact()
    test_compiler_emits_cte_rewrite_for_additive_with_dim_join()
    test_compiler_emits_cte_rewrite_with_dimension_filter_semijoin()
    test_compiler_emits_cte_rewrite_for_multiple_safe_dim_joins()
    test_compiler_falls_back_direct_for_fact_only_dim()
    test_compiler_falls_back_direct_for_non_additive()
    test_schema_introspection_propagates_grain_and_role()
    test_existing_r8_r9_direct_path_still_passes()
    print("All grain_safety R12 tests passed!")
