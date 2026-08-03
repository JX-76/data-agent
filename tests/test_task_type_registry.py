# -*- coding: utf-8 -*-

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))


def test_default_registry_contains_required_task_types():
    from task_type_registry import get_task_type_registry

    registry = get_task_type_registry()
    names = registry.registered_task_types()
    for task_type in ["descriptive", "comparison", "attribution", "anomaly", "funnel"]:
        assert task_type in names
        definition = registry.get(task_type)
        assert definition.task_type == task_type
        assert definition.analyzer_name
        assert definition.report_template_name
        assert isinstance(definition.chart_policy, dict)
        assert isinstance(definition.fallback_policy, dict)


def test_unknown_task_type_resolves_to_descriptive_fallback():
    from task_type_registry import get_task_type_registry

    registry = get_task_type_registry()
    definition = registry.get("unknown_new_task")
    assert definition.task_type == "descriptive"
    assert registry.get_analyzer("unknown_new_task") == "descriptive"
    assert registry.get_report_template("unknown_new_task") == "descriptive"
    assert registry.get_chart_policy("unknown_new_task")["policy_id"] == "descriptive_default"


def test_analysis_strategy_registry_uses_task_type_registry_fallback():
    from analysis_strategies import AnalysisStrategyRegistry

    registry = AnalysisStrategyRegistry()
    analysis = registry.analyze(
        {"task_type": "missing_plugin", "metric": "gmv", "dimensions": ["channel"]},
        {"status": "ok", "results": [{"channel": "app", "gmv": 10, "order_count": 2}]},
    )
    assert analysis["type"] == "descriptive"
    assert analysis["summary_facts"]["row_count"] == 1


def test_agent_facade_analysis_stage_does_not_crash_for_unknown_task_type():
    from agent_facade import AgentFacade

    facade = AgentFacade()
    ctx = {
        "query": "未知插件任务",
        "plan": {"task_type": "missing_plugin", "metric": "gmv", "dimensions": ["channel"]},
        "exec_result": {"status": "ok", "results": [{"channel": "app", "gmv": 10, "order_count": 2}]},
    }
    out = facade._stage_analysis(ctx)
    assert out["analysis"]["type"] == "descriptive"
    assert out["chart"] is not None
    assert out["insight"] is not None
