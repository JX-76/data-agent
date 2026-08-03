# -*- coding: utf-8 -*-
"""Tests for the Phase 6 task capability boundary."""

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, "src"))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def test_default_capabilities_are_discoverable():
    from task_capabilities import get_task_capability_registry

    names = get_task_capability_registry().names()
    assert "descriptive" in names
    assert "comparison" in names
    assert "anomaly" in names
    assert "attribution" in names
    assert "funnel" in names
    assert "retention" in names


def test_metric_is_a_hard_precondition_for_descriptive_work():
    from task_capabilities import validate_plan_capabilities

    check = validate_plan_capabilities({"task_type": "descriptive", "model": "order_detail"})
    assert check.ok is False
    assert check.errors[0]["code"] == "capability_metric_required"


def test_comparison_missing_prior_range_is_a_warning_not_blocker():
    from task_capabilities import validate_plan_capabilities

    check = validate_plan_capabilities({"task_type": "comparison", "model": "order_detail", "metric": "gmv"})
    assert check.ok is True
    assert any(item["code"] == "capability_previous_range_missing" for item in check.warnings)


def test_funnel_is_explicitly_unavailable_until_its_strategy_exists():
    from task_capabilities import validate_plan_capabilities

    check = validate_plan_capabilities({"task_type": "funnel", "model": "order_detail"})
    assert check.ok is False
    assert check.errors[0]["code"] == "task_type_not_executable"


def test_execution_engine_reports_capability_phase_before_compilation():
    from execution_engine import ExecutionEngine

    result = ExecutionEngine().execute({"task_type": "funnel", "model": "order_detail"})
    assert result["status"] == "error"
    assert result["diagnostics"]["phase"] == "strategy_dispatch"
    assert result["diagnostics"]["capability"]["errors"][0]["code"] == "task_type_not_executable"


if __name__ == "__main__":
    test_default_capabilities_are_discoverable()
    test_metric_is_a_hard_precondition_for_descriptive_work()
    test_comparison_missing_prior_range_is_a_warning_not_blocker()
    test_funnel_is_explicitly_unavailable_until_its_strategy_exists()
    test_execution_engine_reports_capability_phase_before_compilation()
    print("All task capability tests passed!")
