# -*- coding: utf-8 -*-
"""R16: execution parity between grain rewrite SQL and direct SQL.

These tests use an in-memory SQLite database to prove that selected rewrite
paths keep the same numeric semantics as the direct join/group-by query.
Python 2.7 compatible. No f-strings, no dataclasses.
"""
from __future__ import unicode_literals

import os
import sqlite3
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, "src"))
TEST_ROOT = os.path.abspath(os.path.dirname(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if TEST_ROOT not in sys.path:
    sys.path.insert(0, TEST_ROOT)

from metric_sql_compiler import compile_metric_sql
from test_grain_safety_r12 import CATALOG


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
CREATE TABLE fct_orders (
  order_id INTEGER PRIMARY KEY,
  sell_through REAL,
  channel TEXT,
  store_id INTEGER,
  product_id INTEGER,
  paid_at TEXT
);
CREATE TABLE dim_store (
  store_id INTEGER PRIMARY KEY,
  region TEXT
);
CREATE TABLE dim_product (
  product_id INTEGER PRIMARY KEY,
  category TEXT,
  unit_price REAL
);
INSERT INTO dim_store VALUES (1, 'east');
INSERT INTO dim_store VALUES (2, 'west');
INSERT INTO dim_product VALUES (10, 'apparel', 20.0);
INSERT INTO dim_product VALUES (20, 'beauty', 30.0);
INSERT INTO fct_orders VALUES (101, 100.0, 'taobao', 1, 10, '2026-07-01');
INSERT INTO fct_orders VALUES (102, 150.0, 'jd', 1, 20, '2026-07-01');
INSERT INTO fct_orders VALUES (103, 200.0, 'taobao', 2, 10, '2026-07-02');
INSERT INTO fct_orders VALUES (104, 50.0, 'jd', 2, 20, '2026-07-02');
""")
    return conn


def _rows(conn, sql):
    rows = []
    for row in conn.execute(sql).fetchall():
        item = {}
        for key in row.keys():
            value = row[key]
            if isinstance(value, float):
                value = round(value, 6)
            item[str(key)] = value
        rows.append(item)
    return sorted(rows, key=lambda item: repr(sorted(item.items())))


def _assert_same(sql_a, sql_b):
    conn = _conn()
    try:
        assert _rows(conn, sql_a) == _rows(conn, sql_b)
    finally:
        conn.close()


def test_single_dimension_rewrite_matches_direct_sql():
    compiled = compile_metric_sql(
        {"metric": "gmv", "model": "order_detail", "dimensions": ["region"]},
        CATALOG)
    direct = """
SELECT dim_store.region AS region, SUM(fct_orders.sell_through) AS gmv
FROM fct_orders
JOIN dim_store ON fct_orders.store_id = dim_store.store_id
GROUP BY dim_store.region
ORDER BY gmv DESC
LIMIT 1000
"""
    assert compiled.ok is True
    assert compiled.metadata["grain_rewrite"]["selected"] is True
    _assert_same(compiled.sql, direct)


def test_multi_dimension_rewrite_matches_direct_sql():
    compiled = compile_metric_sql(
        {"metric": "gmv", "model": "order_detail", "dimensions": ["region", "category"]},
        CATALOG)
    direct = """
SELECT dim_store.region AS region, dim_product.category AS category,
       SUM(fct_orders.sell_through) AS gmv
FROM fct_orders
JOIN dim_store ON fct_orders.store_id = dim_store.store_id
JOIN dim_product ON fct_orders.product_id = dim_product.product_id
GROUP BY dim_store.region, dim_product.category
ORDER BY gmv DESC
LIMIT 1000
"""
    assert compiled.ok is True
    assert compiled.metadata["grain_rewrite"]["join_count"] == 2
    _assert_same(compiled.sql, direct)


def test_dimension_filter_semijoin_rewrite_matches_direct_sql():
    compiled = compile_metric_sql(
        {"metric": "gmv", "model": "order_detail", "dimensions": ["region"],
         "filters": [{"field": "dim_store.region", "op": "=", "value": "east"}]},
        CATALOG)
    direct = """
SELECT dim_store.region AS region, SUM(fct_orders.sell_through) AS gmv
FROM fct_orders
JOIN dim_store ON fct_orders.store_id = dim_store.store_id
WHERE dim_store.region = 'east'
GROUP BY dim_store.region
ORDER BY gmv DESC
LIMIT 1000
"""
    assert compiled.ok is True
    assert compiled.metadata["grain_rewrite"]["semijoin_pushdowns"]
    _assert_same(compiled.sql, direct)


def test_empty_semijoin_rewrite_matches_direct_sql():
    compiled = compile_metric_sql(
        {"metric": "gmv", "model": "order_detail", "dimensions": ["region"],
         "filters": [{"field": "dim_store.region", "op": "=", "value": "north"}]},
        CATALOG)
    direct = """
SELECT dim_store.region AS region, SUM(fct_orders.sell_through) AS gmv
FROM fct_orders
JOIN dim_store ON fct_orders.store_id = dim_store.store_id
WHERE dim_store.region = 'north'
GROUP BY dim_store.region
ORDER BY gmv DESC
LIMIT 1000
"""
    _assert_same(compiled.sql, direct)


def test_ratio_metric_stays_direct_path():
    compiled = compile_metric_sql(
        {"metric": "aov", "model": "order_detail", "dimensions": ["channel"]},
        CATALOG)
    assert compiled.ok is True
    assert compiled.metadata["grain_rewrite"]["selected"] is False
    assert compiled.metadata["grain_rewrite"]["reason"] in (
        "no_join_to_rewrite", "metric_not_declared_additive")


if __name__ == "__main__":
    test_single_dimension_rewrite_matches_direct_sql()
    test_multi_dimension_rewrite_matches_direct_sql()
    test_dimension_filter_semijoin_rewrite_matches_direct_sql()
    test_empty_semijoin_rewrite_matches_direct_sql()
    test_ratio_metric_stays_direct_path()
    print("All grain execution parity R16 tests passed!")
