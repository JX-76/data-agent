# -*- coding: utf-8 -*-
"""Replaceable deterministic forecast strategies."""
from __future__ import unicode_literals
import math

class BaseForecastStrategy(object):
    name="base"; version="v1"
    def fit_predict(self, values, horizon): raise NotImplementedError

class SeasonalNaiveStrategy(BaseForecastStrategy):
    name="seasonal_naive"; version="seasonal_naive_v1"
    def fit_predict(self, values, horizon):
        period=7; base=list(values[-period:])
        preds=[base[i % len(base)] for i in range(horizon)]
        residuals=[values[i]-values[i-period] for i in range(period,len(values))]
        sigma=(sum(x*x for x in residuals)/float(len(residuals)))**.5 if residuals else 0.0
        return {"points":preds,"baseline":sum(base)/float(len(base)),"intervals":[[max(0,p-1.96*sigma),p+1.96*sigma] for p in preds],"confidence_status":"available"}

class MovingAverageStrategy(BaseForecastStrategy):
    name="moving_average"; version="moving_average_v1"
    def fit_predict(self, values, horizon):
        base=sum(values[-7:])/float(min(7,len(values)))
        return {"points":[base]*horizon,"baseline":base,"intervals":None,"confidence_status":"unavailable"}

class ForecastStrategyRegistry(object):
    def __init__(self): self._items={}
    def register(self,item): self._items[item.name]=item; return item
    def get(self,name): return self._items.get(name) or self._items["seasonal_naive"]
_DEFAULT=ForecastStrategyRegistry(); _DEFAULT.register(SeasonalNaiveStrategy()); _DEFAULT.register(MovingAverageStrategy())
def get_forecast_strategy_registry(): return _DEFAULT
__all__=["BaseForecastStrategy","SeasonalNaiveStrategy","MovingAverageStrategy","ForecastStrategyRegistry","get_forecast_strategy_registry"]
