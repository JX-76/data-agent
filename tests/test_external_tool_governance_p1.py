# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from external_tool_executor import ExternalToolExecutor
from external_tool_registry import ExternalToolRegistry
from observability import ObservationRecorder


class EchoExecutor(ExternalToolExecutor):
    def __init__(self, *args, **kwargs):
        ExternalToolExecutor.__init__(self, *args, **kwargs)
        self.calls = 0

    def _execute_impl(self, tool_id, args, spec, context):
        self.calls += 1
        if tool_id == "test.echo_pii":
            return {"rows": [{"email": args.get("email"), "phone": "13812345678", "value": 1}], "row_count": 1}
        if tool_id == "test.flaky":
            if self.calls < 2:
                raise RuntimeError("temporary upstream error")
            return {"rows": [{"value": 1}], "row_count": 1}
        if tool_id == "test.always_fail":
            raise RuntimeError("upstream down")
        if tool_id == "test.strict_nested":
            return {"rows": [{"ok": True}], "row_count": 1}
        if tool_id == "test.undeclared_pii_output":
            return {"rows": [{"customer_email": "alice@example.com"}], "row_count": 1}
        return ExternalToolExecutor._execute_impl(self, tool_id, args, spec, context)


def _registry():
    return ExternalToolRegistry(config_path="missing-file.yaml", tools=[
        {
            "tool_id": "test.echo_pii",
            "category": "test",
            "input_schema": {"required": ["email"], "properties": {"email": {"type": "string", "min_len": 1}}},
            "output_schema": {"required": ["rows", "row_count"]},
            "timeout_ms": 1000,
            "side_effect": "read_only",
            "allowed_intents": ["metric_query"],
            "idempotent": True,
            "masked_input_fields": ["email"],
            "masked_output_fields": ["email", "phone"],
            "permission_plan": {"model": "orders", "fields": ["email"]},
        },
        {
            "tool_id": "test.flaky",
            "category": "test",
            "input_schema": {"required": [], "properties": {}},
            "output_schema": {"required": ["rows", "row_count"]},
            "timeout_ms": 1000,
            "side_effect": "read_only",
            "allowed_intents": ["metric_query"],
            "idempotent": True,
            "retry_attempts": 2,
            "retry_backoff_ms": 1,
        },
        {
            "tool_id": "test.always_fail",
            "category": "test",
            "input_schema": {"required": [], "properties": {}},
            "output_schema": {"required": ["rows", "row_count"]},
            "timeout_ms": 1000,
            "side_effect": "read_only",
            "allowed_intents": ["metric_query"],
            "idempotent": True,
        },
        {
            "tool_id": "test.strict_nested",
            "category": "test",
            "input_schema": {
                "required": ["payload"],
                "additionalProperties": False,
                "properties": {
                    "payload": {
                        "type": "object",
                        "required": ["metric", "filters"],
                        "additionalProperties": False,
                        "properties": {
                            "metric": {"type": "string", "enum": ["gmv", "orders"]},
                            "filters": {
                                "type": "array",
                                "min_items": 1,
                                "max_items": 3,
                                "items": {
                                    "type": "object",
                                    "required": ["field", "value"],
                                    "additionalProperties": False,
                                    "properties": {
                                        "field": {"type": "string", "pattern": "^[a-z_]+$"},
                                        "value": {"type": "string", "min_len": 1},
                                    },
                                },
                            },
                        },
                    }
                },
            },
            "output_schema": {"required": ["rows", "row_count"], "properties": {"rows": {"type": "array"}, "row_count": {"type": "integer"}}},
            "timeout_ms": 1000,
            "side_effect": "read_only",
            "allowed_intents": ["metric_query"],
            "idempotent": True,
        },
        {
            "tool_id": "test.undeclared_pii_output",
            "category": "test",
            "input_schema": {"required": [], "properties": {}},
            "output_schema": {"required": ["rows", "row_count"]},
            "timeout_ms": 1000,
            "side_effect": "read_only",
            "allowed_intents": ["metric_query"],
            "idempotent": True,
        },
    ])


def test_external_tool_masks_args_output_and_trace_for_privileged_pii():
    observer = ObservationRecorder()
    executor = EchoExecutor(registry=_registry(), observer=observer)
    result = executor.call(
        "test.echo_pii",
        {"email": "alice@example.com"},
        {"intent": "metric_query", "trace_id": "t_pii", "access_context": {"role": "admin", "tenant_id": "tenant-a"}, "query": "导出 email 明细"},
    )

    assert result["status"] == "ok"
    text = repr(result)
    assert "alice@example.com" not in text
    assert "13812345678" not in text
    assert result["diagnostics"]["idempotency_key"].startswith("idem:")
    event_text = repr(observer.events_as_dicts("t_pii"))
    assert "alice@example.com" not in event_text
    assert "13812345678" not in event_text


def test_external_tool_blocks_sensitive_access_without_privileged_role():
    executor = EchoExecutor(registry=_registry())
    result = executor.call(
        "test.echo_pii",
        {"email": "alice@example.com"},
        {"intent": "metric_query", "access_context": {"role": "viewer"}, "query": "导出 email 明细"},
    )

    assert result["status"] == "blocked"
    assert result["execution_envelope"]["authority"] == "unverified"
    assert result["execution_envelope"]["evidence_id"] is None
    assert result["diagnostics"]["failure_type"] == "external_tool_human_review_required"


def test_external_tool_retry_uses_same_idempotency_key_and_succeeds():
    executor = EchoExecutor(registry=_registry())
    context = {"intent": "metric_query", "tenant_id": "tenant-a"}
    result = executor.call("test.flaky", {}, context)

    assert result["status"] == "ok"
    assert executor.calls == 2
    assert result["diagnostics"]["idempotency_key"] == context["idempotency_key"]
    assert result["execution_envelope"]["authority"] == "verified_execution"


def test_external_tool_circuit_open_returns_unverified_error_envelope():
    executor = EchoExecutor(registry=_registry())
    for _ in range(3):
        result = executor.call("test.always_fail", {}, {"intent": "metric_query"})
        assert result["status"] == "error"
    result = executor.call("test.always_fail", {}, {"intent": "metric_query"})

    assert result["status"] == "error"
    assert result["diagnostics"]["failure_type"] == "external_tool_circuit_open"
    assert result["execution_envelope"]["authority"] == "unverified"
    assert result["execution_envelope"]["evidence_id"] is None


def test_external_tool_strict_nested_schema_blocks_unknown_and_bad_nested_values():
    executor = EchoExecutor(registry=_registry())
    result = executor.call(
        "test.strict_nested",
        {"payload": {"metric": "profit", "filters": [{"field": "bad-field", "value": "x", "extra": "no"}]}, "unexpected": True},
        {"intent": "metric_query"},
    )

    assert result["status"] == "blocked"
    assert result["diagnostics"]["failure_type"] == "external_tool_contract_error"
    errors = ";".join(result["diagnostics"]["errors"])
    assert "unknown_arg: unexpected" in errors
    assert "arg_enum_violation: payload.metric" in errors
    assert "arg_pattern_violation: payload.filters[0].field" in errors
    assert "unknown_arg: payload.filters[0].extra" in errors
    assert result["execution_envelope"]["evidence_id"] is None


def test_external_tool_blocks_undeclared_sensitive_output_before_evidence():
    executor = EchoExecutor(registry=_registry())
    result = executor.call("test.undeclared_pii_output", {}, {"intent": "metric_query"})

    assert result["status"] == "error"
    assert result["diagnostics"]["failure_type"] == "external_tool_output_contract_error"
    assert "unmasked_sensitive_output" in result["diagnostics"]["errors"]
    assert result["execution_envelope"]["authority"] == "unverified"
    assert result["execution_envelope"]["evidence_id"] is None
    assert "alice@example.com" not in repr(result)
