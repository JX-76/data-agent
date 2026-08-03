# -*- coding: utf-8 -*-
"""Controlled time-series forecast execution (R29).

Non-causal: results are statistical estimates based on historical patterns.
Predictions should be treated as indicative, not as factual commitments.
"""
from __future__ import unicode_literals

from forecast_registry import get_forecast_registry
from forecast_strategies import get_forecast_strategy_registry


def _validate_series(series, metric, min_len, min_non_null_ratio):
    """Return (errors, warnings) list for the raw time-series."""
    errors, warnings = [], []
    if len(series) < min_len:
        errors.append("insufficient_history:need_%d_got_%d" % (min_len, len(series)))
        return errors, warnings
    values = [r.get(metric) for r in series]
    null_n = sum(1 for v in values if v is None)
    ratio = 1.0 - null_n / float(len(values))
    if ratio < min_non_null_ratio:
        errors.append("data_quality:null_ratio_%.2f_below_%.2f" % (ratio, min_non_null_ratio))
    # Check for strong anomalies (last point z-score > 5)
    clean = [float(v) for v in values if v is not None]
    if len(clean) >= 4:
        mu = sum(clean) / len(clean)
        sigma = (sum((x - mu) ** 2 for x in clean) / len(clean)) ** .5
        if sigma > 0 and abs(clean[-1] - mu) / sigma > 5:
            warnings.append("strong_anomaly_in_last_period")
    # Check for date gaps if series has a date field
    dates = [r.get("date") or r.get("ds") for r in series]
    if all(dates):
        from datetime import date as date_cls
        try:
            parsed = sorted(date_cls.fromisoformat(str(d)) for d in dates)
            for i in range(1, len(parsed)):
                if (parsed[i] - parsed[i-1]).days != 1:
                    warnings.append("date_gaps_detected")
                    break
        except Exception:
            pass
    return errors, warnings


def _mape(actuals, preds):
    """Mean absolute percentage error; returns None if any actual is 0."""
    pairs = list(zip(actuals, preds))
    if not pairs or any(a == 0 for a, _ in pairs):
        return None
    return sum(abs(a - p) / abs(a) for a, p in pairs) / float(len(pairs))


def _mae(actuals, preds):
    pairs = list(zip(actuals, preds))
    if not pairs:
        return None
    return sum(abs(a - p) for a, p in pairs) / float(len(pairs))


def _run_backtest(series, metric, strategy, holdout, mape_threshold):
    """Hold out last *holdout* periods, fit on prefix, evaluate on holdout."""
    if len(series) < holdout + 7:
        return {"available": False, "reason": "insufficient_history_for_backtest"}
    train_vals = [float(r[metric]) for r in series[:-holdout] if r.get(metric) is not None]
    hold_vals = [float(r[metric]) for r in series[-holdout:] if r.get(metric) is not None]
    if len(train_vals) < 7 or len(hold_vals) == 0:
        return {"available": False, "reason": "insufficient_clean_values"}
    result = strategy.fit_predict(train_vals, len(hold_vals))
    preds = result["points"]
    mae = _mae(hold_vals, preds)
    mape = _mape(hold_vals, preds)
    passed = mape is not None and mape <= mape_threshold
    return {"available": True, "holdout_periods": holdout, "mae": mae, "mape": mape,
            "mape_threshold": mape_threshold, "passed": passed,
            "strategy": strategy.name, "strategy_version": strategy.version}


def run_forecast(metric, horizon=1, granularity="day", series=None,
                 tenant_id=None, registry=None, strategy_registry=None):
    """Validate, forecast and backtest.

    Returns an execution-result-shaped dict compatible with standardize_analysis_output.
    """
    registry = registry or get_forecast_registry()
    strategy_registry = strategy_registry or get_forecast_strategy_registry()
    series = list(series or [])

    # 1 — Definition resolution
    resolution = registry.resolve(metric, granularity=granularity, horizon=horizon)
    if not resolution["ok"]:
        return {"status": "need_clarification", "task_type": "forecast",
                "results": [], "results_summary": {"row_count": 0},
                "diagnostics": {"phase": "forecast_resolution",
                                "forecast_errors": resolution["errors"],
                                "quality": {"empty_result": True}}}
    defn = resolution["definition"]

    # 2 — Tenant filter (additive safety; series caller is responsible for pre-filtering)
    if tenant_id:
        series = [r for r in series if str(r.get("tenant_id", tenant_id)) == str(tenant_id)]

    # 3 — Data quality validation
    min_history = defn["required_historical_periods"]
    min_non_null = defn["minimum_data_quality_rules"]["min_non_null_ratio"]
    errors, warnings = _validate_series(series, metric, min_history, min_non_null)
    if errors:
        return {"status": "need_clarification", "task_type": "forecast",
                "results": [], "results_summary": {"row_count": 0},
                "diagnostics": {"phase": "data_validation", "validation_errors": errors,
                                "validation_warnings": warnings, "quality": {"empty_result": True}}}

    # 4 — Choose strategy
    algo = defn["algorithm_policy"]
    strategy = strategy_registry.get(algo)

    # 5 — Forecast
    train_vals = [float(r[metric]) for r in series if r.get(metric) is not None]
    fit = strategy.fit_predict(train_vals, horizon)

    # 6 — Backtest
    backtest = _run_backtest(series, metric, strategy,
                             defn["backtest_policy"]["holdout_periods"],
                             defn["backtest_policy"]["mape_threshold"])

    # 7 — Build forecast points (without raw training values)
    points = [{"step": i + 1, "forecast": v,
               "interval": fit["intervals"][i] if fit.get("intervals") else None,
               "confidence_interval": {"lower": fit["intervals"][i][0], "upper": fit["intervals"][i][1]}
               if fit.get("intervals") else {"status": fit.get("confidence_status", "unavailable")}}
              for i, v in enumerate(fit["points"])]

    # 8 — Caveats
    caveats = [
        u"预测结果为统计估计，不构成业务承诺，不具备因果解释能力。",
        u"模型：%s，版本：%s。" % (strategy.name, strategy.version),
        u"训练窗口：最近 %d 个数据点。" % len(train_vals),
    ]
    if "strong_anomaly_in_last_period" in warnings:
        caveats.append(u"历史数据末尾存在强异常点，预测精度可能下降。")
    if "date_gaps_detected" in warnings:
        caveats.append(u"检测到日期缺口，预测结果仅供参考。")
    if fit.get("confidence_status") == "unavailable":
        caveats.append(u"当前算法不支持置信区间，已显式标注为 unavailable。")

    return {
        "status": "ok",
        "task_type": "forecast",
        "metric": metric,
        "results": points,
        "results_summary": {"row_count": len(points), "source": "forecast_sandbox"},
        "diagnostics": {
            "quality": {"empty_result": False},
            "forecast_definition": defn,
            "training_window": len(train_vals),
            "method": strategy.name,
            "method_version": strategy.version,
            "backtest": backtest,
            "validation_warnings": warnings,
        },
        "baseline": fit["baseline"],
        "caveats": caveats,
    }


__all__ = ["run_forecast"]
