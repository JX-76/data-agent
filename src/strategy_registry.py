# -*- coding: utf-8 -*-
"""Central registry for analysis strategies.

This module provides a single place to register and resolve strategy handlers
for clarification, follow-up, chart selection, and result explanation.
It is intentionally lightweight so the main pipeline can stay thin.
"""

from clarification_policy import build_clarification
from followup_policy import detect_follow_up, merge_context
from chart_policy import select_chart
from result_explainer import build_insight_bundle


class StrategyRegistry(object):
    def __init__(self):
        self._strategies = {
            "clarification": build_clarification,
            "follow_up_detect": detect_follow_up,
            "follow_up_merge": merge_context,
            "chart": select_chart,
            "insight": build_insight_bundle,
        }
        self._groups = {
            "routing": ["clarification", "follow_up_detect", "follow_up_merge"],
            "conversation": ["clarification", "follow_up_detect", "follow_up_merge"],
            "charting": ["chart"],
            "explanation": ["insight"],
        }

    def register(self, name, handler, group=None):
        self._strategies[name] = handler
        if group:
            self._groups.setdefault(group, [])
            if name not in self._groups[group]:
                self._groups[group].append(name)

    def get(self, name):
        return self._strategies.get(name)

    def names(self):
        return sorted(self._strategies.keys())

    def groups(self):
        return {key: list(value) for key, value in self._groups.items()}

    def group_names(self, group):
        return list(self._groups.get(group, []))

    def resolve(self, name, *args, **kwargs):
        handler = self.get(name)
        if handler is None:
            raise KeyError("Unknown strategy: %s" % name)
        return handler(*args, **kwargs)

    def resolve_group(self, group, *args, **kwargs):
        results = {}
        for name in self.group_names(group):
            results[name] = self.resolve(name, *args, **kwargs)
        return results


_default_registry = StrategyRegistry()


def get_strategy_registry():
    return _default_registry


__all__ = ["StrategyRegistry", "get_strategy_registry"]
