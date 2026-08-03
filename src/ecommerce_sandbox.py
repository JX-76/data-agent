# -*- coding: utf-8 -*-
"""Deterministic ecommerce diagnostic sandbox tools.

The module is intentionally local and read-only.  It provides realistic linked
observations for agent evaluation, never a substitute for production APIs.
"""
from __future__ import unicode_literals

from sandbox_data_factory import build_ecommerce_sandbox_connection


def _where(date_start=None, date_end=None, extra=None):
    clauses, params = [], []
    if date_start:
        clauses.append('date >= ?'); params.append(date_start)
    if date_end:
        clauses.append('date <= ?'); params.append(date_end)
    if extra:
        clauses.extend(extra[0]); params.extend(extra[1])
    return (' WHERE ' + ' AND '.join(clauses) if clauses else ''), params


def _rows(cursor):
    names = [x[0] for x in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


class EcommerceSandboxTools(object):
    def __init__(self, connection=None):
        self.conn = connection or build_ecommerce_sandbox_connection()

    def get_store_overview(self, date_start=None, date_end=None):
        sql, params = _where(date_start, date_end)
        result = _rows(self.conn.execute('SELECT * FROM store_daily' + sql + ' ORDER BY date', params))
        return {'rows': result, 'row_count': len(result), 'source': 'ecommerce_sandbox'}

    def get_channel_performance(self, date_start=None, date_end=None, channel=None):
        extra = (['channel = ?'], [channel]) if channel else None
        sql, params = _where(date_start, date_end, extra)
        result = _rows(self.conn.execute('SELECT * FROM channel_daily' + sql + ' ORDER BY date, channel', params))
        return {'rows': result, 'row_count': len(result), 'source': 'ecommerce_sandbox'}

    def get_product_detail(self, date_start=None, date_end=None, product_id=None):
        extra = (['product_id = ?'], [product_id]) if product_id else None
        sql, params = _where(date_start, date_end, extra)
        result = _rows(self.conn.execute('SELECT * FROM product_daily' + sql + ' ORDER BY date, product_id', params))
        return {'rows': result, 'row_count': len(result), 'source': 'ecommerce_sandbox'}

    def get_ad_report(self, date_start=None, date_end=None, plan_name=None):
        extra = (['plan_name = ?'], [plan_name]) if plan_name else None
        sql, params = _where(date_start, date_end, extra)
        result = _rows(self.conn.execute('SELECT * FROM ad_daily' + sql + ' ORDER BY date, plan_name, audience', params))
        return {'rows': result, 'row_count': len(result), 'source': 'ecommerce_sandbox'}

    def get_reviews(self, date_start=None, date_end=None, product_id=None):
        extra = (['product_id = ?'], [product_id]) if product_id else None
        sql, params = _where(date_start, date_end, extra)
        result = _rows(self.conn.execute('SELECT * FROM reviews' + sql + ' ORDER BY date, helpful_votes DESC', params))
        return {'rows': result, 'row_count': len(result), 'source': 'ecommerce_sandbox'}

    def get_service_data(self, date_start=None, date_end=None):
        sql, params = _where(date_start, date_end)
        result = _rows(self.conn.execute('SELECT * FROM service_daily' + sql + ' ORDER BY date', params))
        return {'rows': result, 'row_count': len(result), 'source': 'ecommerce_sandbox'}

    def get_competitor_insight(self, date_start=None, date_end=None):
        sql, params = _where(date_start, date_end)
        result = _rows(self.conn.execute('SELECT * FROM competitor_daily' + sql + ' ORDER BY date, competitor_name', params))
        return {'rows': result, 'row_count': len(result), 'source': 'ecommerce_sandbox'}


__all__ = ['EcommerceSandboxTools']
