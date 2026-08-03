# -*- coding: utf-8 -*-
"""Task capability registry for analysis planning and execution.

A capability describes the minimum contract of an analysis family.  It keeps
routing, execution, charting, and future semantic metadata from maintaining
separate support lists.  The registry deliberately validates only hard
preconditions; optional requirements are returned as warnings so existing MVP
plans remain backward compatible.
"""

from task_types import ANOMALY, ATTRIBUTION, COMPARISON, DESCRIPTIVE, FUNNEL, RETENTION


class CapabilityValidationResult(object):
    def __init__(self, ok=True, errors=None, warnings=None, metadata=None):
        self.ok = bool(ok)
        self.errors = errors or []
        self.warnings = warnings or []
        self.metadata = metadata or {}

    def to_dict(self):
        return {
            "ok": self.ok,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "metadata": dict(self.metadata),
        }


class TaskCapability(object):
    """Declarative requirements and product hints for one task type."""

    def __init__(self, task_type, executable=True, metric_required=True,
                 dimension_required=False, time_series_required=False,
                 minimum_rows=0, chart_type="none", execution_mode="aggregate",
                 supported_models=None, notes=""):
        self.task_type = task_type
        self.executable = bool(executable)
        self.metric_required = bool(metric_required)
        self.dimension_required = bool(dimension_required)
        self.time_series_required = bool(time_series_required)
        self.minimum_rows = int(minimum_rows or 0)
        self.chart_type = chart_type
        self.execution_mode = execution_mode
        self.supported_models = list(supported_models or [])
        self.notes = notes

    def to_dict(self):
        return {
            "task_type": self.task_type,
            "executable": self.executable,
            "metric_required": self.metric_required,
            "dimension_required": self.dimension_required,
            "time_series_required": self.time_series_required,
            "minimum_rows": self.minimum_rows,
            "chart_type": self.chart_type,
            "execution_mode": self.execution_mode,
            "supported_models": list(self.supported_models),
            "notes": self.notes,
        }

    def validate(self, plan):
        plan = plan or {}
        errors = []
        warnings = []
        task_type = plan.get("task_type") or self.task_type
        if task_type != self.task_type:
            errors.append({"code": "capability_task_type_mismatch", "expected": self.task_type, "actual": task_type})
        if not self.executable:
            errors.append({"code": "task_type_not_executable", "task_type": self.task_type})
        if self.metric_required and not plan.get("metric"):
            errors.append({"code": "capability_metric_required", "task_type": self.task_type, "field": "metric"})
        if self.dimension_required and not (plan.get("dimensions") or []):
            errors.append({"code": "capability_dimension_required", "task_type": self.task_type, "field": "dimensions"})
        if self.supported_models and plan.get("model") and plan.get("model") not in self.supported_models:
            errors.append({"code": "capability_model_not_supported", "task_type": self.task_type, "model": plan.get("model")})
        if self.time_series_required and not (plan.get("time_dimension") or plan.get("time_range")):
            warnings.append({"code": "capability_time_series_recommended", "task_type": self.task_type, "fields": ["time_dimension", "time_range"]})
        if self.task_type == COMPARISON and not (plan.get("previous_time_range") or plan.get("compare_time_range")):
            warnings.append({"code": "capability_previous_range_missing", "task_type": self.task_type, "message": "comparison will run as a single-period baseline until a prior range is resolved"})
        if self.task_type == ATTRIBUTION and not (plan.get("dimensions") or plan.get("attribution_dimension")):
            warnings.append({"code": "capability_attribution_dimension_defaulted", "task_type": self.task_type, "default_dimension": "channel"})
        return CapabilityValidationResult(
            ok=len(errors) == 0,
            errors=errors,
            warnings=warnings,
            metadata={"capability": self.to_dict()},
        )


class TaskCapabilityRegistry(object):
    def __init__(self):
        self._capabilities = {}
        self._register_defaults()

    def _register_defaults(self):
        self.register(TaskCapability(DESCRIPTIVE, chart_type="bar", execution_mode="aggregate"))
        self.register(TaskCapability(COMPARISON, chart_type="line", execution_mode="comparison"))
        self.register(TaskCapability(ANOMALY, chart_type="line_with_anomaly", time_series_required=True, minimum_rows=3, execution_mode="time_series"))
        self.register(TaskCapability(ATTRIBUTION, chart_type="waterfall", execution_mode="comparison_by_dimension"))
        # Registered now so router/product layers share a single capability map;
        # execution remains explicitly unavailable until their dedicated semantic
        # models and SQL strategies are delivered in the later Phase 6 slice.
        self.register(TaskCapability(FUNNEL, executable=False, metric_required=False, chart_type="funnel", execution_mode="event_funnel", notes="requires registered funnel definition"))
        self.register(TaskCapability(RETENTION, executable=True, metric_required=False, chart_type="heatmap", execution_mode="cohort", supported_models=["user_events"], notes="requires registered cohort definition and aggregate-only event model"))

    def register(self, capability):
        if not isinstance(capability, TaskCapability):
            raise TypeError("capability must be a TaskCapability")
        self._capabilities[capability.task_type] = capability
        return capability

    def get(self, task_type):
        return self._capabilities.get(task_type)

    def names(self):
        return sorted(self._capabilities.keys())

    def validate(self, plan):
        plan = plan or {}
        task_type = plan.get("task_type") or DESCRIPTIVE
        capability = self.get(task_type)
        if capability is None:
            return CapabilityValidationResult(
                ok=False,
                errors=[{"code": "unknown_task_capability", "task_type": task_type}],
                metadata={"task_type": task_type},
            )
        return capability.validate(plan)


_DEFAULT_REGISTRY = TaskCapabilityRegistry()


def get_task_capability_registry():
    return _DEFAULT_REGISTRY


def get_task_capability(task_type):
    return _DEFAULT_REGISTRY.get(task_type)


def validate_plan_capabilities(plan):
    return _DEFAULT_REGISTRY.validate(plan)


__all__ = [
    "CapabilityValidationResult", "TaskCapability", "TaskCapabilityRegistry",
    "get_task_capability_registry", "get_task_capability", "validate_plan_capabilities",
]
