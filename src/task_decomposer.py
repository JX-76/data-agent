# -*- coding: utf-8 -*-
"""Task Decomposer: decompose large AnalysisPlan into sub-plans.

This module provides the decomposition layer between planning and execution.
It uses a fallback chain: LLM Decomposer (optional) -> Rule Decomposer (default)
-> No-split fallback.

Python 2.7 compatible.
"""

import copy
import uuid

from task_types import DESCRIPTIVE, ANOMALY, ATTRIBUTION, COMPARISON

try:  # pragma: no cover - Python 3 compatibility
    unicode
except NameError:
    unicode = str


def _new_id():
    return str(uuid.uuid4())


def _to_unicode(value):
    if value is None:
        return u""
    if isinstance(value, unicode):
        return value
    try:
        return value.decode("utf-8")
    except Exception:
        try:
            return unicode(value)
        except Exception:
            return u"%s" % value



class DecompositionResult(object):
    """Result of decomposing a plan into sub-plans."""

    def __init__(self, sub_plans=None, strategy="no_split", reason="",
                 diagnostics=None):
        self.sub_plans = sub_plans or []
        self.strategy = strategy
        self.reason = reason
        self.diagnostics = diagnostics or {}

    def to_dict(self):
        return {
            "sub_plans": [p.to_dict() if hasattr(p, "to_dict") else dict(p)
                          for p in self.sub_plans],
            "strategy": self.strategy,
            "reason": self.reason,
            "diagnostics": dict(self.diagnostics),
            "sub_plan_count": len(self.sub_plans),
        }


class RuleDecomposer(object):
    """Deterministic rule-based plan decomposer.

    Rules (applied in order):
        1. Multi-metric split: metrics > max_metrics
        2. Multi-dimension split: dimensions > max_dimensions
        3. Multi-intent split: query contains multiple intent keywords
        4. Overview expansion: intent=metric_query with 2+ metrics
        5. Time range split: multiple time ranges detected
    """

    def __init__(self, max_metrics=3, max_dimensions=2):
        self.max_metrics = max_metrics
        self.max_dimensions = max_dimensions

    def decompose(self, plan, query=None):
        """Apply rule-based decomposition.

        Args:
            plan: AnalysisPlan or dict
            query: original user query string

        Returns:
            DecompositionResult or None (if no decomposition needed)
        """
        pd = plan.to_dict() if hasattr(plan, "to_dict") else dict(plan)
        query = _to_unicode(query or pd.get("query", ""))
        metrics = pd.get("metrics") or []
        if pd.get("metric") and pd["metric"] not in metrics:
            metrics.append(pd["metric"])
        dimensions = pd.get("dimensions") or []
        intent = pd.get("intent", "")
        task_type = pd.get("task_type", DESCRIPTIVE)

        # Rule 1: Multi-metric split
        if len(metrics) > self.max_metrics:
            return self._split_by_metric(pd, metrics, query)

        # Rule 4: Overview expansion (metric_query with 2+ metrics)
        if intent == "metric_query" and len(metrics) >= 2:
            return self._expand_overview(pd, metrics, dimensions, query)

        # Rule 2: Multi-dimension split
        if len(dimensions) > self.max_dimensions:
            return self._split_by_dimension(pd, dimensions, query)

        # Rule 3: Multi-intent split
        multi_intent = self._detect_multi_intent(query, intent)
        if multi_intent:
            return self._split_by_intent(pd, query, multi_intent)

        # No decomposition needed
        return None

    def _detect_multi_intent(self, query, current_intent):
        """Detect if query contains multiple intents.

        Returns list of (intent, task_type, keywords) tuples, or None.
        """
        query = _to_unicode(query).lower()
        intents = []

        # Check for anomaly intent keywords
        anomaly_kw = [u"为什么", u"原因", u"异常", u"突然", u"波动",
                      u"暴跌", u"暴涨", u"骤降", u"骤升"]
        if any(kw in query for kw in anomaly_kw):
            intents.append(("anomaly", ANOMALY, anomaly_kw))

        # Check for attribution intent keywords
        attribution_kw = [u"贡献", u"归因", u"占比", u"帕累托",
                          u"主要", u"哪个"]
        if any(kw in query for kw in attribution_kw):
            intents.append(("attribution", ATTRIBUTION, attribution_kw))

        # Check for comparison intent keywords
        comparison_kw = [u"对比", u"比较", u"同比", u"环比", u"vs"]
        if any(kw in query for kw in comparison_kw):
            intents.append(("comparison", COMPARISON, comparison_kw))

        # Check for breakdown intent keywords
        breakdown_kw = [u"按", u"各", u"分"]
        if any(kw in query for kw in breakdown_kw):
            intents.append(("breakdown", DESCRIPTIVE, breakdown_kw))

        # Filter out the current intent
        other_intents = [(i, t, k) for i, t, k in intents if i != current_intent]

        # Only trigger if there are 2+ distinct intents
        if len(other_intents) >= 1 and len(set([i[0] for i in intents])) >= 2:
            return intents
        return None

    def _split_by_metric(self, pd, metrics, query):
        """Split by metric: one sub-plan per metric."""
        sub_plans = []
        for metric in metrics:
            sub = dict(pd)
            sub["metric"] = metric
            sub["metrics"] = [metric]
            sub["multi_metric"] = False
            sub["task_id"] = _new_id()
            sub["parent_task_id"] = pd.get("task_id")
            sub["decompose_strategy"] = "rule"
            sub["decompose_reason"] = "multi_metric_split"
            sub_plans.append(sub)

        return DecompositionResult(
            sub_plans=sub_plans,
            strategy="rule",
            reason="multi_metric_split: %d metrics > max %d" % (
                len(metrics), self.max_metrics),
            diagnostics={
                "rule": "multi_metric",
                "original_metrics": metrics,
                "sub_plan_count": len(sub_plans),
            },
        )

    def _split_by_dimension(self, pd, dimensions, query):
        """Split by dimension: one sub-plan per dimension."""
        sub_plans = []
        for dim in dimensions:
            sub = dict(pd)
            sub["dimensions"] = [dim]
            sub["task_id"] = _new_id()
            sub["parent_task_id"] = pd.get("task_id")
            sub["decompose_strategy"] = "rule"
            sub["decompose_reason"] = "multi_dimension_split"
            sub_plans.append(sub)

        return DecompositionResult(
            sub_plans=sub_plans,
            strategy="rule",
            reason="multi_dimension_split: %d dimensions > max %d" % (
                len(dimensions), self.max_dimensions),
            diagnostics={
                "rule": "multi_dimension",
                "original_dimensions": dimensions,
                "sub_plan_count": len(sub_plans),
            },
        )

    def _expand_overview(self, pd, metrics, dimensions, query):
        """Expand overview query into overview + breakdown sub-plans."""
        sub_plans = []

        # Sub-plan 1: overview (all metrics, no breakdown)
        overview = dict(pd)
        overview["intent"] = "metric_query"
        overview["dimensions"] = []
        overview["task_id"] = _new_id()
        overview["parent_task_id"] = pd.get("task_id")
        overview["decompose_strategy"] = "rule"
        overview["decompose_reason"] = "overview_expansion"
        sub_plans.append(overview)

        # Sub-plan 2+: breakdown by first dimension (if any) or channel
        breakdown_dims = dimensions[:1] if dimensions else ["channel"]
        for metric in metrics:
            breakdown = dict(pd)
            breakdown["intent"] = "breakdown"
            breakdown["metric"] = metric
            breakdown["metrics"] = [metric]
            breakdown["dimensions"] = list(breakdown_dims)
            breakdown["multi_metric"] = False
            breakdown["task_id"] = _new_id()
            breakdown["parent_task_id"] = pd.get("task_id")
            breakdown["decompose_strategy"] = "rule"
            breakdown["decompose_reason"] = "overview_expansion"
            sub_plans.append(breakdown)

        return DecompositionResult(
            sub_plans=sub_plans,
            strategy="rule",
            reason="overview_expansion: %d metrics with overview + breakdown" % len(metrics),
            diagnostics={
                "rule": "overview_expansion",
                "original_metrics": metrics,
                "breakdown_dimensions": breakdown_dims,
                "sub_plan_count": len(sub_plans),
            },
        )

    def _split_by_intent(self, pd, query, intents):
        """Split by intent: one sub-plan per detected intent."""
        sub_plans = []
        metrics = pd.get("metrics") or [pd.get("metric", "gmv")]
        dimensions = pd.get("dimensions") or []

        for intent, task_type, keywords in intents:
            sub = dict(pd)
            sub["intent"] = intent
            sub["task_type"] = task_type
            sub["task_id"] = _new_id()
            sub["parent_task_id"] = pd.get("task_id")
            sub["decompose_strategy"] = "rule"
            sub["decompose_reason"] = "multi_intent_split"
            # Keep first metric for each sub-plan
            sub["metric"] = metrics[0] if metrics else "gmv"
            sub["metrics"] = [sub["metric"]]
            sub["multi_metric"] = False
            sub_plans.append(sub)

        return DecompositionResult(
            sub_plans=sub_plans,
            strategy="rule",
            reason="multi_intent_split: %d intents detected" % len(intents),
            diagnostics={
                "rule": "multi_intent",
                "intents": [i[0] for i in intents],
                "sub_plan_count": len(sub_plans),
            },
        )


class TaskDecomposer(object):
    """Main decomposer with fallback chain.

    Fallback chain:
        1. LLM Decomposer (optional, configurable)
        2. Rule Decomposer (default, deterministic)
        3. No-split fallback (pass through)
    """

    def __init__(self, max_metrics=3, max_dimensions=2,
                 use_llm=False, llm_decomposer=None):
        self.max_metrics = max_metrics
        self.max_dimensions = max_dimensions
        self.use_llm = use_llm
        self.llm_decomposer = llm_decomposer
        self.rule_decomposer = RuleDecomposer(
            max_metrics=max_metrics,
            max_dimensions=max_dimensions,
        )

    def decompose(self, plan, query=None, trace_id=None):
        """Decompose a plan into sub-plans with fallback chain.

        Args:
            plan: AnalysisPlan or dict
            query: original user query string
            trace_id: optional trace ID for observability

        Returns:
            DecompositionResult
        """
        pd = plan.to_dict() if hasattr(plan, "to_dict") else dict(plan)

        # Skip decomposition for terminal statuses
        if pd.get("status") in ("blocked", "need_clarification",
                                 "unsupported", "pending_human_review",
                                 "error"):
            return DecompositionResult(
                sub_plans=[plan],
                strategy="no_split",
                reason="terminal_status: %s" % pd.get("status"),
                diagnostics={"status": pd.get("status")},
            )

        # Fallback chain level 1: LLM Decomposer (optional)
        if self.use_llm and self.llm_decomposer:
            try:
                result = self.llm_decomposer.decompose(plan, query)
                if result and result.sub_plans:
                    result.strategy = "llm"
                    result.diagnostics["fallback_level"] = 1
                    return result
            except Exception:
                pass  # Fall through to rule decomposer

        # Fallback chain level 2: Rule Decomposer (default)
        try:
            result = self.rule_decomposer.decompose(plan, query)
            if result is not None:
                result.diagnostics["fallback_level"] = 2
                return result
        except Exception:
            pass  # Fall through to no-split

        # Fallback chain level 3: No-split fallback (safest)
        return DecompositionResult(
            sub_plans=[plan],
            strategy="no_split",
            reason="fallback: no decomposition needed or possible",
            diagnostics={
                "fallback_level": 3,
                "max_metrics": self.max_metrics,
                "max_dimensions": self.max_dimensions,
            },
        )


__all__ = [
    "DecompositionResult",
    "RuleDecomposer",
    "TaskDecomposer",
]
