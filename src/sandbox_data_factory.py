# -*- coding: utf-8 -*-
"""Deterministic, non-sensitive ecommerce sandbox data for integration tests."""
from __future__ import unicode_literals

import sqlite3


SANDBOX_SCHEMA_VERSION = 'sandbox-ecommerce-v1'


def build_sandbox_connection(seed=17):
    """Return an in-memory SQLite database with deterministic representative data."""
    # seed is intentionally part of the public contract; current fixture is stable.
    del seed
    conn = sqlite3.connect(':memory:')
    cur = conn.cursor()
    cur.executescript('''
        CREATE TABLE orders (
          order_id TEXT PRIMARY KEY, order_date TEXT NOT NULL, user_id TEXT NOT NULL,
          channel TEXT NOT NULL, region TEXT NOT NULL, gmv REAL NOT NULL,
          refund_amount REAL NOT NULL DEFAULT 0, status TEXT NOT NULL
        );
        -- Semantic-layer-compatible fact table used by the product demo.
        CREATE TABLE fct_orders (
          order_id TEXT PRIMARY KEY, paid_at TEXT NOT NULL, user_id TEXT NOT NULL,
          store_id TEXT NOT NULL, product_id TEXT NOT NULL,
          channel TEXT NOT NULL, region TEXT NOT NULL, category TEXT NOT NULL,
          sell_through REAL NOT NULL, ad_cost REAL NOT NULL DEFAULT 0,
          order_status TEXT NOT NULL, is_first_order INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE dim_store (
          store_id TEXT PRIMARY KEY, store_name TEXT NOT NULL, region TEXT NOT NULL
        );
        CREATE TABLE dim_product (
          product_id TEXT PRIMARY KEY, product_name TEXT NOT NULL,
          category TEXT NOT NULL, unit_price REAL NOT NULL
        );
        CREATE TABLE fct_events (
          event_id TEXT PRIMARY KEY, event_time TEXT NOT NULL, user_id TEXT NOT NULL,
          channel TEXT NOT NULL, region TEXT NOT NULL, category TEXT NOT NULL,
          click_id TEXT, impression_id TEXT
        );
        CREATE TABLE products (
          product_id TEXT PRIMARY KEY, product_name TEXT NOT NULL,
          category TEXT NOT NULL, price REAL NOT NULL
        );
        CREATE TABLE users (
          user_id TEXT PRIMARY KEY, user_name TEXT NOT NULL, region TEXT NOT NULL,
          is_vip INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE user_events (
          event_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, tenant_id TEXT NOT NULL,
          event_name TEXT NOT NULL, event_date TEXT NOT NULL
        );
    ''')
    cur.executemany('INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?, ?, ?)', [
        ('o001', '2026-07-01', 'u001', 'organic', 'east', 120.0, 0.0, 'paid'),
        ('o002', '2026-07-01', 'u002', 'ads', 'north', 240.0, 0.0, 'paid'),
        ('o003', '2026-07-02', 'u001', 'organic', 'east', 80.0, 10.0, 'refunded'),
        ('o004', '2026-07-02', 'u003', 'affiliate', 'south', 310.0, 0.0, 'paid'),
        ('o005', '2026-07-03', 'u004', 'ads', 'north', 150.0, 0.0, 'paid'),
    ])
    cur.executemany('INSERT INTO fct_orders VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', [
        ('fo001', '2026-07-22 09:15:00', 'u001', 's001', 'p001', 'organic', 'east', 'electronics', 120.0, 12.0, 'paid', 1),
        ('fo002', '2026-07-22 11:40:00', 'u002', 's002', 'p002', 'ads', 'north', 'apparel', 240.0, 80.0, 'paid', 1),
        ('fo003', '2026-07-22 14:05:00', 'u003', 's003', 'p001', 'affiliate', 'south', 'electronics', 310.0, 62.0, 'completed', 1),
        ('fo004', '2026-07-22 18:20:00', 'u004', 's002', 'p002', 'ads', 'north', 'apparel', 150.0, 45.0, 'paid', 1),
        ('fo005', '2026-07-23 08:10:00', 'u001', 's001', 'p001', 'organic', 'east', 'electronics', 180.0, 18.0, 'paid', 0),
    ])
    cur.executemany('INSERT INTO dim_store VALUES (?, ?, ?)', [
        ('s001', 'East Demo Store', 'east'),
        ('s002', 'North Demo Store', 'north'),
        ('s003', 'South Demo Store', 'south'),
    ])
    cur.executemany('INSERT INTO dim_product VALUES (?, ?, ?, ?)', [
        ('p001', 'Demo Phone', 'electronics', 699.0),
        ('p002', 'Demo Shoe', 'apparel', 89.0),
    ])
    cur.executemany('INSERT INTO fct_events VALUES (?, ?, ?, ?, ?, ?, ?, ?)', [
        ('fe001', '2026-07-22 09:00:00', 'u001', 'organic', 'east', 'electronics', 'c001', 'i001'),
        ('fe002', '2026-07-22 09:01:00', 'u002', 'organic', 'east', 'electronics', None, 'i002'),
        ('fe003', '2026-07-22 10:00:00', 'u003', 'ads', 'north', 'apparel', 'c002', 'i003'),
        ('fe004', '2026-07-22 10:01:00', 'u004', 'ads', 'north', 'apparel', None, 'i004'),
    ])
    cur.executemany('INSERT INTO user_events VALUES (?, ?, ?, ?, ?)', [
        ('e001', 'u001', 'tenant_a', 'register', '2026-07-01'), ('e002', 'u002', 'tenant_a', 'register', '2026-07-01'),
        ('e003', 'u003', 'tenant_a', 'register', '2026-07-02'), ('e004', 'u001', 'tenant_a', 'active', '2026-07-02'),
        ('e005', 'u001', 'tenant_a', 'active', '2026-07-02'), ('e006', 'u002', 'tenant_a', 'active', '2026-07-08'),
        ('e007', 'u003', 'tenant_a', 'active', '2026-07-09'), ('e008', 'u004', 'tenant_b', 'register', '2026-07-01'),
        ('e009', 'u004', 'tenant_b', 'active', '2026-07-08'), ('e010', 'u001', 'tenant_a', 'purchase', '2026-07-01'),
        ('e011', 'u002', 'tenant_a', 'purchase', '2026-07-01'), ('e012', 'u001', 'tenant_a', 'purchase', '2026-07-08'),
    ])
    cur.executemany('INSERT INTO products VALUES (?, ?, ?, ?)', [
        ('p001', 'Demo Phone', 'electronics', 699.0),
        ('p002', 'Demo Shoe', 'apparel', 89.0),
    ])
    cur.executemany('INSERT INTO users VALUES (?, ?, ?, ?)', [
        ('u001', 'sandbox_user_1', 'east', 1), ('u002', 'sandbox_user_2', 'north', 0),
        ('u003', 'sandbox_user_3', 'south', 0), ('u004', 'sandbox_user_4', 'north', 1),
    ])
    conn.commit()
    return conn

def _date(day):
    return '2024-06-%02d' % day


def build_ecommerce_sandbox_connection(seed=42):
    """Build a 30-day, linked ecommerce diagnostic sandbox.

    The values are deterministic and deliberately contain documented anomalies:
    6/10-12 SKU stockout, 6/15 high-ticket preheat carting, 6/24 negative
    review, and 6/25 paid traffic / review / service / competitor shock.
    """
    del seed
    conn = sqlite3.connect(':memory:')
    cur = conn.cursor()
    cur.executescript('''
      CREATE TABLE store_daily (
        date TEXT PRIMARY KEY, visitors INTEGER, pay_amount REAL, pay_orders INTEGER,
        cvr REAL, asp REAL, promotion_phase TEXT
      );
      CREATE TABLE channel_daily (
        date TEXT, channel TEXT, visitors INTEGER, pay_orders INTEGER, cvr REAL,
        pay_amount REAL, ad_cost REAL, PRIMARY KEY(date, channel)
      );
      CREATE TABLE product_daily (
        date TEXT, product_id TEXT, product_name TEXT, category TEXT, price REAL,
        visitors INTEGER, cart_adds INTEGER, pay_units INTEGER, pay_amount REAL,
        cvr REAL, dwell_seconds REAL, bounce_rate REAL, stock_units INTEGER,
        PRIMARY KEY(date, product_id)
      );
      CREATE TABLE ad_daily (
        date TEXT, plan_name TEXT, keyword TEXT, audience TEXT, impressions INTEGER,
        clicks INTEGER, ad_cost REAL, conversions INTEGER, conversion_amount REAL,
        roi REAL, PRIMARY KEY(date, plan_name, keyword, audience)
      );
      CREATE TABLE reviews (
        review_id TEXT PRIMARY KEY, date TEXT, product_id TEXT, content TEXT,
        rating INTEGER, has_image INTEGER, helpful_votes INTEGER, source TEXT
      );
      CREATE TABLE service_daily (
        date TEXT PRIMARY KEY, first_response_seconds REAL, average_response_seconds REAL,
        consultation_count INTEGER, consultation_cvr REAL, unresolved_negative_count INTEGER
      );
      CREATE TABLE competitor_daily (
        date TEXT, competitor_name TEXT, price REAL, sales_units INTEGER,
        traffic_share REAL, lost_amount REAL, PRIMARY KEY(date, competitor_name)
      );
      CREATE TABLE sandbox_anomalies (
        anomaly_id TEXT PRIMARY KEY, date TEXT, domain TEXT, affected_entity TEXT,
        expected_signal TEXT, severity TEXT
      );
    ''')
    products = [
      ('PROD-001', '极光羽绒服', 'apparel', 899.0, 0.012, 0.37),
      ('PROD-002', '简约T恤', 'apparel', 59.0, 0.065, 0.18),
      ('PROD-003', '专业咖啡机', 'appliance', 1999.0, 0.007, 0.16),
      ('PROD-004', '手机壳', 'accessory', 25.0, 0.075, 0.10),
      ('PROD-005', '智能台灯', 'home', 159.0, 0.032, 0.08),
      ('PROD-006', '运动水杯', 'home', 79.0, 0.048, 0.06),
      ('PROD-007', '收纳箱', 'home', 99.0, 0.038, 0.05),
    ]
    product_rows, store_rows, channel_rows, ad_rows = [], [], [], []
    review_rows, service_rows, competitor_rows = [], [], []
    for day in range(1, 31):
      date = _date(day); weekend = 0.90 if ((day - 1) % 7) in (5, 6) else 1.0
      event = 1.22 if day in (18, 19) else 1.0
      phase = 'big_promotion' if day in (18, 19) else ('decline_period' if day >= 25 else 'steady')
      total_visitors = total_orders = 0; total_amount = 0.0
      for index, (pid, name, category, price, base_cvr, share) in enumerate(products):
        visitors = int(5000 * share * weekend * event * (1 + ((day * (index + 3)) % 7 - 3) * .012))
        cvr = base_cvr; dwell = 65.0 + index * 3; bounce = .38 - index * .018; stock = 180
        if pid == 'PROD-002' and day in (10, 11, 12):
          stock = 0; cvr *= .42
        if pid == 'PROD-003' and day == 15:
          # Add-to-cart rises for preheat, but purchase waits for promotion.
          cvr *= .70
        if pid == 'PROD-001' and day >= 25:
          cvr *= .43; dwell = 20.0; bounce = .70
        units = max(1, int(visitors * cvr))
        carts = int(visitors * (.10 + index * .012) * (2.05 if pid == 'PROD-003' and day == 15 else 1.0))
        amount = round(units * price, 2)
        product_rows.append((date, pid, name, category, price, visitors, carts, units, amount,
                             round(float(units) / visitors, 4), dwell, round(bounce, 4), stock))
        total_visitors += visitors; total_orders += units; total_amount += amount
      # The overall shock is induced by product conversion, while traffic remains stable.
      store_rows.append((date, total_visitors, round(total_amount, 2), total_orders,
                         round(float(total_orders) / total_visitors, 4), round(total_amount / total_orders, 2), phase))
      channel_specs = [('手淘搜索', .50, .040), ('直通车', .30, .035), ('其他渠道', .20, .030)]
      for channel, share, base_cvr in channel_specs:
        visitors = int(total_visitors * share)
        cvr = .008 if channel == '直通车' and day >= 25 else base_cvr
        orders = max(1, int(visitors * cvr)); amount = round(orders * (total_amount / total_orders), 2)
        ad_cost = round(amount / (3.0 if channel == '直通车' and day < 25 else .5), 2) if channel == '直通车' else 0.0
        channel_rows.append((date, channel, visitors, orders, round(cvr, 4), amount, ad_cost))
      # Four granular ad records/day: precise vs broad plans and audiences.
      for plan, keyword, audience, share, normal_roi in [
        ('核心转化', '羽绒服 保暖', '高购买意向人群', .42, 3.2),
        ('核心转化', '咖啡机 家用', '相似店铺人群', .18, 3.2),
        ('智能测款', '低价 清仓', '相似店铺人群', .20, 1.8),
        ('智能测款', '夏季T恤', '高购买意向人群', .20, 1.8)]:
        if day >= 25 and plan == '核心转化': share *= .50
        if day >= 25 and plan == '智能测款': share *= 1.75
        if day >= 25 and keyword == '低价 清仓': normal_roi = .5
        if day >= 25 and plan == '智能测款' and audience == '相似店铺人群': share *= 2.2
        cost = round(2800 * share, 2); roi = normal_roi; amount = round(cost * roi, 2)
        clicks = int(cost / 2.8); impressions = clicks * 12; conversions = max(1, int(amount / 150.0))
        ad_rows.append((date, plan, keyword, audience, impressions, clicks, cost, conversions, amount, roi))
      service_rows.append((date, 180.0 if day >= 25 else 30.0, 260.0 if day >= 25 else 55.0,
                           190, .018 if day >= 25 else .042, 18 if day >= 25 else 3))
      competitor_rows.extend([
        (date, '竞品X', 799.0 if day >= 25 else 999.0, 80 if day >= 25 else 30,
         .34 if day >= 25 else .18, 24000.0 if day >= 25 else 3000.0),
        (date, '竞品Y', 910.0, 25, .13, 1500.0),
      ])
      for j in range(4):
        rating = 5 if j < 3 else 4
        review_rows.append(('r%02d_%d' % (day, j), date, products[j % len(products)][0],
                            '做工不错，物流正常，整体符合预期。', rating, 1 if j == 0 else 0, 2 + j, 'review'))
    review_rows.extend([
      ('r_special_001', '2024-06-24', 'PROD-001', '穿了三天就跑绒！和图片完全不符，太差了！', 1, 1, 999, 'review'),
      ('r_special_002', '2024-06-24', 'PROD-001', '请问会跑绒吗？', 1, 0, 150, 'qa'),
      ('r_special_003', '2024-06-24', 'PROD-001', '会！我的洗了一次就废了。', 1, 0, 260, 'qa'),
      ('r_special_004', '2024-06-25', 'PROD-001', '色差极大，拉链三天就坏了。', 1, 1, 88, 'review'),
    ])
    cur.executemany('INSERT INTO store_daily VALUES (?, ?, ?, ?, ?, ?, ?)', store_rows)
    cur.executemany('INSERT INTO channel_daily VALUES (?, ?, ?, ?, ?, ?, ?)', channel_rows)
    cur.executemany('INSERT INTO product_daily VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', product_rows)
    cur.executemany('INSERT INTO ad_daily VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', ad_rows)
    cur.executemany('INSERT INTO reviews VALUES (?, ?, ?, ?, ?, ?, ?, ?)', review_rows)
    cur.executemany('INSERT INTO service_daily VALUES (?, ?, ?, ?, ?, ?)', service_rows)
    cur.executemany('INSERT INTO competitor_daily VALUES (?, ?, ?, ?, ?, ?)', competitor_rows)
    cur.executemany('INSERT INTO sandbox_anomalies VALUES (?, ?, ?, ?, ?, ?)', [
      ('a_stockout', '2024-06-10', 'product', 'PROD-002', 'stock_units=0 and cvr declines through 2024-06-12', 'medium'),
      ('a_preheat', '2024-06-15', 'product', 'PROD-003', 'cart_adds doubles while cvr declines', 'low'),
      ('a_review', '2024-06-24', 'review', 'PROD-001', 'top-voted negative down-fill review/qa', 'high'),
      ('a_paid', '2024-06-25', 'channel/ad', '直通车', 'cvr=0.8%, broad low-price audience/keyword dominates', 'critical'),
      ('a_service', '2024-06-25', 'service', 'shop', 'first response reaches 180 seconds', 'high'),
      ('a_competitor', '2024-06-25', 'market', '竞品X', 'price falls to 799 and sales rise to 80', 'high'),
    ])
    conn.commit()
    return conn


def sandbox_manifest():
    return {'schema_version': SANDBOX_SCHEMA_VERSION,
            'tables': ['orders', 'fct_orders', 'products', 'users', 'user_events',
                       'store_daily', 'channel_daily', 'product_daily', 'ad_daily',
                       'reviews', 'service_daily', 'competitor_daily', 'sandbox_anomalies'],
            'seed': 17, 'cohort_data': 'aggregate_safe_sandbox',
            'ecommerce_diagnostic_period': '2024-06-01..2024-06-30'}


__all__ = ['SANDBOX_SCHEMA_VERSION', 'build_sandbox_connection',
           'build_ecommerce_sandbox_connection', 'sandbox_manifest']
