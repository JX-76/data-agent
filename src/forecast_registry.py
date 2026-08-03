# -*- coding: utf-8 -*-
"""Declarative, controlled forecasting definitions (R29)."""
from __future__ import unicode_literals

class ForecastDefinition(object):
    def __init__(self, metric, granularity="day", required_history=14, max_horizon=7,
                 algorithm_policy="seasonal_naive", min_non_null_ratio=.95,
                 backtest_periods=3, mape_threshold=.50, capability_status="mvp"):
        self.metric, self.granularity = metric, granularity
        self.required_history, self.max_horizon = int(required_history), int(max_horizon)
        self.algorithm_policy, self.min_non_null_ratio = algorithm_policy, float(min_non_null_ratio)
        self.backtest_periods, self.mape_threshold = int(backtest_periods), float(mape_threshold)
        self.capability_status = capability_status
    def to_dict(self):
        return {"metric": self.metric, "supported_metrics": [self.metric], "granularity": self.granularity,
                "required_historical_periods": self.required_history, "forecast_horizon_upper_bound": self.max_horizon,
                "algorithm_policy": self.algorithm_policy, "minimum_data_quality_rules": {"continuous_periods": True, "min_non_null_ratio": self.min_non_null_ratio},
                "backtest_policy": {"holdout_periods": self.backtest_periods, "mape_threshold": self.mape_threshold},
                "capability_status": self.capability_status}

class ForecastRegistry(object):
    def __init__(self): self._items = {}
    def register(self, definition): self._items[definition.metric] = definition; return definition
    def get(self, metric): return self._items.get(metric)
    def resolve(self, metric, granularity="day", horizon=1):
        item = self.get(metric); errors=[]
        if not item: errors.append("forecast_metric_not_supported")
        elif granularity != item.granularity: errors.append("forecast_granularity_not_supported:%s" % granularity)
        elif int(horizon or 0) < 1 or int(horizon) > item.max_horizon: errors.append("forecast_horizon_out_of_range:%s" % horizon)
        return {"ok": not errors, "definition": item.to_dict() if item else None, "errors": errors}
    def names(self): return sorted(self._items)

def build_default_forecast_registry():
    r=ForecastRegistry()
    r.register(ForecastDefinition("gmv")); r.register(ForecastDefinition("order_count"))
    return r
DEFAULT_FORECAST_REGISTRY=build_default_forecast_registry()
def get_forecast_registry(): return DEFAULT_FORECAST_REGISTRY
__all__=["ForecastDefinition","ForecastRegistry","get_forecast_registry"]
