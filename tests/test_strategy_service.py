# -*- coding: utf-8 -*-
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))

from strategy_service import StrategyService, get_strategy_service


def test_strategy_service_groups_and_names():
    service = get_strategy_service()
    assert "routing" in service.groups()
    assert "charting" in service.groups()
    assert "clarification" in service.names()


def test_strategy_service_run_group():
    service = StrategyService()
    result = service.run_group("charting", {"metric": "gmv"}, {"status": "ok"})
    assert "chart" in result


def test_strategy_service_run_single():
    service = StrategyService()
    chart = service.run("chart", {"metric": "gmv"}, {"status": "ok"})
    assert isinstance(chart, dict)
