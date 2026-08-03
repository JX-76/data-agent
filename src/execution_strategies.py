# -*- coding: utf-8 -*-
"""Execution strategy registry for task-type specific SQL compilation.

The engine stays responsible for validation/retry/diagnostics. This module owns
how an AnalysisPlan is translated into SQL for each task type so new analysis
families can be added without editing the main execution loop.
"""

from task_types import ANOMALY, ATTRIBUTION, COMPARISON, DESCRIPTIVE


class StrategyCompileResult(object):
    def __init__(self, sql, trace=None, dataset_count=0, metadata=None):
        self.sql = sql
        self.trace = trace or []
        self.dataset_count = dataset_count
        self.metadata = metadata or {}


class BaseExecutionStrategy(object):
    name = "base"

    def compile(self, runtime, plan):
        raise NotImplementedError

    def _metric(self, plan):
        return plan.get("metric") or "gmv"

    def _model(self, plan):
        return plan.get("model") or "order_detail"

    def _dimensions(self, plan):
        return plan.get("dimensions") or []

    def _time_range(self, plan):
        return plan.get("time_range")

    def _compile_base(self, runtime, plan, dimensions=None, time_range=None, sort=True):
        model = self._model(plan)
        metric = self._metric(plan)
        dims = self._dimensions(plan) if dimensions is None else dimensions
        tr = self._time_range(plan) if time_range is None else time_range

        did = runtime.switch(model)
        if tr and isinstance(tr, (list, tuple)) and len(tr) == 2:
            did = runtime.filter_time_and_defaults(did, metric, tr[0], tr[1])
        did = runtime.aggregate(did, metric, dims)
        if sort and dims:
            did = runtime.sort(did, by=metric, order="DESC")
        return did

    def _result(self, runtime, did, metadata=None):
        sql = runtime.compile_sql(did)
        return StrategyCompileResult(
            sql=sql,
            trace=getattr(runtime, "trace", []),
            dataset_count=len(getattr(runtime, "datasets", {}) or {}),
            metadata=metadata or {},
        )


class DescriptiveStrategy(BaseExecutionStrategy):
    name = DESCRIPTIVE

    def compile(self, runtime, plan):
        did = self._compile_base(runtime, plan)
        return self._result(runtime, did, {"strategy": self.name})


class ComparisonStrategy(BaseExecutionStrategy):
    name = COMPARISON

    def compile(self, runtime, plan):
        metric = self._metric(plan)
        dims = self._dimensions(plan)
        current_range = plan.get("current_time_range") or plan.get("time_range")
        previous_range = plan.get("previous_time_range") or plan.get("compare_time_range")

        current_id = self._compile_base(runtime, plan, dimensions=dims, time_range=current_range, sort=False)
        if previous_range:
            previous_id = self._compile_base(runtime, plan, dimensions=dims, time_range=previous_range, sort=False)
            join_keys = list(dims)
            select_parts = []
            for dim in join_keys:
                select_parts.append("COALESCE(c.%s, p.%s) AS %s" % (dim, dim, dim))
            select_parts.extend([
                "c.%s AS current" % metric,
                "p.%s AS previous" % metric,
                "c.%s - p.%s AS delta" % (metric, metric),
                "CASE WHEN p.%s = 0 THEN NULL ELSE (c.%s - p.%s) * 1.0 / p.%s END AS delta_pct" % (metric, metric, metric, metric),
            ])
            if join_keys:
                on_clause = " AND ".join(["c.%s = p.%s" % (key, key) for key in join_keys])
                sql = "SELECT %s\nFROM %s c\nFULL OUTER JOIN %s p ON %s" % (", ".join(select_parts), current_id, previous_id, on_clause)
            else:
                sql = "SELECT %s\nFROM %s c CROSS JOIN %s p" % (", ".join(select_parts), current_id, previous_id)
            did = runtime._register(self._model(plan), sql, join_keys + ["current", "previous", "delta", "delta_pct"], parents=[current_id, previous_id], op="compare(%s)" % metric)
        else:
            did = current_id
        if dims:
            did = runtime.sort(did, by="delta" if previous_range else metric, order="DESC")
        return self._result(runtime, did, {"strategy": self.name, "has_previous_range": bool(previous_range)})


class AnomalyStrategy(BaseExecutionStrategy):
    name = ANOMALY

    def compile(self, runtime, plan):
        dims = list(self._dimensions(plan))
        # Product/runtime semantic layer exposes the daily bucket as `date`.
        # Older planners used `day` as a generic grain name, which produced
        # invalid SQL (`no such column: day`) for anomaly/trend cases. Normalize
        # grain-like values to executable dimension ids before compilation.
        time_dim = plan.get("time_dimension") or "date"
        if time_dim == "day":
            time_dim = "date"
        dims = ["date" if d == "day" else d for d in dims]
        if time_dim not in dims:
            dims.insert(0, time_dim)
        did = self._compile_base(runtime, plan, dimensions=dims, sort=False)
        return self._result(runtime, did, {"strategy": self.name, "time_dimension": time_dim})


class AttributionStrategy(ComparisonStrategy):
    name = ATTRIBUTION

    def compile(self, runtime, plan):
        if not self._dimensions(plan):
            plan = dict(plan)
            plan["dimensions"] = [plan.get("attribution_dimension") or "channel"]
        result = ComparisonStrategy.compile(self, runtime, plan)
        result.metadata["strategy"] = self.name
        return result


class ExecutionStrategyRegistry(object):
    def __init__(self):
        self._strategies = {}
        self.register(DescriptiveStrategy())
        self.register(ComparisonStrategy())
        self.register(AnomalyStrategy())
        self.register(AttributionStrategy())

    def register(self, strategy):
        self._strategies[strategy.name] = strategy

    def get(self, task_type):
        return self._strategies.get(task_type or DESCRIPTIVE)

    def names(self):
        return sorted(self._strategies.keys())


_DEFAULT_REGISTRY = ExecutionStrategyRegistry()


def get_execution_strategy_registry():
    return _DEFAULT_REGISTRY


def get_execution_strategy(task_type):
    return _DEFAULT_REGISTRY.get(task_type)


__all__ = [
    "StrategyCompileResult",
    "BaseExecutionStrategy",
    "DescriptiveStrategy",
    "ComparisonStrategy",
    "AnomalyStrategy",
    "AttributionStrategy",
    "ExecutionStrategyRegistry",
    "get_execution_strategy_registry",
    "get_execution_strategy",
]
