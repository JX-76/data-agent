# -*- coding: utf-8 -*-
"""GMV Driver Analysis: Decompose GMV into order_count x AOV (Average Order Value).

Provides:
- gmv_driver_decomposition: decompose GMV into order_count * AOV
- gmv_driver_change: analyze period-over-period GMV change by driver
- gmv_driver_report: generate natural language report of GMV drivers

Python 2.7 compatible and deterministic.
"""

from __future__ import unicode_literals


def _to_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def gmv_driver_decomposition(gmv, order_count):
    """Decompose GMV into order_count * AOV.

    Args:
        gmv: Total GMV value
        order_count: Total order count

    Returns:
        {
            "gmv": gmv,
            "order_count": order_count,
            "aov": aov,  # Average Order Value
            "status": "ok" or "insufficient_data"
        }
    """
    gmv = _to_float(gmv)
    order_count = _to_float(order_count)
    if order_count <= 0 or gmv <= 0:
        return {
            "status": "insufficient_data",
            "gmv": gmv,
            "order_count": order_count,
            "aov": 0.0,
        }
    aov = gmv / order_count
    return {
        "status": "ok",
        "gmv": gmv,
        "order_count": order_count,
        "aov": aov,
    }


def gmv_driver_change(current_gmv, current_orders, prev_gmv, prev_orders):
    """Analyze period-over-period GMV change by driver.

    GMV = order_count * AOV
    GMV change = volume_effect + price_mix_effect

    Args:
        current_gmv: Current period GMV
        current_orders: Current period order count
        prev_gmv: Previous period GMV
        prev_orders: Previous period order count

    Returns:
        {
            "status": "ok" or "insufficient_data",
            "current": {"gmv": ..., "orders": ..., "aov": ...},
            "previous": {"gmv": ..., "orders": ..., "aov": ...},
            "gmv_change": ...,        # absolute change
            "gmv_change_pct": ...,    # percentage change
            "volume_effect": ...,     # contribution from order count change
            "price_mix_effect": ...,  # contribution from AOV change
            "volume_effect_pct": ..., # volume effect as % of total change
            "price_mix_effect_pct": ..., # price/mix effect as % of total change
            "primary_driver": "volume" or "price_mix" or "unknown",
        }
    """
    current_gmv = _to_float(current_gmv)
    current_orders = _to_float(current_orders)
    prev_gmv = _to_float(prev_gmv)
    prev_orders = _to_float(prev_orders)

    if prev_gmv <= 0 or prev_orders <= 0 or current_orders <= 0:
        return {
            "status": "insufficient_data",
            "current": {"gmv": current_gmv, "orders": current_orders, "aov": 0.0 if current_orders <= 0 else current_gmv / current_orders},
            "previous": {"gmv": prev_gmv, "orders": prev_orders, "aov": 0.0 if prev_orders <= 0 else prev_gmv / prev_orders},
            "gmv_change": 0.0,
            "gmv_change_pct": 0.0,
            "volume_effect": 0.0,
            "price_mix_effect": 0.0,
            "volume_effect_pct": 0.0,
            "price_mix_effect_pct": 0.0,
            "primary_driver": "unknown",
        }

    current_aov = current_gmv / current_orders
    prev_aov = prev_gmv / prev_orders
    gmv_change = current_gmv - prev_gmv
    gmv_change_pct = gmv_change / prev_gmv if prev_gmv != 0 else 0.0

    # Volume effect: (current_orders - prev_orders) * prev_aov
    volume_effect = (current_orders - prev_orders) * prev_aov
    # Price/mix effect: (current_aov - prev_aov) * current_orders
    price_mix_effect = (current_aov - prev_aov) * current_orders

    total_effect = abs(volume_effect) + abs(price_mix_effect)
    volume_effect_pct = abs(volume_effect) / total_effect if total_effect > 0 else 0.0
    price_mix_effect_pct = abs(price_mix_effect) / total_effect if total_effect > 0 else 0.0

    if abs(volume_effect) > abs(price_mix_effect):
        primary_driver = "volume"
    elif abs(price_mix_effect) > abs(volume_effect):
        primary_driver = "price_mix"
    else:
        primary_driver = "unknown"

    return {
        "status": "ok",
        "current": {"gmv": current_gmv, "orders": current_orders, "aov": current_aov},
        "previous": {"gmv": prev_gmv, "orders": prev_orders, "aov": prev_aov},
        "gmv_change": gmv_change,
        "gmv_change_pct": gmv_change_pct,
        "volume_effect": volume_effect,
        "price_mix_effect": price_mix_effect,
        "volume_effect_pct": volume_effect_pct,
        "price_mix_effect_pct": price_mix_effect_pct,
        "primary_driver": primary_driver,
    }


def gmv_driver_report(driver_result):
    """Generate natural language report from GMV driver analysis result.

    Args:
        driver_result: Result dict from gmv_driver_change()

    Returns:
        List of natural language sentences.
    """
    if driver_result.get("status") != "ok":
        return [u"数据不足，无法进行 GMV 驱动拆解分析。"]

    current = driver_result["current"]
    previous = driver_result["previous"]
    gmv_change = driver_result["gmv_change"]
    gmv_change_pct = driver_result["gmv_change_pct"]
    volume_effect = driver_result["volume_effect"]
    price_mix_effect = driver_result["price_mix_effect"]
    primary_driver = driver_result["primary_driver"]

    lines = []

    # Direction
    if gmv_change >= 0:
        lines.append(u"GMV 环比增长 %.2f（%.1f%%）。" % (gmv_change, gmv_change_pct * 100))
    else:
        lines.append(u"GMV 环比下降 %.2f（%.1f%%）。" % (abs(gmv_change), abs(gmv_change_pct) * 100))

    # Volume vs price mix
    lines.append(u"订单量从 %.0f 变为 %.0f，客单价从 %.2f 变为 %.2f。" % (
        previous["orders"], current["orders"],
        previous["aov"], current["aov"],
    ))

    # Primary driver
    if primary_driver == "volume":
        lines.append(u"主要驱动因素是订单量变化（贡献 %.1f%%），客单价变化贡献 %.1f%%。" % (
            driver_result["volume_effect_pct"] * 100,
            driver_result["price_mix_effect_pct"] * 100,
        ))
    elif primary_driver == "price_mix":
        lines.append(u"主要驱动因素是客单价变化（贡献 %.1f%%），订单量变化贡献 %.1f%%。" % (
            driver_result["price_mix_effect_pct"] * 100,
            driver_result["volume_effect_pct"] * 100,
        ))
    else:
        lines.append(u"订单量和客单价变化贡献相当。")

    return lines


__all__ = ["gmv_driver_decomposition", "gmv_driver_change", "gmv_driver_report"]
