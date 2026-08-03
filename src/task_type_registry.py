# -*- coding: utf-8 -*-
"""Task type plugin registry.

Phase R18 centralizes task-type metadata so adding a new analysis family does
not require changing AgentFacade. The registry is intentionally declarative and
safe: unknown task types resolve to the descriptive fallback.
"""
from __future__ import unicode_literals

from task_types import DESCRIPTIVE, COMPARISON, ATTRIBUTION, ANOMALY, FUNNEL, RETENTION, FORECAST, EXPERIMENT


class TaskTypeDefinition(object):
    def __init__(self, task_type, supported_intents=None, required_plan_fields=None,
                 default_metric_strategy=None, default_dimension_strategy=None,
                 default_time_range_strategy=None, analyzer_name=None,
                 report_template_name=None, chart_policy=None, fallback_policy=None):
        self.task_type = task_type or DESCRIPTIVE
        self.supported_intents = list(supported_intents or [])
        self.required_plan_fields = list(required_plan_fields or [])
        self.default_metric_strategy = default_metric_strategy
        self.default_dimension_strategy = default_dimension_strategy
        self.default_time_range_strategy = default_time_range_strategy
        self.analyzer_name = analyzer_name or self.task_type
        self.report_template_name = report_template_name or self.task_type
        self.chart_policy = chart_policy or {}
        self.fallback_policy = fallback_policy or {"task_type": DESCRIPTIVE, "mode": "safe_descriptive_fallback"}

    def to_dict(self):
        return {
            "task_type": self.task_type,
            "supported_intents": list(self.supported_intents),
            "required_plan_fields": list(self.required_plan_fields),
            "default_metric_strategy": self.default_metric_strategy,
            "default_dimension_strategy": self.default_dimension_strategy,
            "default_time_range_strategy": self.default_time_range_strategy,
            "analyzer_name": self.analyzer_name,
            "report_template_name": self.report_template_name,
            "chart_policy": dict(self.chart_policy),
            "fallback_policy": dict(self.fallback_policy),
        }


class TaskTypeRegistry(object):
    def __init__(self, fallback=None):
        self._definitions = {}
        self.fallback = fallback or TaskTypeDefinition(
            task_type=DESCRIPTIVE,
            supported_intents=["metric_query", "analysis", "descriptive"],
            required_plan_fields=[],
            default_metric_strategy="use_plan_metric_or_domain_default",
            default_dimension_strategy="use_plan_dimensions_or_empty",
            default_time_range_strategy="use_plan_time_range_or_none",
            analyzer_name=DESCRIPTIVE,
            report_template_name=DESCRIPTIVE,
            chart_policy={"policy_id": "descriptive_default", "chart_type": "bar"},
            fallback_policy={"task_type": DESCRIPTIVE, "mode": "self"},
        )
        self.register(self.fallback)

    def register(self, definition):
        if not isinstance(definition, TaskTypeDefinition):
            raise TypeError("definition must be a TaskTypeDefinition")
        self._definitions[definition.task_type] = definition
        return definition

    def get(self, task_type):
        return self._definitions.get(task_type) or self.fallback

    def resolve(self, task_type):
        return self.get(task_type)

    def registered_task_types(self):
        return sorted(self._definitions.keys())

    def get_analyzer(self, task_type):
        return self.get(task_type).analyzer_name

    def get_report_template(self, task_type):
        return self.get(task_type).report_template_name

    def get_chart_policy(self, task_type):
        return dict(self.get(task_type).chart_policy)

    def as_dict(self):
        return dict((name, definition.to_dict()) for name, definition in self._definitions.items())


def build_default_task_type_registry():
    registry = TaskTypeRegistry()
    registry.register(TaskTypeDefinition(
        task_type=COMPARISON,
        supported_intents=["comparison", "compare"],
        required_plan_fields=["metric"],
        default_metric_strategy="use_plan_metric_or_gmv",
        default_dimension_strategy="use_plan_dimensions_or_empty",
        default_time_range_strategy="use_current_previous_or_plan_time_range",
        analyzer_name=COMPARISON,
        report_template_name=COMPARISON,
        chart_policy={"policy_id": "comparison_default", "chart_type": "grouped_bar"},
        fallback_policy={"task_type": DESCRIPTIVE, "mode": "descriptive_on_missing_comparison_inputs"},
    ))
    registry.register(TaskTypeDefinition(
        task_type=ATTRIBUTION,
        supported_intents=["attribution", "driver", "why"],
        required_plan_fields=["metric"],
        default_metric_strategy="use_plan_metric_or_gmv",
        default_dimension_strategy="use_first_dimension_or_channel",
        default_time_range_strategy="use_plan_time_range_or_none",
        analyzer_name=ATTRIBUTION,
        report_template_name=ATTRIBUTION,
        chart_policy={"policy_id": "attribution_default", "chart_type": "waterfall"},
        fallback_policy={"task_type": DESCRIPTIVE, "mode": "descriptive_on_missing_driver_inputs"},
    ))
    registry.register(TaskTypeDefinition(
        task_type=ANOMALY,
        supported_intents=["anomaly", "monitoring"],
        required_plan_fields=["metric"],
        default_metric_strategy="use_plan_metric_or_gmv",
        default_dimension_strategy="use_plan_dimensions_or_empty",
        default_time_range_strategy="use_plan_time_range_or_recent_window",
        analyzer_name=ANOMALY,
        report_template_name=ANOMALY,
        chart_policy={"policy_id": "anomaly_default", "chart_type": "line_with_anomaly"},
        fallback_policy={"task_type": DESCRIPTIVE, "mode": "descriptive_on_missing_time_series"},
    ))
    registry.register(TaskTypeDefinition(
        task_type=RETENTION,
        supported_intents=["retention", "cohort", "repurchase"],
        required_plan_fields=["cohort_definition"],
        default_metric_strategy="retention_rate",
        default_dimension_strategy="cohort_period",
        default_time_range_strategy="use_plan_time_range_or_definition_window",
        analyzer_name=RETENTION,
        report_template_name=RETENTION,
        chart_policy={"policy_id": "retention_default", "chart_type": "heatmap"},
        fallback_policy={"task_type": RETENTION, "mode": "clarify_or_unsupported_on_missing_cohort_definition"},
    ))
    registry.register(TaskTypeDefinition(task_type=EXPERIMENT, supported_intents=["experiment", "ab_test"], required_plan_fields=["experiment_id"], analyzer_name=EXPERIMENT, report_template_name=EXPERIMENT, chart_policy={"policy_id": "experiment_default", "chart_type": "grouped_bar"}, fallback_policy={"task_type": EXPERIMENT, "mode": "clarify_on_missing_experiment_definition"}))
    registry.register(TaskTypeDefinition(
        task_type=FORECAST,
        supported_intents=["forecast", "prediction"],
        required_plan_fields=["metric", "time_dimension"],
        default_metric_strategy="controlled_forecast_metric_only",
        default_dimension_strategy="daily_time_series",
        default_time_range_strategy="use_forecast_training_window",
        analyzer_name=FORECAST,
        report_template_name=FORECAST,
        chart_policy={"policy_id": "forecast_default", "chart_type": "forecast_trend_overlay"},
        fallback_policy={"task_type": FORECAST, "mode": "clarify_on_unsupported_metric_or_series"},
    ))
    registry.register(TaskTypeDefinition(
        task_type=FUNNEL,
        supported_intents=["funnel"],
        required_plan_fields=[],
        default_metric_strategy=None,
        default_dimension_strategy=None,
        default_time_range_strategy="use_plan_time_range_or_none",
        analyzer_name=DESCRIPTIVE,
        report_template_name=FUNNEL,
        chart_policy={"policy_id": "funnel_default", "chart_type": "funnel"},
        fallback_policy={"task_type": DESCRIPTIVE, "mode": "placeholder_or_descriptive"},
    ))
    return registry


DEFAULT_TASK_TYPE_REGISTRY = build_default_task_type_registry()


def get_task_type_registry():
    return DEFAULT_TASK_TYPE_REGISTRY


__all__ = [
    "TaskTypeDefinition", "TaskTypeRegistry", "build_default_task_type_registry",
    "DEFAULT_TASK_TYPE_REGISTRY", "get_task_type_registry",
]
