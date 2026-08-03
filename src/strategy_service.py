# -*- coding: utf-8 -*-
"""Unified strategy service.

This is a thin facade over the strategy registry so higher-level entrypoints
can call grouped strategy flows without hard-coding policy selection.
"""

from strategy_registry import get_strategy_registry


class StrategyService(object):
    def __init__(self, registry=None):
        self.registry = registry or get_strategy_registry()

    def run_group(self, group, *args, **kwargs):
        return self.registry.resolve_group(group, *args, **kwargs)

    def run(self, name, *args, **kwargs):
        return self.registry.resolve(name, *args, **kwargs)

    def groups(self):
        return self.registry.groups()

    def names(self):
        return self.registry.names()


_default_service = StrategyService()


def get_strategy_service():
    return _default_service


__all__ = ["StrategyService", "get_strategy_service"]
