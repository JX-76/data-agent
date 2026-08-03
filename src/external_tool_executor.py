# -*- coding: utf-8 -*-
"""Governed executor for external interaction tools.

All external calls go through: registry -> policy -> timeout/circuit -> execute -> trace.
"""
from __future__ import unicode_literals

import hashlib
import os
import time

from circuit_breaker import CircuitBreakerOpenError, get_circuit_breaker_registry
from contracts import build_execution_envelope
from db_adapter import MockDBAdapter, ReadonlyQueryExecutor
from external_tool_policy import ExternalToolPolicy
from external_tool_registry import get_external_tool_registry
from external_tool_trace import ExternalToolTraceRecorder
from phase3a_runtime import build_tool_invocation_plan, build_dag_trace_event
from observability import get_observer
from semantic_registry import get_semantic_registry
from timeout_guard import TimeoutGuard
from rag_governance import IdempotencyKeyBuilder

try:
    basestring
except NameError:  # pragma: no cover
    basestring = str


BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ExternalToolExecutionResult(object):
    def __init__(self, status="ok", tool_id=None, data=None, diagnostics=None, trace_event=None, execution_envelope=None):
        self.status = status
        self.tool_id = tool_id
        self.data = data or {}
        self.diagnostics = diagnostics or {}
        self.trace_event = trace_event or {}
        self.execution_envelope = execution_envelope or build_execution_envelope(
            status=status, stage="external_tool", error_code=(self.diagnostics or {}).get("failure_type"),
            retryable=status == "error", message=(self.diagnostics or {}).get("error"),
            tool_call_id=self._tool_call_id(tool_id, self.trace_event),
            evidence_id=self._evidence_id(status, tool_id, self.trace_event),
            row_count=self._row_count(self.data),
            authority="verified_execution" if status == "ok" else "unverified",
            provenance={"trace_id": self.trace_event.get("trace_id"), "tool_id": tool_id},
            metadata={"tool_id": tool_id})

    def _tool_call_id(self, tool_id, trace_event):
        raw = "%s|%s" % (tool_id or "tool", (trace_event or {}).get("event_id") or (trace_event or {}).get("started_at") or "")
        try:
            raw = raw.encode("utf-8")
        except Exception:
            pass
        return "tool:%s" % hashlib.sha1(raw).hexdigest()[:16]

    def _evidence_id(self, status, tool_id, trace_event):
        if status != "ok":
            return None
        return "ev:%s" % self._tool_call_id(tool_id, trace_event)

    def _row_count(self, data):
        if isinstance(data, dict):
            if data.get("row_count") is not None:
                return data.get("row_count")
            if isinstance(data.get("rows"), list):
                return len(data.get("rows"))
            if isinstance(data.get("items"), list):
                return len(data.get("items"))
        return 0

    def to_dict(self):
        return {
            "status": self.status,
            "tool_id": self.tool_id,
            "data": self.data,
            "diagnostics": self.diagnostics,
            "trace_event": self.trace_event,
            "execution_envelope": self.execution_envelope,
            "evidence_id": self.execution_envelope.get("evidence_id"),
            "evidence_refs": [self.execution_envelope.get("evidence_id")] if self.execution_envelope.get("evidence_id") else [],
            "authority": self.execution_envelope.get("authority"),
        }


class ExternalToolExecutor(object):
    def __init__(self, registry=None, policy=None, db_executor=None, observer=None, trace_recorder=None):
        self.registry = registry or get_external_tool_registry()
        self.policy = policy or ExternalToolPolicy()
        self.db_executor = db_executor or ReadonlyQueryExecutor(MockDBAdapter())
        self.observer = observer or get_observer()
        self.trace_recorder = trace_recorder or ExternalToolTraceRecorder(observer=self.observer)
        self.breakers = get_circuit_breaker_registry()

    def call(self, tool_id, args=None, context=None):
        args = args or {}
        context = context or {}
        trace_id = context.get("trace_id")
        spec = self.registry.get(tool_id)
        started = time.time()
        if spec is None:
            return self._fail(tool_id, args, context, {}, started, "external_tool_not_found", "tool not registered")

        idempotency_key = context.get("idempotency_key") or self._idempotency_key(tool_id, args, context)
        context["idempotency_key"] = idempotency_key
        policy_result = self.policy.validate(spec, args, context=context)
        policy_dict = policy_result.to_dict()
        safe_args = self.policy.sanitize_args(spec, args, context)
        tool_plan = build_tool_invocation_plan(tool_id, safe_args, spec, context, policy_dict)
        if not policy_result.allowed:
            event = self.trace_recorder.record(trace_id, tool_id, "blocked", args=safe_args, output={}, spec=spec,
                                               policy=policy_dict, started_at=started,
                                               error="; ".join(policy_result.errors),
                                               failure_type=policy_result.failure_type,
                                               tool_plan=tool_plan,
                                               dag_event=build_dag_trace_event("tool_call", "blocked", policy_result.failure_type, {"tool_id": tool_id}))
            return ExternalToolExecutionResult(
                status="blocked", tool_id=tool_id, data={},
                diagnostics={"failure_type": policy_result.failure_type, "errors": policy_result.errors, "policy": policy_dict,
                              "tool_invocation_plan": tool_plan, "idempotency_key": idempotency_key},
                trace_event=event,
            ).to_dict()

        breaker = self.breakers.get("external:%s" % tool_id, failure_threshold=3, recovery_timeout=30.0)
        try:
            with TimeoutGuard((spec.get("timeout_ms") or 1000) / 1000.0, tool_id) as guard:
                data = self._call_with_retries(breaker, tool_id, args, spec, context)
            if guard.timed_out:
                return self._fail(tool_id, args, context, spec, started, "external_tool_timeout", "tool timeout")
            output_policy = self.policy.validate_output(spec, data)
            if not output_policy.allowed:
                output_policy_dict = output_policy.to_dict()
                safe_data = self.policy.sanitize_output(spec, data, context)
                event = self.trace_recorder.record(trace_id, tool_id, "error", args=safe_args, output=safe_data, spec=spec,
                                                   policy=output_policy_dict, started_at=started,
                                                   error="; ".join(output_policy.errors),
                                                   failure_type=output_policy.failure_type,
                                                   tool_plan=tool_plan,
                                                   dag_event=build_dag_trace_event("tool_call", "error", output_policy.failure_type, {"tool_id": tool_id}))
                return ExternalToolExecutionResult(
                    status="error", tool_id=tool_id, data=safe_data,
                    diagnostics={"failure_type": output_policy.failure_type, "errors": output_policy.errors, "policy": output_policy_dict,
                                  "tool_invocation_plan": tool_plan, "idempotency_key": idempotency_key},
                    trace_event=event,
                ).to_dict()
            safe_data = self.policy.sanitize_output(spec, data, context)
            event = self.trace_recorder.record(trace_id, tool_id, "ok", args=safe_args, output=safe_data, spec=spec,
                                               policy=policy_dict, started_at=started,
                                               tool_plan=tool_plan,
                                               dag_event=build_dag_trace_event("tool_call", "ok", None, {"tool_id": tool_id}))
            return ExternalToolExecutionResult(status="ok", tool_id=tool_id, data=safe_data,
                                               diagnostics={"policy": policy_dict, "tool_invocation_plan": tool_plan, "idempotency_key": idempotency_key}, trace_event=event).to_dict()
        except CircuitBreakerOpenError as exc:
            return self._fail(tool_id, args, context, spec, started, "external_tool_circuit_open", str(exc), policy_dict)
        except Exception as exc:
            return self._fail(tool_id, args, context, spec, started, "external_tool_execution_error", str(exc), policy_dict)

    def _idempotency_key(self, tool_id, args, context):
        tenant = (context or {}).get("tenant_id") or ((context or {}).get("access_context") or {}).get("tenant_id") if isinstance((context or {}).get("access_context"), dict) else "default"
        try:
            built = IdempotencyKeyBuilder().build(tenant_id=tenant, stage="external_tool:%s" % tool_id,
                                                  input_value=args or {}, policy_version="external_tool_policy_v1")
            return built.get("idempotency_key")
        except Exception:
            raw = "%s|%s|%s" % (tool_id, repr(sorted((args or {}).items())), tenant)
            try:
                raw = raw.encode("utf-8")
            except Exception:
                pass
            return "idem:%s" % hashlib.sha1(raw).hexdigest()[:24]

    def _call_with_retries(self, breaker, tool_id, args, spec, context):
        attempts = int(spec.get("retry_attempts") or 1)
        attempts = max(1, min(attempts, 3))
        last_exc = None
        for attempt in range(attempts):
            try:
                context["attempt"] = attempt + 1
                return breaker.call(self._execute_impl, tool_id, args, spec, context)
            except CircuitBreakerOpenError:
                raise
            except Exception as exc:
                last_exc = exc
                if attempt >= attempts - 1:
                    raise
                time.sleep(float(spec.get("retry_backoff_ms") or 10) / 1000.0)
        raise last_exc

    def _execute_impl(self, tool_id, args, spec, context):
        if tool_id == "semantic.catalog_read":
            registry = get_semantic_registry()
            payload = registry.get()
            return {
                "semantic_version": registry.get_version(),
                "metrics": sorted((payload.get("metrics") or {}).keys()),
                "dimensions": sorted((payload.get("dimensions") or {}).keys()),
                "models": sorted((payload.get("models") or {}).keys()),
            }
        if tool_id == "warehouse.schema_introspect":
            return {"schema": self.db_executor.describe_schema()}
        if tool_id == "warehouse.query_sql":
            return self.db_executor.execute(args.get("sql"), limit=args.get("limit"), offset=args.get("offset", 0))
        if tool_id == "ecommerce.overview":
            metric = args.get("metric") or "gmv"
            return {"metric": metric, "current": 80000, "baseline": 100000, "delta_pct": -0.20,
                    "time_range": args.get("time_range") or "last_7_days"}
        if tool_id == "ecommerce.channel_performance":
            return {"rows": [{"channel": "paid_search", "gmv": 30000, "delta_pct": -0.35},
                              {"channel": "organic", "gmv": 50000, "delta_pct": -0.05}], "row_count": 2}
        if tool_id == "ecommerce.product_performance":
            return {"rows": [{"product_id": "sku_001", "gmv": 18000, "delta_pct": -0.42},
                              {"product_id": "sku_002", "gmv": 12000, "delta_pct": -0.10}], "row_count": 2}
        if tool_id == "ecommerce.review_sentiment":
            return {"negative_rate": 0.18, "top_terms": ["物流慢", "尺码偏小"], "ttl_seconds": 86400}
        if tool_id == "ecommerce.competitor_price":
            return {"items": [{"competitor": "peer_a", "price_index": 0.92}], "ttl_seconds": 3600}
        if tool_id == "harness.run_suite":
            return self._run_harness_suite(args.get("suite"))
        raise ValueError("unsupported external tool: %s" % tool_id)

    def _run_harness_suite(self, suite):
        # Avoid importing AgentHarness at module import time to prevent cycles.
        from agent_harness import AgentHarness
        harness = AgentHarness()
        if not suite or suite == "base":
            path = os.path.join(BASE, "harness", "cases", "base.jsonl")
        elif suite == "phase8":
            path = os.path.join(BASE, "harness", "cases", "phase8.jsonl")
        elif suite == "external_tools":
            path = os.path.join(BASE, "harness", "cases", "external_tools.jsonl")
        else:
            path = suite
            if not os.path.isabs(path):
                path = os.path.join(BASE, path)
        cases = harness.load_cases(path)
        results = harness.run_cases(cases)
        return {"suite": suite, "metrics": harness.summarize(results), "result_count": len(results)}

    def _fail(self, tool_id, args, context, spec, started, failure_type, error, policy=None):
        event = self.trace_recorder.record(context.get("trace_id"), tool_id, "error", args=self.policy.sanitize_args(spec, args, context), output={}, spec=spec,
                                           policy=policy or {}, started_at=started, error=error,
                                           failure_type=failure_type)
        return ExternalToolExecutionResult(
            status="error", tool_id=tool_id, data={},
                 diagnostics={"failure_type": failure_type, "error": error, "idempotency_key": context.get("idempotency_key")}, trace_event=event,
        ).to_dict()


__all__ = ["ExternalToolExecutor", "ExternalToolExecutionResult"]
