# -*- coding: utf-8 -*-
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))

from strategy_registry import StrategyRegistry, get_strategy_registry


def test_strategy_registry_has_core_strategies():
    registry = get_strategy_registry()
    names = registry.names()
    assert "clarification" in names
    assert "follow_up_detect" in names
    assert "follow_up_merge" in names
    assert "chart" in names
    assert "insight" in names


def test_strategy_registry_groups():
    registry = get_strategy_registry()
    groups = registry.groups()
    assert "routing" in groups
    assert "conversation" in groups
    assert "charting" in groups
    assert "explanation" in groups
    assert "clarification" in registry.group_names("routing")
    assert "insight" in registry.group_names("explanation")


def test_strategy_registry_register_and_resolve():
    registry = StrategyRegistry()

    def stub(x):
        return x + 1

    registry.register("stub", stub, group="custom")
    assert registry.resolve("stub", 1) == 2
    assert "stub" in registry.group_names("custom")


def test_strategy_registry_resolve_group():
    registry = StrategyRegistry()
    result = registry.resolve_group("charting", {"metric": "gmv"}, {"status": "ok"})
    assert "chart" in result
