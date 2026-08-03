# -*- coding: utf-8 -*-
"""Compatibility layer for standardized result contracts.

New canonical dataclasses live in ``schemas.py``. This module keeps the old
imports working while the codebase migrates to the structured types.
"""

from schemas import AnalysisPlan, ClarificationRequest, ExecutionResult, InsightBundle, ensure_dict
from task_types import DESCRIPTIVE, infer_task_type
from plan_validator import enforce_analysis_plan_v2


CANONICAL_TERMINAL_STATUSES = ("ok", "need_clarification", "blocked", "error", "no_answer")
VALID_STATUSES = ("ok", "need_clarification", "blocked", "fallback", "error", "pending_human_review", "unsupported", "degraded", "no_answer")
NON_OK_TERMINAL_STATUSES = ("need_clarification", "blocked", "error", "no_answer", "fallback", "pending_human_review", "unsupported", "degraded")
RESPONSE_CONTRACT_KEYS = (
    "status", "query", "intent", "plan", "sql", "results", "results_summary",
    "analysis", "diagnostics", "report", "trace_id", "task_id", "elapsed_ms",
    "errors", "clarification", "blocked_reason", "fallback_reason", "session_id", "execution",
    "requires_human_review", "approval_status", "risk_level", "review_checklist",
    "prompt_chain", "prompt_specs", "sandbox", "human_gate", "execution_mode",
)


class ExecutionEnvelope(object):
    """Stable execution-boundary envelope for SQL/tool calls.

    The envelope is intentionally plain-dict compatible so legacy callers can
    keep reading top-level fields while new governance/audit code has one
    normalized place for status, evidence authority and failure metadata.
    """

    def __init__(self, status="ok", stage="execute", error_code=None,
                 retryable=False, message=None, query_id=None, tool_call_id=None,
                 evidence_id=None, dataid=None, data_version=None, row_count=0,
                 time_range=None, authority="unverified", provenance=None,
                 metadata=None):
        self.status = status or "error"
        self.stage = stage or "execute"
        self.error_code = error_code
        self.retryable = bool(retryable)
        self.message = message
        self.query_id = query_id
        self.tool_call_id = tool_call_id
        self.evidence_id = evidence_id
        self.dataid = dataid
        self.data_version = data_version
        self.row_count = int(row_count or 0)
        self.time_range = time_range
        self.authority = authority or "unverified"
        self.provenance = provenance or {}
        self.metadata = metadata or {}

    def to_dict(self):
        return {
            "status": self.status,
            "stage": self.stage,
            "error_code": self.error_code,
            "retryable": self.retryable,
            "message": self.message,
            "query_id": self.query_id,
            "tool_call_id": self.tool_call_id,
            "evidence_id": self.evidence_id,
            "dataid": self.dataid,
            "data_version": self.data_version,
            "row_count": self.row_count,
            "time_range": self.time_range,
            "authority": self.authority,
            "provenance": self.provenance,
            "metadata": self.metadata,
        }


def build_execution_envelope(status="ok", stage="execute", error_code=None,
                             retryable=False, message=None, query_id=None,
                             tool_call_id=None, evidence_id=None, dataid=None,
                             data_version=None, row_count=0, time_range=None,
                             authority=None, provenance=None, metadata=None):
    if authority is None:
        authority = "verified_execution" if status == "ok" and evidence_id else "unverified"
    return ExecutionEnvelope(status=status, stage=stage, error_code=error_code,
                             retryable=retryable, message=message,
                             query_id=query_id, tool_call_id=tool_call_id,
                             evidence_id=evidence_id, dataid=dataid,
                             data_version=data_version, row_count=row_count,
                             time_range=time_range, authority=authority,
                             provenance=provenance, metadata=metadata).to_dict()


class ExecutionMeta(object):
    def __init__(self, used_db=False, used_llm=False, tool_calls=0, step_count=0):
        self.used_db = used_db
        self.used_llm = used_llm
        self.tool_calls = tool_calls
        self.step_count = step_count

    def to_dict(self):
        return {
            "used_db": self.used_db,
            "used_llm": self.used_llm,
            "tool_calls": self.tool_calls,
            "step_count": self.step_count,
        }


def infer_status(termination_reason, errors):
    if errors:
        return "error"
    if termination_reason and "exceeded" in str(termination_reason):
        return "error"
    return "ok"


def normalize_status(status):
    """Map legacy aliases while preserving distinct terminal semantics."""
    if status in ("clarification_needed", "need_clarification"):
        return "need_clarification"
    if status in ("rejected", "human_rejected"):
        return "blocked"
    if status in ("failed", "insufficient_data"):
        return "error"
    if status in VALID_STATUSES:
        return status
    return status or "ok"


def _terminal_diagnostics(result):
    """Build stable terminal-state diagnostics without inventing business data."""
    status = result.get("status")
    diagnostics = result.get("diagnostics") or {}
    if not isinstance(diagnostics, dict):
        diagnostics = {}
    terminal = dict(diagnostics.get("terminal") or {})
    terminal["status"] = status
    if status == "blocked":
        terminal["reason"] = result.get("blocked_reason") or terminal.get("reason")
    elif result.get("legacy_status") == "fallback":
        terminal["reason"] = result.get("fallback_reason") or terminal.get("reason") or "fallback selected"
    elif status == "need_clarification":
        clarification = result.get("clarification") or {}
        terminal["reason"] = clarification.get("reason") if isinstance(clarification, dict) else terminal.get("reason")
    elif status == "pending_human_review":
        terminal["reason"] = result.get("approval_status") or terminal.get("reason") or "human review required"
    elif status == "no_answer":
        terminal["reason"] = result.get("fallback_reason") or result.get("blocked_reason") or result.get("reason") or terminal.get("reason") or "no verified answer available"
    elif status == "error":
        terminal["reason"] = result.get("reason") or terminal.get("reason") or "execution failed"
    diagnostics["terminal"] = terminal
    return diagnostics


def normalize_analysis_plan(plan, query=None, task_id=None, parent_task_id=None, resume_payload=None):
    """Normalize router/runtime output into the canonical AnalysisPlan contract.

    This is the single boundary for plan coercion. Entrypoints should call this
    instead of hand-filling AnalysisPlan fields in multiple places.
    """
    if isinstance(plan, AnalysisPlan):
        data = plan.to_dict()
    else:
        data = ensure_dict(plan)
        if not isinstance(data, dict):
            data = {}

    normalized_query = data.get("query") or query
    if normalized_query is None and isinstance(plan, str):
        normalized_query = plan

    diagnostics = data.get("diagnostics") or {}
    status = normalize_status(data.get("status", "ok"))
    if not normalized_query:
        status = "error"
        diagnostics = dict(diagnostics)
        diagnostics.setdefault("reason", "missing query")

    base_plan = AnalysisPlan(
        query=normalized_query,
        status=status,
        intent=data.get("intent"),
        source=data.get("source"),
        confidence=data.get("confidence"),
        model=data.get("model"),
        metric=data.get("metric"),
        metrics=data.get("metrics") or [],
        dimensions=data.get("dimensions") or [],
        filters=data.get("filters") or [],
        time_range=data.get("time_range"),
        clarification=data.get("clarification"),
        blocked_reason=data.get("blocked_reason"),
        task_steps=data.get("task_steps") or [],
        fallback_policy=data.get("fallback_policy") or {},
        verification_policy=data.get("verification_policy") or {},
        schema_version=data.get("schema_version", "v1"),
        plan_version=data.get("plan_version", "v1"),
        memory_refs=data.get("memory_refs") or [],
        diagnostics=diagnostics,
        task_id=data.get("task_id") or task_id,
        parent_task_id=data.get("parent_task_id") or parent_task_id,
        resume_payload=data.get("resume_payload") or resume_payload or {},
        task_type=data.get("task_type") or infer_task_type(normalized_query, data.get("intent")) or DESCRIPTIVE,
        analysis_config=data.get("analysis_config") or {},
        current_time_range=data.get("current_time_range"),
        previous_time_range=data.get("previous_time_range") or data.get("compare_time_range"),
        time_dimension=data.get("time_dimension"),
        attribution_dimension=data.get("attribution_dimension"),
        cohort_definition=data.get("cohort_definition") or {},
        funnel_steps=data.get("funnel_steps") or [],
        sub_plans=data.get("sub_plans") or [],
        decompose_strategy=data.get("decompose_strategy"),
        decompose_reason=data.get("decompose_reason"),
        execution_mode=data.get("execution_mode") or "plan_act",
        join_strategy=data.get("join_strategy") or {},
    )
    v2_data, validation = enforce_analysis_plan_v2(base_plan)
    diagnostics = dict(v2_data.get("diagnostics") or {})
    diagnostics["plan_validation"] = validation
    v2_data["diagnostics"] = diagnostics
    if not validation.get("valid") and v2_data.get("status") == "ok":
        v2_data["status"] = "error"
    return AnalysisPlan(
        query=v2_data.get("query"),
        status=v2_data.get("status"),
        intent=v2_data.get("intent"),
        source=v2_data.get("source"),
        confidence=v2_data.get("confidence"),
        model=v2_data.get("model"),
        metric=v2_data.get("metric"),
        metrics=v2_data.get("metrics"),
        dimensions=v2_data.get("dimensions"),
        filters=v2_data.get("filters"),
        time_range=v2_data.get("time_range"),
        clarification=v2_data.get("clarification"),
        blocked_reason=v2_data.get("blocked_reason"),
        task_steps=v2_data.get("task_steps"),
        fallback_policy=v2_data.get("fallback_policy"),
        verification_policy=v2_data.get("verification_policy"),
        schema_version=v2_data.get("schema_version"),
        plan_version=v2_data.get("plan_version"),
        memory_refs=v2_data.get("memory_refs"),
        diagnostics=v2_data.get("diagnostics"),
        task_id=v2_data.get("task_id"),
        parent_task_id=v2_data.get("parent_task_id"),
        resume_payload=v2_data.get("resume_payload"),
        task_type=v2_data.get("task_type"),
        analysis_config=v2_data.get("analysis_config"),
        current_time_range=v2_data.get("current_time_range"),
        previous_time_range=v2_data.get("previous_time_range"),
        time_dimension=v2_data.get("time_dimension"),
        attribution_dimension=v2_data.get("attribution_dimension"),
        cohort_definition=v2_data.get("cohort_definition"),
        funnel_steps=v2_data.get("funnel_steps"),
        sub_plans=v2_data.get("sub_plans"),
        decompose_strategy=v2_data.get("decompose_strategy"),
        decompose_reason=v2_data.get("decompose_reason"),
        execution_mode=v2_data.get("execution_mode"),
        join_strategy=v2_data.get("join_strategy"),
    )



def normalize_result(result, query, used_db=False, used_llm=False, session_id=None, trace_id=None):
    result = ensure_dict(result)
    if not isinstance(result, dict):
        result = dict(result or {})
    errors = result.get("errors") or []
    loop_stats = result.get("loop_stats") or {}
    execution = result.get("execution") or {}

    result.setdefault("query", query)
    legacy_status = result.get("status") or infer_status(result.get("termination_reason"), errors)
    result["legacy_status"] = legacy_status
    result["status"] = normalize_status(legacy_status)
    result.setdefault("intent", None)
    result.setdefault("model", None)
    result.setdefault("metric", None)
    result.setdefault("dimensions", [])
    result.setdefault("time_range", None)
    result.setdefault("steps", [])
    result.setdefault("insight", None)
    result.setdefault("chart", {"type": "none"})
    result.setdefault("sql", None)
    result.setdefault("results", None)
    result.setdefault("results_summary", None)
    result.setdefault("errors", errors)
    result.setdefault("trace", None)
    result.setdefault("termination_reason", None)
    result.setdefault("loop_stats", loop_stats)
    result.setdefault("diagnostics", result.get("diagnostics") or {})
    if result.get("status") == "blocked" and not result.get("blocked_reason"):
        result["blocked_reason"] = result.get("reason")
    if result.get("status") == "fallback" and not result.get("fallback_reason"):
        result["fallback_reason"] = result.get("reason")
    if result.get("status") == "pending_human_review" and not result.get("approval_status"):
        result["approval_status"] = result.get("reason") or "pending"
    result.setdefault("blocked_reason", result.get("blocked_reason"))
    if result.get("status") == "no_answer" and result.get("legacy_status") in ("unsupported", "fallback", "degraded") and not result.get("fallback_reason"):
        result["fallback_reason"] = result.get("reason") or result.get("blocked_reason") or result.get("legacy_status")
    result.setdefault("fallback_reason", result.get("fallback_reason"))
    result.setdefault("report", result.get("report"))
    result.setdefault("elapsed_ms", result.get("elapsed_ms"))
    result.setdefault("confidence", result.get("confidence"))
    result.setdefault("task_type", result.get("task_type") or infer_task_type(result.get("query"), result.get("intent")) or DESCRIPTIVE)
    result.setdefault("plan", result.get("plan") or _build_plan_snapshot(result))
    result.setdefault("requires_human_review", result.get("requires_human_review"))
    result.setdefault("approval_status", result.get("approval_status"))
    result.setdefault("risk_level", result.get("risk_level"))
    result.setdefault("review_checklist", result.get("review_checklist") or [])
    result.setdefault("prompt_chain", result.get("prompt_chain") or [])
    result.setdefault("prompt_specs", result.get("prompt_specs") or [])
    result.setdefault("sandbox", result.get("sandbox") or {})
    result.setdefault("human_gate", result.get("human_gate") or {})
    analysis = result.get("analysis") or result.get("insight") or {}
    if isinstance(analysis, dict) and "summary" not in analysis:
        analysis = dict(analysis)
        if analysis.get("insight") and not analysis.get("summary"):
            analysis["summary"] = analysis.get("insight")
        if analysis.get("top_n") is None and result.get("results"):
            analysis["top_n"] = result.get("results")
    result["analysis"] = analysis
    result.setdefault("interrupt", result.get("interrupt"))
    result.setdefault("state", result.get("state"))
    result.setdefault("executor", result.get("executor"))

    base_execution = {
        "used_db": bool(used_db),
        "used_llm": bool(used_llm),
        "tool_calls": loop_stats.get("tool_call_count", 0),
        "step_count": loop_stats.get("step_count", len(result.get("steps") or [])),
    }
    if isinstance(execution, dict):
        base_execution.update(execution)
    result["execution"] = base_execution
    result["session_id"] = session_id if session_id is not None else result.get("session_id")
    result["trace_id"] = trace_id if trace_id is not None else result.get("trace_id")
    result["diagnostics"] = _terminal_diagnostics(result)
    result["diagnostics"].setdefault("response_contract", "v1")
    result["diagnostics"].setdefault("status", result.get("status"))
    # Execution diagnostics are part of the stable facade contract. Terminal
    # paths (blocked, clarification, planner failure) do not enter the
    # execution engine, but consumers must not need a special-case branch.
    result["diagnostics"].setdefault("retry_count", 0)
    result["diagnostics"].setdefault("retry_exhausted", False)

    return ExecutionResult(
        query=result.get("query"),
        status=result.get("status"),
        intent=result.get("intent"),
        model=result.get("model"),
        metric=result.get("metric"),
        dimensions=result.get("dimensions"),
        time_range=result.get("time_range"),
        steps=result.get("steps"),
        insight=result.get("insight"),
        chart=result.get("chart"),
        sql=result.get("sql"),
        results=result.get("results"),
        results_summary=result.get("results_summary"),
        errors=result.get("errors"),
        trace=result.get("trace"),
        termination_reason=result.get("termination_reason"),
        loop_stats=result.get("loop_stats"),
        execution=result.get("execution"),
        diagnostics=result.get("diagnostics"),
        session_id=result.get("session_id"),
        trace_id=result.get("trace_id"),
        clarification=result.get("clarification"),
        task_id=result.get("task_id"),
        parent_task_id=result.get("parent_task_id"),
        resume_payload=result.get("resume_payload"),
        analysis=result.get("analysis"),
        interrupt=result.get("interrupt"),
        state=result.get("state"),
        executor=result.get("executor"),
        plan=result.get("plan"),
        report=result.get("report"),
        elapsed_ms=result.get("elapsed_ms"),
        blocked_reason=result.get("blocked_reason"),
        fallback_reason=result.get("fallback_reason"),
        confidence=result.get("confidence"),
        requires_human_review=result.get("requires_human_review"),
        approval_status=result.get("approval_status"),
        risk_level=result.get("risk_level"),
        review_checklist=result.get("review_checklist"),
        prompt_chain=result.get("prompt_chain"),
        prompt_specs=result.get("prompt_specs"),
        sandbox=result.get("sandbox"),
        human_gate=result.get("human_gate"),
        execution_mode=result.get("execution_mode") or (result.get("plan") or {}).get("execution_mode") or "plan_act",
    )


def _build_plan_snapshot(result):
    return {
        "intent": result.get("intent"),
        "model": result.get("model"),
        "metric": result.get("metric"),
        "dimensions": result.get("dimensions") or [],
        "time_range": result.get("time_range"),
        "task_id": result.get("task_id"),
        "task_type": result.get("task_type") or infer_task_type(result.get("query"), result.get("intent")) or DESCRIPTIVE,
        "execution_mode": result.get("execution_mode") or "plan_act",
    }


def validate_response_contract(result):
    data = result.to_dict() if hasattr(result, "to_dict") else ensure_dict(result)
    missing = [key for key in RESPONSE_CONTRACT_KEYS if key not in data]
    return len(missing) == 0, missing


def normalize_result_dict(result, query, used_db=False, used_llm=False, session_id=None, trace_id=None):
    return normalize_result(result, query, used_db=used_db, used_llm=used_llm, session_id=session_id, trace_id=trace_id).to_dict()


__all__ = [
    "AnalysisPlan",
    "ClarificationRequest",
    "ExecutionEnvelope",
    "build_execution_envelope",
    "ExecutionMeta",
    "ExecutionResult",
    "InsightBundle",
    "infer_status",
    "normalize_analysis_plan",
    "normalize_result",
    "normalize_result_dict",
    "normalize_status",
    "validate_response_contract",
    "CANONICAL_TERMINAL_STATUSES",
    "NON_OK_TERMINAL_STATUSES",
    "RESPONSE_CONTRACT_KEYS",
]
