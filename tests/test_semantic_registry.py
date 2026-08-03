# -*- coding: utf-8 -*-
"""Tests for semantic registry lookup and plan validation."""

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, "src"))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def test_metric_metadata_shape():
    from semantic_registry import get_metric_metadata

    meta = get_metric_metadata("gmv")
    assert meta["id"] == "gmv"
    assert "allowed_dimensions" in meta
    assert "channel" in meta["allowed_dimensions"]
    assert meta["unit"] == "CNY"


def test_validate_plan_semantics_ok():
    from semantic_registry import validate_plan_semantics

    result = validate_plan_semantics({
        "model": "order_detail",
        "metric": "gmv",
        "dimensions": ["channel"],
    })
    payload = result.to_dict()
    assert payload["ok"] is True
    assert payload["metadata"]["metric"]["id"] == "gmv"
    assert payload["metadata"]["semantic_version"] == "v1"


def test_validate_plan_semantics_unknown_metric():
    from semantic_registry import validate_plan_semantics

    result = validate_plan_semantics({"model": "order_detail", "metric": "bad_metric"})
    assert result.ok is False
    assert result.errors[0]["code"] == "unknown_metric"


def test_validate_plan_semantics_dimension_not_visible_in_model():
    from semantic_registry import validate_plan_semantics

    result = validate_plan_semantics({
        "model": "order_detail",
        "metric": "gmv",
        "dimensions": ["category"],
    })
    codes = [item["code"] for item in result.errors]
    assert "dimension_not_visible_in_model" in codes


def test_attach_physical_schema_reports_schema_drift_warning():
    from semantic_registry import SemanticRegistry

    registry = SemanticRegistry()
    registry.attach_physical_schema({"order_detail": {"name": "order_detail", "columns": [{"name": "date"}]}})
    result = registry.validate_plan({"model": "order_detail", "metric": "gmv", "dimensions": ["channel"]})

    assert result.ok is True
    codes = [item["code"] for item in result.warnings]
    assert "schema_dimension_field_missing" in codes
    assert result.metadata["schema_drift_warnings"]


def test_execution_engine_blocks_semantic_error_before_runtime():
    from execution_engine import ExecutionEngine

    class _Executor(object):
        calls = 0
        def execute(self, sql):
            self.calls += 1
            return {"rows": [], "row_count": 0, "source": "fake"}

    executor = _Executor()
    engine = ExecutionEngine(executor=executor, runtime_factory=lambda: None, validator=lambda sql: (True, None), max_retries=0)
    result = engine.execute({"model": "order_detail", "metric": "missing_metric"})

    assert result["status"] == "error"
    assert result["errors"][0]["phase"] == "semantic_validation"
    assert result["diagnostics"]["failure_type"] == "semantic_validation_error"
    assert result["diagnostics"]["semantic"]["errors"][0]["code"] == "unknown_metric"
    assert executor.calls == 0


if __name__ == "__main__":
    test_metric_metadata_shape()
    test_validate_plan_semantics_ok()
    test_validate_plan_semantics_unknown_metric()
    test_validate_plan_semantics_dimension_not_visible_in_model()
    test_attach_physical_schema_reports_schema_drift_warning()
    test_execution_engine_blocks_semantic_error_before_runtime()
    print("All semantic registry tests passed!")
