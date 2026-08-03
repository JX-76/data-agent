# -*- coding: utf-8 -*-
"""Small, replaceable statistical strategies for controlled A/B MVP."""
from __future__ import unicode_literals
import math

def _normal_cdf(z): return .5 * (1.0 + math.erf(z / math.sqrt(2.0)))
def _mean(values): return sum(values) / float(len(values)) if values else 0.0
def _variance(values, mean): return sum((v-mean)**2 for v in values) / float(len(values)-1) if len(values)>1 else 0.0

class ExperimentStrategy(object):
    name="base"
    def analyze(self, control, treatment): raise NotImplementedError

class ConversionRateStrategy(ExperimentStrategy):
    name="two_proportion_z_test"
    def analyze(self, control, treatment):
        n0,n1=len(control),len(treatment); p0,p1=_mean(control),_mean(treatment); diff=p1-p0
        pooled=(sum(control)+sum(treatment))/float(n0+n1); se=math.sqrt(max(0.0,pooled*(1-pooled)*(1.0/n0+1.0/n1)))
        z=diff/se if se else 0.0; p=max(0.0,min(1.0,2*(1-_normal_cdf(abs(z)))))
        ci_se=math.sqrt(max(0.0,p0*(1-p0)/n0+p1*(1-p1)/n1))
        return {"control_value":p0,"treatment_value":p1,"absolute_lift":diff,"relative_lift":None if p0==0 else diff/p0,"p_value":p,"confidence_interval":[diff-1.96*ci_se,diff+1.96*ci_se],"method":self.name}

class WelchTTestStrategy(ExperimentStrategy):
    name="welch_t_test_normal_approximation"
    def analyze(self, control, treatment):
        n0,n1=len(control),len(treatment); m0,m1=_mean(control),_mean(treatment); diff=m1-m0
        se=math.sqrt(_variance(control,m0)/n0+_variance(treatment,m1)/n1)
        t=diff/se if se else 0.0
        p=0.0 if se == 0.0 and diff != 0.0 else max(0.0,min(1.0,2*(1-_normal_cdf(abs(t)))))
        return {"control_value":m0,"treatment_value":m1,"absolute_lift":diff,"relative_lift":None if m0==0 else diff/m0,"p_value":p,"confidence_interval":[diff-1.96*se,diff+1.96*se],"method":self.name}

class ExperimentStrategyRegistry(object):
    def __init__(self): self._items={"binary_conversion":ConversionRateStrategy(),"continuous":WelchTTestStrategy()}
    def get(self, metric_kind): return self._items.get(metric_kind)
    def register(self, metric_kind, strategy): self._items[metric_kind]=strategy; return strategy
_DEFAULT=ExperimentStrategyRegistry()
def get_experiment_strategy_registry(): return _DEFAULT
__all__=["ExperimentStrategy","ConversionRateStrategy","WelchTTestStrategy","ExperimentStrategyRegistry","get_experiment_strategy_registry"]
