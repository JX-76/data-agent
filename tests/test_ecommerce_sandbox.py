# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'src')
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from ecommerce_sandbox import EcommerceSandboxTools
from sandbox_data_factory import build_ecommerce_sandbox_connection, sandbox_manifest


def test_ecommerce_sandbox_has_seven_diagnostic_datasets_and_thirty_days():
    conn = build_ecommerce_sandbox_connection()
    expected = ['store_daily', 'channel_daily', 'product_daily', 'ad_daily',
                'reviews', 'service_daily', 'competitor_daily']
    names = set(x[0] for x in conn.execute("SELECT name FROM sqlite_master WHERE type='table'"))
    assert set(expected).issubset(names)
    assert conn.execute('SELECT COUNT(*) FROM store_daily').fetchone()[0] == 30
    assert conn.execute('SELECT COUNT(*) FROM product_daily').fetchone()[0] == 210
    assert conn.execute('SELECT COUNT(*) FROM ad_daily').fetchone()[0] == 120
    assert conn.execute('SELECT COUNT(*) FROM reviews').fetchone()[0] >= 100
    assert sandbox_manifest()['ecommerce_diagnostic_period'] == '2024-06-01..2024-06-30'


def test_paid_channel_and_ad_audience_collapse_on_june_25():
    conn = build_ecommerce_sandbox_connection()
    before = conn.execute("SELECT cvr FROM channel_daily WHERE date='2024-06-24' AND channel='直通车'").fetchone()[0]
    after = conn.execute("SELECT cvr FROM channel_daily WHERE date='2024-06-25' AND channel='直通车'").fetchone()[0]
    broad = conn.execute("SELECT roi FROM ad_daily WHERE date='2024-06-25' AND keyword='低价 清仓'").fetchone()[0]
    assert before == 0.035
    assert after == 0.008
    assert broad == 0.5


def test_linked_review_product_service_and_competitor_evidence_is_queryable():
    tools = EcommerceSandboxTools()
    reviews = tools.get_reviews('2024-06-24', '2024-06-25', 'PROD-001')['rows']
    product = tools.get_product_detail('2024-06-25', '2024-06-25', 'PROD-001')['rows'][0]
    service = tools.get_service_data('2024-06-25', '2024-06-25')['rows'][0]
    market = tools.get_competitor_insight('2024-06-25', '2024-06-25')['rows'][0]
    assert any('跑绒' in row['content'] and row['helpful_votes'] == 999 for row in reviews)
    assert product['dwell_seconds'] == 20.0 and product['bounce_rate'] == 0.7
    assert service['first_response_seconds'] == 180.0
    assert market['competitor_name'] == '竞品X' and market['price'] == 799.0


def test_stockout_and_preheat_are_separate_non_confounded_cases():
    tools = EcommerceSandboxTools()
    stockout = tools.get_product_detail('2024-06-10', '2024-06-12', 'PROD-002')['rows']
    preheat = tools.get_product_detail('2024-06-15', '2024-06-15', 'PROD-003')['rows'][0]
    assert all(row['stock_units'] == 0 for row in stockout)
    baseline = tools.get_product_detail('2024-06-14', '2024-06-14', 'PROD-003')['rows'][0]
    assert preheat['cart_adds'] > baseline['cart_adds'] * 1.8
    assert preheat['cvr'] < baseline['cvr']
