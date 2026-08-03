# -*- coding: utf-8 -*-
"""Execution engine for the Data Agent mainline.

This module owns the execution closed loop:
AnalysisPlan -> runtime SQL -> validation -> readonly execution -> retry -> diagnostics.
It is intentionally lightweight and Python 2.7 compatible.
"""

import hashlib
import time
from db_adapter import ReadonlyQueryExecutor
from sql_preflight import validate_sql_preflight
from contracts import build_execution_envelope

from execution_strategies import StrategyCompileResult, get_execution_strategy
from metadata_catalog import build_metadata_catalog
from metric_sql_compiler import compile_metric_sql
from semantic_registry import validate_plan_semantics
from task_capabilities import validate_plan_capabilities
from task_types import DESCRIPTIVE, is_supported_execution

try:
    from data_quality import quick_check
except Exception:  # keep execution engine usable when optional deps are absent
    quick_check = None


class ExecutionEngine(object):
    """Build and execute SQL from a normalized AnalysisPlan."""

    def __init__(self, executor=None, runtime_factory=None, validator=None, max_retries=1, observer=None):
        self.executor = executor or ReadonlyQueryExecutor()
        self.runtime_factory = runtime_factory
        self.validator = validator
        self.max_retries = max_retries
        self.observer = observer

    def execute(self, plan, trace_id=None, task_id=None):
        runtime = None
        task_type = plan.get("task_type") or DESCRIPTIVE
        if trace_id is None:
            trace_id = getattr(self.observer, "trace_id", None)
        # Preserve the established dispatch boundary for task types without an
        # execution strategy; capability validation applies to executable work.
        if not is_supported_execution(task_type):
            self._record(
                "execute",
                trace_id,
                "error",
                task_id=task_id,
                phase="strategy_dispatch",
                failure_type="unsupported_task_type",
                task_type=task_type,
            )
            return self._error_result(
                sql=None,
                steps=[],
                phase="strategy_dispatch",
                failure_type="unsupported_task_type",
                error="unsupported task_type: %s" % task_type,
                retry_count=0,
                diagnostics={
                    "strategy": task_type,
                    "capability": validate_plan_capabilities(plan).to_dict(),
                    "retry_exhausted": False,
                    "confidence": self._confidence(status="error", retry_count=0),
                    "trace_summary": self._trace_summary(trace_id),
                },
                task_id=task_id,
                trace_id=trace_id,
            )
        capability_check = validate_plan_capabilities(plan)
        if not capability_check.ok:
            self._record(
                "execute",
                trace_id,
                "error",
                task_id=task_id,
                phase="capability_validation",
                failure_type="capability_validation_error",
                task_type=task_type,
            )
            return self._error_result(
                sql=None,
                steps=[],
                phase="capability_validation",
                failure_type="capability_validation_error",
                error="task capability validation failed",
                retry_count=0,
                diagnostics={
                    "capability": capability_check.to_dict(),
                    "retry_exhausted": False,
                    "confidence": self._confidence(status="error", retry_count=0),
                    "trace_summary": self._trace_summary(trace_id),
                },
                task_id=task_id,
                trace_id=trace_id,
            )
        try:
            semantic_check = validate_plan_semantics(plan)
            if not semantic_check.ok:
                self._record(
                    "execute",
                    trace_id,
                    "error",
                    task_id=task_id,
                    phase="semantic_validation",
                    failure_type="semantic_validation_error",
                    task_type=task_type,
                )
                return self._error_result(
                    sql=None,
                    steps=[],
                    phase="semantic_validation",
                    failure_type="semantic_validation_error",
                    error="semantic validation failed",
                    retry_count=0,
                    diagnostics={
                        "semantic": semantic_check.to_dict(),
                        "retry_exhausted": False,
                        "confidence": self._confidence(status="error", retry_count=0),
                        "trace_summary": self._trace_summary(trace_id),
                    },
                    task_id=task_id,
                    trace_id=trace_id,
                )
            physical_schema = self._describe_schema()
            metadata_catalog = build_metadata_catalog(physical_schema=physical_schema)
            runtime = self._new_runtime()
            compile_result = self._compile_plan(runtime, plan, task_type, metadata_catalog=metadata_catalog)
            sql = compile_result.sql
            runtime_trace = compile_result.trace
            dataset_count = compile_result.dataset_count
            strategy_metadata = compile_result.metadata
            strategy_metadata.setdefault("semantic", semantic_check.to_dict())
            strategy_metadata.setdefault("capability", capability_check.to_dict())
            strategy_metadata.setdefault("metadata_catalog", {
                "contract": metadata_catalog.get("contract"),
                "fingerprint": metadata_catalog.get("fingerprint"),
                "semantic_version": metadata_catalog.get("semantic_version"),
                "schema_fingerprint": metadata_catalog.get("schema_fingerprint"),
            })
            # Legacy validator intentionally requires runtime CTE/dataids. The
            # R8 compiler emits direct readonly SQL and is protected by the
            # structured preflight + metadata validation instead.
            use_legacy_validator = strategy_metadata.get("strategy") != "metric_sql_compiler"
            sql_preflight = self._preflight_sql(sql, plan=plan, metadata_catalog=metadata_catalog,
                                                use_legacy_validator=use_legacy_validator)
            ok = sql_preflight["valid"]
            reason = "; ".join(sql_preflight["errors"]) if not ok else "ok"
            if not ok:
                self._record("execute", trace_id, "error", task_id=task_id, error=reason, phase="validate_sql", failure_type="sql_validation_error")
                return self._error_result(
                    sql=sql,
                    steps=runtime_trace,
                    phase="validate_sql",
                    failure_type="sql_validation_error",
                    error=reason,
                    retry_count=0,
                    diagnostics={
                        "validation_reason": reason,
                        "sql_preflight": sql_preflight,
                        "dataset_count": dataset_count,
                        "retry_exhausted": False,
                        "confidence": self._confidence(status="error", retry_count=0),
                        "trace_summary": self._trace_summary(trace_id),
                    },
                    task_id=task_id,
                    trace_id=trace_id,
                )

            execution, retry_count, execution_error = self._execute_with_retry(sql)
            if isinstance(execution, dict) and execution.get("status") == "error":
                return self._error_result(
                    sql=execution.get("sql") or sql,
                    steps=runtime_trace,
                    phase="db_execute",
                    failure_type=execution.get("error_type") or "db_error",
                    error=execution.get("error") or "database execution failed",
                    retry_count=retry_count,
                    diagnostics={
                        "execution": execution,
                        "retry_exhausted": False,
                        "confidence": self._confidence(status="error", retry_count=retry_count),
                        "trace_summary": self._trace_summary(trace_id),
                    },
                    task_id=task_id,
                    trace_id=trace_id,
                )
            rows = execution.get("rows") if isinstance(execution, dict) else None
            quality = self._quality(rows)
            confidence = self._confidence(quality=quality, retry_count=retry_count, task_type=task_type, status="ok")
            compiler_metadata = (strategy_metadata.get("compiled_sql") or {}).get("grain_rewrite")
            diagnostics = {
                "phase": "execute",
                "stage": "execute",
                "failure_type": "empty_result" if quality.get("empty_result") else None,
                "retry_count": retry_count,
                "retry_exhausted": False,
                "validation_reason": None,
                "sql_preflight": sql_preflight,
                "execution_error": execution_error,
                "dataset_count": dataset_count,
                "quality": quality,
                "strategy": strategy_metadata.get("strategy", task_type),
                "strategy_metadata": strategy_metadata,
                "grain_rewrite": compiler_metadata,
                "confidence": confidence,
                "trace_summary": self._trace_summary(trace_id),
            }
            self._record(
                "execute",
                trace_id,
                "ok",
                task_id=task_id,
                sql_length=len(sql or ""),
                dataset_count=dataset_count,
                retry_count=retry_count,
                row_count=execution.get("row_count", 0) if isinstance(execution, dict) else 0,
            )
            row_count = execution.get("row_count", 0) if isinstance(execution, dict) else 0
            query_id = self._query_id(sql, task_id)
            evidence_id = "exec:%s" % query_id
            compiled_sql_metadata = dict(strategy_metadata.get("compiled_sql") or {})
            compiled_sql_metadata.setdefault("task_type", task_type)
            compiled_sql_metadata.setdefault("claim_scope", (plan.get("analysis_config") or {}).get("claim_scope"))
            envelope = build_execution_envelope(
                status="ok", stage="db_execute", query_id=query_id,
                evidence_id=evidence_id,
                dataid=(execution.get("dataid") if isinstance(execution, dict) else None) or plan.get("dataid"),
                data_version=(execution.get("data_version") if isinstance(execution, dict) else None) or plan.get("data_version"),
                row_count=row_count, time_range=plan.get("time_range") or plan.get("time_range_label"),
                authority="verified_execution",
                provenance={"trace_id": trace_id, "task_id": task_id, "sql_hash": query_id},
                metadata={"source": execution.get("source", "unknown") if isinstance(execution, dict) else "unknown",
                          "preflight_contract": sql_preflight.get("contract"),
                          "compiled_sql": compiled_sql_metadata,
                          "strategy": strategy_metadata.get("strategy"),
                          "task_type": task_type,
                          "claim_scope": (plan.get("analysis_config") or {}).get("claim_scope")})
            return {
                "sql": sql,
                "steps": runtime_trace,
                "status": "ok",
                "results": rows,
                "results_summary": {
                    "row_count": row_count,
                    "source": execution.get("source", "unknown") if isinstance(execution, dict) else "unknown",
                },
                "execution": execution or {},
                "execution_envelope": envelope,
                "evidence_id": evidence_id,
                "evidence_refs": [evidence_id],
                "authority": "verified_execution",
                "query_id": query_id,
                "dataid": envelope.get("dataid"),
                "data_version": envelope.get("data_version"),
                "row_count": row_count,
                "diagnostics": diagnostics,
                "task_id": task_id,
                "trace_id": trace_id,
                "task_type": task_type,
                "confidence": confidence,
            }
        except Exception as e:
            phase = "runtime_compile" if runtime is None else "execute"
            failure_type = "runtime_compile_error" if runtime is None else "retry_exhausted"
            self._record("execute", trace_id, "error", task_id=task_id, error=str(e), phase=phase, failure_type=failure_type)
            confidence = self._confidence(status="error", retry_count=max(1, self.max_retries))
            return self._error_result(
                sql=None,
                steps=getattr(runtime, "trace", []) if runtime is not None else [],
                phase=phase,
                failure_type=failure_type,
                error=str(e),
                retry_count=max(1, self.max_retries),
                diagnostics={
                    "execution_error": str(e),
                    "retry_exhausted": failure_type == "retry_exhausted",
                    "confidence": confidence,
                    "trace_summary": self._trace_summary(trace_id),
                },
                task_id=task_id,
                trace_id=trace_id,
            )

    def _new_runtime(self):
        if self.runtime_factory is not None:
            return self.runtime_factory()
        from dag_runtime import DAGAgentRuntime
        return DAGAgentRuntime()

    def _preflight_sql(self, sql, plan=None, metadata_catalog=None, use_legacy_validator=True):
        """Return the inspectable pre-execution SQL safety report."""
        validator = None
        if use_legacy_validator:
            if self.validator is not None:
                validator = self.validator
            else:
                from runtime_core import validate_sql as _legacy
                validator = _legacy
        return validate_sql_preflight(sql, validator=validator, require_runtime_cte=False,
                                      plan=plan, metadata_catalog=metadata_catalog)

    def _validate_sql(self, sql):
        """Compatibility wrapper for callers expecting ``(ok, reason)``."""
        report = self._preflight_sql(sql)
        return report["valid"], "; ".join(report["errors"]) if not report["valid"] else "ok"

    def _compile_plan(self, runtime, plan, task_type, metadata_catalog=None):
        if task_type in (DESCRIPTIVE, "comparison"):
            compiled = compile_metric_sql(plan, metadata_catalog or {})
            if compiled.ok:
                return StrategyCompileResult(
                    compiled.sql,
                    trace=[{"op": "metric_sql_compile", "contract": "compiled_sql_v1",
                            "fingerprint": compiled.metadata.get("fingerprint"),
                            "grain_rewrite": compiled.metadata.get("grain_rewrite")}],
                    dataset_count=1,
                    metadata={"strategy": "metric_sql_compiler", "compiled_sql": compiled.to_dict()},
                )
        strategy = get_execution_strategy(task_type)
        if strategy is None:
            raise ValueError("No execution strategy registered for task_type: %s" % task_type)
        result = strategy.compile(runtime, plan)
        result.metadata.setdefault("metric_sql_compiler", {"used": False, "fallback": True})
        return result

    def _describe_schema(self):
        try:
            if self.executor is not None and hasattr(self.executor, "describe_schema"):
                return self.executor.describe_schema()
        except Exception:
            return {}
        return {}

    def _compile_sql(self, runtime, plan):
        model = plan.get("model", "order_detail")
        metric = plan.get("metric", "gmv")
        dimensions = plan.get("dimensions", []) or []
        time_range = plan.get("time_range")

        did = runtime.switch(model)
        if time_range and isinstance(time_range, (list, tuple)) and len(time_range) == 2:
            did = runtime.filter_time_and_defaults(did, metric, time_range[0], time_range[1])
        did = runtime.aggregate(did, metric, dimensions)
        if dimensions:
            did = runtime.sort(did, by=metric, order="DESC")

        sql = runtime.compile_sql(did)
        return sql, getattr(runtime, "trace", []), len(getattr(runtime, "datasets", {}) or {})

    def _execute_with_retry(self, sql):
        execution = None
        execution_error = None
        retry_count = 0
        attempts = int(self.max_retries or 0) + 1
        t0 = time.time()
        for attempt in range(attempts):
            try:
                execution = self.executor.execute(sql)
                elapsed_ms = (time.time() - t0) * 1000
                # Slow query detection
                SLOW_QUERY_THRESHOLD_MS = 5000
                if elapsed_ms > SLOW_QUERY_THRESHOLD_MS:
                    self._record(
                        "slow_query",
                        getattr(self.observer, "trace_id", None),
                        "warning",
                        elapsed_ms=elapsed_ms,
                        sql_length=len(sql or ""),
                        retry_count=retry_count,
                    )
                return execution, retry_count, execution_error
            except Exception as e:
                execution_error = str(e)
                retry_count = attempt + 1
                if attempt >= attempts - 1:
                    raise
        return execution, retry_count, execution_error


    def _quality(self, rows):
        rows = rows or []
        if quick_check is None:
            return {
                "status": "unknown",
                "empty_result": len(rows) == 0,
                "messages": ["quality checker unavailable"],
                "score": None,
                "checks": [],
            }
        report = quick_check(rows)
        return {
            "status": getattr(report, "status", "unknown"),
            "empty_result": len(rows) == 0,
            "messages": list(getattr(report, "messages", []) or []),
            "score": getattr(report, "score", None),
            "checks": list(getattr(report, "checks", []) or []),
        }

    def _confidence(self, quality=None, retry_count=0, task_type=None, status="ok"):
        quality = quality or {}
        score = quality.get("score")
        if score is None:
            score = 0.75 if not quality.get("empty_result") else 0.5
        confidence = float(score)
        if retry_count:
            confidence -= min(0.15, 0.05 * int(retry_count))
        if quality.get("empty_result"):
            confidence -= 0.2
        if status != "ok":
            confidence -= 0.2
        if task_type in ("comparison", "attribution") and confidence < 0.6:
            confidence = 0.6
        if confidence < 0.0:
            confidence = 0.0
        if confidence > 1.0:
            confidence = 1.0
        return round(confidence, 2)

    def _query_id(self, sql, task_id=None):
        raw = "%s|%s" % (task_id or "", sql or "")
        try:
            raw = raw.encode("utf-8")
        except Exception:
            pass
        return hashlib.sha1(raw).hexdigest()[:16]

    def _error_result(self, sql, steps, phase, failure_type, error, retry_count, diagnostics=None, task_id=None, trace_id=None):
        details = diagnostics or {}
        details.setdefault("retry_count", retry_count)
        details.setdefault("phase", phase)
        details.setdefault("stage", phase)
        details.setdefault("failure_type", failure_type)
        if trace_id is not None:
            details.setdefault("trace_summary", self._trace_summary(trace_id))
        query_id = self._query_id(sql, task_id) if sql else None
        envelope = build_execution_envelope(
            status="error", stage=phase, error_code=failure_type,
            retryable=bool(failure_type in ("db_error", "retry_exhausted", "external_tool_timeout")),
            message=error, query_id=query_id, evidence_id=None,
            row_count=0, authority="unverified",
            provenance={"trace_id": trace_id, "task_id": task_id, "sql_hash": query_id},
            metadata={"retry_count": retry_count, "failure_type": failure_type})
        return {
            "sql": sql,
            "steps": steps or [],
            "status": "error",
            "errors": [{"phase": phase, "error": error, "error_code": failure_type}],
            "diagnostics": details,
            "execution_envelope": envelope,
            "authority": "unverified",
            "query_id": query_id,
            "task_id": task_id,
            "trace_id": trace_id,
            "confidence": details.get("confidence"),
        }

    def _record(self, name, trace_id, status, **payload):
        if self.observer is not None:
            self.observer.record(name, trace_id=trace_id, status=status, **payload)

    def _trace_summary(self, trace_id):
        if self.observer is None or trace_id is None:
            return None
        summarize = getattr(self.observer, "summarize", None)
        if summarize is None:
            return None
        try:
            return summarize(trace_id)
        except Exception:
            return None


__all__ = ["ExecutionEngine"]
