# -*- coding: utf-8 -*-
"""Release v1 API facade.

This module provides the stable product-facing envelope for the first runnable
release. It keeps the existing AgentFacade as the execution core and adds the
minimum release concerns around it: session reuse, audit id, answer envelope,
catalog/health/history endpoints, and a contract that the UI and release gate
can depend on.
"""
from __future__ import unicode_literals

import json
import os
import time
import uuid
from typing import Optional

try:
    from fastapi import APIRouter, Request
    from pydantic import BaseModel, Field
except Exception:  # pragma: no cover - allows script tests without FastAPI import path failures
    APIRouter = None
    Request = object
    BaseModel = object

from agent_facade import AgentFacade
from circuit_breaker import CircuitBreakerOpenError, get_circuit_breaker_registry
from timeout_guard import TimeoutGuard
from audit_logger import audit_logger, DEFAULT_AUDIT_PATH
from release_quality import evaluate_release_envelope
from release_dashboard import compute_dashboard, format_dashboard_text
from metrics_export import get_metrics_registry
from slo_policy import evaluate_slo, evaluate_alerts
from permission_policy import AccessContext
from masking_policy import sanitize_agent_payload, sanitize_text
from identity_provider import AccessContextProvider, DevelopmentMockIdentityProvider, IdentityError
from ecommerce_graphs import (
    GRAPH_METRIC_QUERY, GRAPH_BREAKDOWN, GRAPH_COMPARISON,
    GRAPH_ROOT_CAUSE, GRAPH_REPORT, run_ecommerce_graph,
)
from ecommerce_answer_adapter import adapt_ecommerce_graph_to_result
from contracts import build_execution_envelope
from answer_contracts import build_final_answer_contract
from final_output_evidence_gate import apply_final_output_evidence_gate, build_evidence_bus_from_envelope
from ollama_adapter import OllamaError, get_ollama_adapter
from deepseek_adapter import DeepSeekError, get_deepseek_adapter
from analysis_control_plane import (
    AnalysisContract, AnalysisRepository, ChartSpec as AnalysisChartSpec,
    InsightClaim, apply_analysis_patch, classify_user_dispute,
    create_correction_plan, invalidate_downstream, make_result_checksum,
    validate_claims_for_release,
)


RELEASE_CONTRACT = "release_v1_envelope"
_RELEASE_START = time.time()
_SESSIONS = {}
_SESSION_ACCESS_INDEX = {}
_HISTORY = []
_GATE_RESULTS = []
# In-memory adapter for the AnalysisContract. Production must replace this with
# a durable, tenant-scoped repository before using report revisions across restarts.
_ANALYSIS_REPOSITORY = AnalysisRepository()
_ANALYSIS_SESSION_INDEX = {}
_METRICS = {"total": 0, "ok": 0, "blocked": 0, "need_clarification": 0,
            "pending_human_review": 0, "error": 0, "fallback": 0}
API_VERSION = "v1"
_RUNTIME_BREAKER_NAME = "release_api_agent"
_ECOMMERCE_GRAPH_TYPES = (GRAPH_METRIC_QUERY, GRAPH_BREAKDOWN, GRAPH_COMPARISON, GRAPH_ROOT_CAUSE, GRAPH_REPORT)
_ECOMMERCE_FORBIDDEN_BODY_KEYS = set([
    "execution_engine", "worker_registry", "runtime", "evidence_bus",
    "execution_envelope", "previous_execution_envelope", "execution_result",
    "previous_execution_result", "evidence_id", "previous_evidence_id",
    "query_id", "previous_query_id", "data_version", "authority",
])
_DEFAULT_TIMEOUT_SECONDS = float(os.environ.get("DATA_AGENT_RELEASE_TIMEOUT_SECONDS", "30") or 30)
_DEFAULT_BREAKER_FAILURE_THRESHOLD = int(os.environ.get("DATA_AGENT_RELEASE_BREAKER_FAILURE_THRESHOLD", "5") or 5)
_DEFAULT_BREAKER_RECOVERY_SECONDS = float(os.environ.get("DATA_AGENT_RELEASE_BREAKER_RECOVERY_SECONDS", "30") or 30)
_DEFAULT_LLM_PROVIDER = (os.environ.get("DATA_AGENT_LLM_PROVIDER") or "ollama").strip().lower()
_PRODUCTION_REQUIRED_ENV = [
    "DATA_AGENT_AUTH_MODE",
    "DATA_AGENT_POSTGRES_ENABLED",
    "DATABASE_URL",
    "DATA_AGENT_RLS_CONFIRMED",
    "DATA_AGENT_AUDIT_SINK",
    "DATA_AGENT_METRICS_SINK",
    "DATA_AGENT_BACKUP_CONFIRMED",
]


class AskRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    session_id: Optional[str] = Field(default=None)
    use_llm: bool = Field(default=False)
    analysis_method: Optional[str] = Field(default=None)


class FollowupRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    session_id: str = Field(..., min_length=1)
    use_llm: bool = Field(default=False)
    analysis_method: Optional[str] = Field(default=None)


class ResumeRequest(BaseModel):
    session_id: str = Field(..., min_length=1)
    choice_id: str = Field(..., min_length=1)


class AnalysisCorrectionRequest(BaseModel):
    session_id: str = Field(..., min_length=1)
    feedback: str = Field(..., min_length=1, max_length=2000)
    analysis_id: Optional[str] = Field(default=None)
    target_type: Optional[str] = Field(default="claim")
    target_id: Optional[str] = Field(default=None)
    patch_ops: list = Field(default_factory=list)


class EcommerceGraphRequest(BaseModel):
    graph_type: str = Field(..., min_length=1, max_length=40)
    metric: str = Field(..., min_length=1, max_length=80)
    time_range: str = Field(..., min_length=1, max_length=120)
    dimensions: list = Field(default_factory=list)
    filters: dict = Field(default_factory=dict)
    rows: list = Field(default_factory=list)
    compare_time_range: Optional[str] = Field(default=None, max_length=120)
    previous_rows: list = Field(default_factory=list)
    session_id: Optional[str] = Field(default=None)


def _new_id(prefix):
    return "%s_%s" % (prefix, uuid.uuid4().hex[:16])


def _facade(session_id):
    sid = session_id or _new_id("sess")
    if sid not in _SESSIONS:
        _SESSIONS[sid] = AgentFacade(session_id=sid)
    return sid, _SESSIONS[sid]


def _roles(access_context):
    metadata = (access_context or {}).get("metadata") or {}
    roles = list(metadata.get("roles") or [])
    role = (access_context or {}).get("role")
    if role and role not in roles:
        roles.append(role)
    return roles


def _is_admin_context(access_context):
    roles = _roles(access_context)
    return "admin" in roles or "security_admin" in roles


def _is_production_profile():
    return (os.environ.get("AGENT_ENV") or os.environ.get("DATA_AGENT_ENV") or "development").lower() == "production"


def _env_truthy(name):
    return (os.environ.get(name) or "").strip().lower() in ("1", "true", "yes", "on", "enabled", "external", "postgres", "jwt", "oidc")


def _production_readiness_status():
    """Return deployment readiness without exposing secrets or DSNs."""
    profile = "production" if _is_production_profile() else "development"
    checks = []
    if profile != "production":
        return {"contract": "production_runtime_readiness_v1", "profile": profile, "ready": True,
                "checks": [{"name": "profile", "passed": True, "detail": "non_production_profile"}]}
    env = os.environ
    checks.append({"name": "auth_mode", "passed": (env.get("DATA_AGENT_AUTH_MODE") or "").lower() in ("jwt", "oidc"),
                   "detail": "production requires trusted JWT/OIDC boundary"})
    checks.append({"name": "postgres_enabled", "passed": _env_truthy("DATA_AGENT_POSTGRES_ENABLED") and bool(env.get("DATABASE_URL")),
                   "detail": "production requires external Postgres persistence"})
    checks.append({"name": "rls_confirmed", "passed": _env_truthy("DATA_AGENT_RLS_CONFIRMED"),
                   "detail": "database RLS/safety view confirmation is required"})
    checks.append({"name": "audit_sink", "passed": (env.get("DATA_AGENT_AUDIT_SINK") or "").lower() in ("external", "postgres", "siem"),
                   "detail": "production audit must use controlled external storage"})
    checks.append({"name": "metrics_sink", "passed": (env.get("DATA_AGENT_METRICS_SINK") or "").lower() in ("external", "prometheus", "otel"),
                   "detail": "production metrics must be externally scraped or exported"})
    checks.append({"name": "backup_confirmed", "passed": _env_truthy("DATA_AGENT_BACKUP_CONFIRMED"),
                   "detail": "backup/restore drill confirmation is required"})
    checks.append({"name": "dev_mock_disabled", "passed": (env.get("DATA_AGENT_AUTH_MODE") or "").lower() not in ("mock", "dev"),
                   "detail": "development mock identity cannot be production auth"})
    return {"contract": "production_runtime_readiness_v1", "profile": profile,
            "ready": all(item["passed"] for item in checks), "checks": checks}


def _remember_session_access(session_id, access_context):
    if not session_id or session_id in _SESSION_ACCESS_INDEX:
        return
    _SESSION_ACCESS_INDEX[session_id] = {
        "tenant_id": (access_context or {}).get("tenant_id") or "default",
        "user_id": (access_context or {}).get("user_id") or "anonymous",
        "roles": _roles(access_context),
        "created_at": int(time.time()),
    }


def _can_access_session(session_id, access_context):
    meta = _SESSION_ACCESS_INDEX.get(session_id) or {}
    if not meta:
        return _is_admin_context(access_context)
    if _is_admin_context(access_context):
        return meta.get("tenant_id") == ((access_context or {}).get("tenant_id") or "default")
    return (meta.get("tenant_id") == ((access_context or {}).get("tenant_id") or "default") and
            meta.get("user_id") == ((access_context or {}).get("user_id") or "anonymous"))


def _tenant_for_access(access_context, requested_tenant_id=None):
    own_tenant = (access_context or {}).get("tenant_id") or "default"
    if requested_tenant_id and requested_tenant_id != own_tenant and _is_admin_context(access_context):
        return requested_tenant_id
    return own_tenant


def _blocked_result(code, message, trace_id=None):
    result = _runtime_error_result("release_access", code, message, False, trace_id)
    result["status"] = "blocked"
    result["answer_type"] = "error"
    result["blocked_reason"] = code
    return result


def _safe_dict(value):
    if value is None:
        return {}
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if isinstance(value, dict):
        return value
    return {}


def _rows(result):
    rows = result.get("results") or result.get("rows") or []
    return rows if isinstance(rows, list) else []


def _analysis_evidence_ids(answer_contract, result):
    ids = []
    ac = answer_contract or {}
    for eid in ac.get("evidence_ids") or []:
        if eid and eid not in ids:
            ids.append(eid)
    for fact in ac.get("facts") or []:
        for eid in (fact.get("evidence_ids") or []):
            if eid and eid not in ids:
                ids.append(eid)
    for citation in ac.get("citations") or []:
        eid = citation if isinstance(citation, basestring) else (citation or {}).get("evidence_id")
        if eid and eid not in ids:
            ids.append(eid)
    for key in ("evidence_id", "previous_evidence_id"):
        eid = result.get(key)
        if eid and eid not in ids:
            ids.append(eid)
    return ids


def _analysis_chart_spec_from_answer(answer, result, analysis_id):
    chart = (answer or {}).get("chart") or result.get("chart") or {}
    return AnalysisChartSpec(
        chart_spec_id="%s:chart" % analysis_id,
        chart_type=chart.get("type") or chart.get("chart_type") or "none",
        x_axis=chart.get("x") or chart.get("x_axis"),
        y_axis=chart.get("y") or chart.get("y_axis"),
        series=chart.get("series") or [],
        title=chart.get("title"),
        unit=chart.get("unit"),
    )


def _build_analysis_contract_for_release(session_id, result, answer, answer_contract, access_context):
    if result.get("status") != "ok" or not result.get("metric") or not result.get("time_range"):
        return None
    trace_id = result.get("trace_id") or (answer_contract or {}).get("trace_id") or session_id
    analysis_id = result.get("analysis_id") or "analysis_%s" % (trace_id or session_id)
    evidence_ids = _analysis_evidence_ids(answer_contract, result)
    analysis = AnalysisContract(
        analysis_id=analysis_id,
        case_id=session_id,
        metric_definition=result.get("metric"),
        metric_version=result.get("metric_version") or "release_metric_v1",
        time_range=result.get("time_range"),
        timezone=result.get("timezone") or "UTC",
        dimensions=list(result.get("dimensions") or []),
        filters=dict(result.get("filters") or {}),
        grain=result.get("grain") or "unknown",
        permission_scope={
            "tenant_id": (access_context or {}).get("tenant_id") or result.get("tenant_id") or "default",
            "user_id": (access_context or {}).get("user_id"),
            "role": (access_context or {}).get("role"),
            "permission_scope": (access_context or {}).get("permission_scope"),
        },
        data_version=result.get("data_version") or result.get("provenance", {}).get("data_version") or "release_unknown_data_version",
        chart_spec=_analysis_chart_spec_from_answer(answer, result, analysis_id),
        evidence_ids=evidence_ids,
        result_checksum=make_result_checksum(_rows(result)),
        created_by=(access_context or {}).get("user_id"),
    )
    analysis.recompute_hashes()
    return analysis


def _build_insight_claims(answer_contract, result):
    claims = []
    for idx, fact in enumerate((answer_contract or {}).get("facts") or []):
        claims.append(InsightClaim(
            claim_id=fact.get("claim_id") or "claim_%s" % (idx + 1),
            text=fact.get("text") or fact.get("claim") or fact.get("summary") or "",
            claim_type=fact.get("claim_type") or "fact",
            confidence=fact.get("confidence") or "medium",
            evidence_ids=list(fact.get("evidence_ids") or []),
            metric_refs=[result.get("metric")] if result.get("metric") else [],
            time_range=result.get("time_range"),
            scope={"filters": result.get("filters") or {}, "dimensions": result.get("dimensions") or []},
            validation_status="validated",
        ))
    if not claims and result.get("summary"):
        claims.append(InsightClaim(
            claim_id="summary_claim",
            text=result.get("summary"),
            claim_type="fact" if _analysis_evidence_ids(answer_contract, result) else "hypothesis",
            confidence="medium" if _analysis_evidence_ids(answer_contract, result) else "low",
            evidence_ids=_analysis_evidence_ids(answer_contract, result),
            metric_refs=[result.get("metric")] if result.get("metric") else [],
            time_range=result.get("time_range"),
            scope={"filters": result.get("filters") or {}, "dimensions": result.get("dimensions") or []},
            validation_status="validated" if _analysis_evidence_ids(answer_contract, result) else "evidence_limited",
        ))
    return claims


def _attach_analysis_control(env, result, answer, answer_contract, access_context=None):
    if env.get("status") != "ok":
        env["analysis_control"] = {"contract": "release_analysis_control_v1", "enabled": False, "reason": "final_output_not_releasable"}
        return env
    analysis = _build_analysis_contract_for_release(env.get("session_id"), result, answer, answer_contract, access_context)
    if analysis is None:
        env["analysis_control"] = {"contract": "release_analysis_control_v1", "enabled": False, "reason": "no_verified_analysis_contract"}
        return env
    _ANALYSIS_REPOSITORY.save_analysis(analysis)
    _ANALYSIS_SESSION_INDEX[env.get("session_id")] = analysis.analysis_id
    claims = _build_insight_claims(answer_contract, result)
    checked = validate_claims_for_release(claims, analysis.evidence_ids)
    for claim in checked.get("claims") or []:
        _ANALYSIS_REPOSITORY.save_claim(analysis.analysis_id, claim)
    env["analysis_control"] = {
        "contract": "release_analysis_control_v1",
        "enabled": True,
        "analysis_contract": analysis.to_dict(),
        "insight_claims": [claim.to_dict() for claim in checked.get("claims") or []],
        "claim_gate": {"passed": checked.get("passed"), "errors": checked.get("errors") or []},
        "report_revision_policy": "contract_patch_not_freeform_rewrite",
    }
    env.setdefault("answer", {})["analysis_contract_id"] = analysis.analysis_id
    env["answer"]["analysis_version"] = analysis.version
    return env


def _analysis_for_session_or_id(session_id=None, analysis_id=None):
    aid = analysis_id or _ANALYSIS_SESSION_INDEX.get(session_id)
    return _ANALYSIS_REPOSITORY.get_analysis(aid) if aid else None


def correct_analysis_release(request_payload, user_id="release_user", access_context=None, headers=None):
    """Plan a safe report/chart correction; this endpoint never executes a query."""
    resolved_access = _resolve_access_context(headers=headers, access_context=access_context, user_id=user_id)
    payload = _public_model_dict(request_payload)
    session_id = payload.get("session_id")
    if not _can_access_session(session_id, resolved_access):
        return {"contract": "analysis_correction_response_v1", "status": "blocked", "blocked_reason": "session_access_denied"}
    analysis = _analysis_for_session_or_id(session_id, payload.get("analysis_id"))
    if analysis is None:
        return {"contract": "analysis_correction_response_v1", "status": "blocked", "blocked_reason": "analysis_not_found"}
    dispute = classify_user_dispute(payload.get("feedback") or "", payload.get("target_type") or "claim")
    plan = create_correction_plan(analysis, dispute, payload.get("target_id"))
    patch_result = None
    stale_plan = None
    if payload.get("patch_ops"):
        patch_result = apply_analysis_patch(analysis, payload.get("patch_ops"), requested_by=resolved_access.get("user_id"), reason=payload.get("feedback"))
        if not patch_result.get("passed"):
            return {"contract": "analysis_correction_response_v1", "status": "blocked", "blocked_reason": patch_result.get("error"), "dispute": dispute, "correction_plan": plan}
        analysis = patch_result["analysis"]
        _ANALYSIS_REPOSITORY.save_analysis(analysis)
        if patch_result.get("patch", {}).get("requires_recompute"):
            stale_plan = invalidate_downstream(analysis, _ANALYSIS_REPOSITORY.get_claims(analysis.analysis_id), "analysis_patch_requires_recompute")
    return {"contract": "analysis_correction_response_v1", "status": "needs_recompute" if stale_plan else "planned", "analysis_id": analysis.analysis_id, "analysis_version": analysis.version, "dispute": dispute, "correction_plan": plan, "patch": patch_result.get("patch") if patch_result else None, "stale_plan": stale_plan, "requires_release_gate": True, "analysis_contract": analysis.to_dict()}


def _answer(result):
    insight = _safe_dict(result.get("insight"))
    report = result.get("report") or {}
    if isinstance(report, basestring):
        try:
            report = json.loads(report)
        except Exception:
            report = {}
    summary = insight.get("summary") or result.get("summary") or report.get("summary")
    rows = _rows(result)
    if result.get("status") == "ok" and not rows:
        summary = "查询已执行，但当前筛选范围内没有可展示的数据。请调整时间范围或筛选条件后重试。"
    elif not summary:
        status = result.get("status") or "error"
        summary = "请求已完成。" if status == "ok" else "请求未完成，状态：%s。" % status
    return {
        "summary": summary,
        "table": rows,
        "chart": result.get("chart") or insight.get("chart") or {},
        "caveats": insight.get("caveats") or result.get("caveats") or [],
        "next_steps": insight.get("next_steps") or result.get("next_steps") or [],
    }


def _audit(user_id, query, result, audit_id):
    safe_query = sanitize_text(query or "")
    details = {
        "audit_id": audit_id,
        "contract": RELEASE_CONTRACT,
        "metric": result.get("metric"),
        "dimensions": result.get("dimensions") or [],
        "trace_id": result.get("trace_id"),
    }
    audit_logger.log_query(
        user_id=user_id,
        query=safe_query,
        status=result.get("status") or "error",
        sql=sanitize_text(result.get("sql") or ""),
        blocked_reason=result.get("blocked_reason") or result.get("reason"),
        trace_id=result.get("trace_id") or "",
        details=details,
        intent=result.get("intent"),
    )
    return audit_id


def _failure_category(result, status):
    if status == "ok":
        return None
    return result.get("blocked_reason") or result.get("failure_type") or result.get("reason") or status


def _runtime_error_result(stage, code, message, retryable=True, trace_id=None):
    return {
        "status": "error",
        "answer_type": "error",
        "summary": "请求未完成：%s" % message,
        "reason": message,
        "failure_type": code,
        "error": {
            "stage": stage,
            "code": code,
            "message": message,
            "retryable": bool(retryable),
        },
        "limitations": ["本次请求没有产生可验证执行证据，不能支撑数值、趋势、排名或归因结论。"],
        "next_actions": ["请稍后重试；如果持续失败，请联系运维查看 trace_id。"],
        "trace_id": trace_id,
        "authority": "unverified",
    }


def _safe_error_projection(result, final_status, audit_id):
    """Expose a typed, non-sensitive error category for harnesses and clients.

    Raw exceptions remain in protected trace/audit storage.  This projection is
    deliberately limited to a stage, stable code, retryability, remediation and
    opaque audit reference; it must never include exception text, SQL or paths.
    """
    if final_status != "error":
        return None
    result = result if isinstance(result, dict) else {}
    detail = result.get("error") or {}
    detail = detail if isinstance(detail, dict) else {}
    stage = detail.get("stage") or "release_api"
    code = detail.get("code") or result.get("failure_type") or "internal_runtime_error"
    retryable = bool(detail.get("retryable", True))
    if code in ("schema_error", "sql_validation_error", "invalid_result", "unknown_status"):
        remediation = "correct_request_or_supported_schema"
    elif code in ("permission_denied", "policy_denied", "session_access_denied"):
        remediation = "request_authorized_access"
    elif code in ("timeout", "circuit_open", "runtime_exception", "db_error", "retry_exhausted"):
        remediation = "retry_or_contact_operator"
    else:
        remediation = "contact_operator_with_audit_reference"
    return {"contract": "safe_error_v1", "stage": str(stage)[:80],
            "code": str(code)[:120], "retryable": retryable,
            "remediation": remediation, "trace_ref": audit_id}


def _breaker():
    return get_circuit_breaker_registry().get(
        _RUNTIME_BREAKER_NAME,
        failure_threshold=_DEFAULT_BREAKER_FAILURE_THRESHOLD,
        recovery_timeout=_DEFAULT_BREAKER_RECOVERY_SECONDS,
    )


def _execute_with_runtime_guard(fn, trace_id):
    """Execute release core with timeout/circuit guard and normalized failures."""
    try:
        with TimeoutGuard(_DEFAULT_TIMEOUT_SECONDS, "release_api") as guard:
            result = _breaker().call(fn)
        if guard.timed_out:
            return _runtime_error_result("release_api", "timeout", "请求执行超时，已安全降级。", True, trace_id)
        if not isinstance(result, dict):
            return _runtime_error_result("release_api", "invalid_result", "执行链路返回了非结构化结果。", False, trace_id)
        return result
    except CircuitBreakerOpenError:
        return _runtime_error_result("release_api", "circuit_open", "服务保护熔断已打开，暂时拒绝执行。", True, trace_id)
    except Exception as exc:
        return _runtime_error_result("release_api", "runtime_exception", str(exc), True, trace_id)


def _safe_release_payload(value, masked_fields=None):
    # R23/P1 is the single recursive protection boundary for result/report/raw data.
    return sanitize_agent_payload(value, masked_fields=masked_fields)


def _resolve_access_context(headers=None, access_context=None, user_id="release_user"):
    """Resolve trusted release identity; body/query identity is never trusted."""
    if access_context is not None:
        return AccessContext.from_value(access_context, fallback=user_id).to_dict()
    if headers:
        try:
            identity = AccessContextProvider(DevelopmentMockIdentityProvider()).resolve(headers=headers, body=None)
            roles = list(getattr(identity, "roles", None) or [])
            return AccessContext(
                user_id=getattr(identity, "user_id", None) or user_id,
                role=roles[0] if roles else "analyst",
                tenant_id=getattr(identity, "tenant_id", None) or "default",
                permissions={}, authenticated=bool(getattr(identity, "verified", False)),
                metadata={"identity_source": getattr(identity, "source", "unknown"), "roles": roles},
            ).to_dict()
        except IdentityError:
            return AccessContext.from_value({"user_id": user_id, "role": "anonymous", "authenticated": False}, fallback=user_id).to_dict()
    return AccessContext.from_value({"user_id": user_id, "role": "analyst", "authenticated": True}, fallback=user_id).to_dict()


def _confidence(score, reasons=None):
    """Return a stable, user-facing confidence descriptor (not a model certainty claim)."""
    score = max(0.0, min(1.0, float(score or 0.0)))
    if score >= 0.80:
        label = "high"
    elif score >= 0.55:
        label = "medium"
    elif score > 0:
        label = "low"
    else:
        label = "unknown"
    return {"score": round(score, 2), "label": label, "reasons": list(reasons or [])}


def _safe_chain_text(value, limit=280):
    """Never send implementation, SQL, stack or path material to product clients."""
    text = sanitize_text(value or "")
    lowered = text.lower()
    forbidden = ("traceback", "exception", "select ", " from ", "sqlite", "postgres",
                 ".py", "file ", "line ", "password", "token", "secret")
    if any(token in lowered for token in forbidden):
        return "内部执行细节已安全隐藏。"
    return text[:limit]


def _safe_analysis_code(env):
    """Produce explanatory pseudo-Python from verified public analysis metadata.

    This is deliberately not executable backend source, SQL, or a replay of any
    database operation. It documents the transformation applied to verified rows.
    """
    control = env.get("analysis_control") or {}
    answer_contract = env.get("answer_contract") or {}
    provenance = answer_contract.get("provenance") or env.get("provenance") or {}
    if env.get("status") != "ok" or not control.get("enabled"):
        return {"contract": "analysis_code_view_v1", "language": "python",
                "visibility": "hidden", "code": None, "sandboxed": False,
                "reason_if_hidden": "当前结果未形成可验证分析契约，未展示分析代码。",
                "redactions": ["backend_source", "sql", "internal_paths", "credentials"]}
    metric = provenance.get("metric") or ((control.get("analysis_contract") or {}).get("metric_definition"))
    time_range = provenance.get("time_range") or ((control.get("analysis_contract") or {}).get("time_range"))
    dimensions = ((control.get("analysis_contract") or {}).get("dimensions") or [])
    evidence_ids = answer_contract.get("evidence_ids") or []
    if not metric or not evidence_ids:
        return {"contract": "analysis_code_view_v1", "language": "python",
                "visibility": "hidden", "code": None, "sandboxed": False,
                "reason_if_hidden": "缺少指标或已验证证据，不能生成可解释分析过程。",
                "redactions": ["backend_source", "sql", "internal_paths", "credentials"]}
    group_columns = ["time"] + [str(item) for item in dimensions]
    group_literal = repr(group_columns)
    code = "# 用户级分析说明：数据来自已验证结果，不包含数据库连接或 SQL\n"
    code += "import pandas as pd\n\n"
    code += "rows = load_verified_result(evidence_id=%r)\n" % str(evidence_ids[0])
    code += "df = pd.DataFrame(rows)\n"
    code += "# 分析指标：%s；时间范围：%s\n" % (_safe_chain_text(metric, 80), _safe_chain_text(time_range, 80))
    code += "summary = (df.groupby(%s, as_index=False)[%r]\n" % (group_literal, str(metric))
    code += "             .sum()\n             .sort_values(%s))\n\n" % group_literal
    code += "chart = build_chart(data=summary, x='time', y=%r, title='指标趋势')\n" % str(metric)
    return {"contract": "analysis_code_view_v1", "language": "python", "visibility": "user_explainable",
            "code": code, "sandboxed": False, "executable_in_browser": False,
            "inputs": {"evidence_id": str(evidence_ids[0]), "metric": metric,
                       "dimensions": dimensions, "time_range": time_range,
                       "data_version": provenance.get("data_version"), "row_count": provenance.get("row_count")},
            "outputs": ["summary_table", "chart_spec", "evidence_bound_claims"],
            "redactions": ["backend_source", "sql", "internal_paths", "credentials"]}


def _llm_provider_adapter(provider):
    provider = (provider or "").strip().lower()
    if provider == "ollama":
        return get_ollama_adapter(), OllamaError
    if provider == "deepseek":
        return get_deepseek_adapter(), DeepSeekError
    return None, None


def _attach_presentation_assist(env, query, use_llm):
    """Attach optional provider output as presentation metadata only.

    It never changes status, answer facts, evidence ids, SQL, permissions or
    final-output gate results.
    """
    provider = _DEFAULT_LLM_PROVIDER
    if not use_llm:
        env["llm_assist"] = {"contract": "llm_assist_v1", "enabled": False,
                             "provider": provider, "status": "disabled"}
        return env
    adapter, error_class = _llm_provider_adapter(provider)
    if adapter is None:
        env["llm_assist"] = {"contract": "llm_assist_v1", "enabled": True,
                             "provider": provider, "status": "unavailable",
                             "reason": "unsupported_provider"}
        return env
    try:
        assist = adapter.explain_safe_analysis(query, env.get("release_chain") or {})
        env["llm_assist"] = assist
        env.setdefault("release_chain", {}).setdefault("report_sections", {})["local_model_note"] = assist.get("text")
    except error_class as exc:
        env["llm_assist"] = {"contract": "llm_assist_v1", "enabled": True,
                             "provider": provider, "status": "unavailable",
                             "model": getattr(adapter, "model", None), "reason": exc.code,
                             "safe_message": exc.message}
    except Exception:
        env["llm_assist"] = {"contract": "llm_assist_v1", "enabled": True,
                             "provider": provider, "status": "unavailable",
                             "reason": "adapter_exception",
                             "safe_message": "外部展示辅助模型暂不可用，已保留受治理的确定性分析结果。"}
    return env


def _build_release_chain_view(env):
    """Create the sole safe product projection for explainable analysis UI."""
    status = env.get("status") or "error"
    answer_contract = env.get("answer_contract") or {}
    gate = env.get("final_output_evidence_gate") or {}
    gate_metrics = gate.get("metrics") or {}
    control = env.get("analysis_control") or {}
    analysis = control.get("analysis_contract") or {}
    quality = env.get("quality") or {}
    provenance = answer_contract.get("provenance") or env.get("provenance") or {}
    facts = list(answer_contract.get("facts") or [])
    hypotheses = list(answer_contract.get("hypotheses") or [])
    evidence_ids = list(answer_contract.get("evidence_ids") or analysis.get("evidence_ids") or [])
    coverage = float(gate_metrics.get("final_output_lineage_coverage") or 0.0)
    quality_score = quality.get("score")
    quality_score = float(quality_score) if quality_score is not None else 0.5
    reasons = []
    score = quality_score
    if gate.get("allowed"):
        score = (score + coverage) / 2.0; reasons.append("evidence_gate_passed")
    else:
        score = min(score, 0.25); reasons.append("evidence_gate_limited")
    if provenance.get("data_version"): reasons.append("data_version_present")
    if provenance.get("time_range"): reasons.append("time_range_present")
    if status in ("blocked", "error", "no_answer", "unsupported", "pending_human_review"):
        score = 0.0; reasons.append("terminal_status_%s" % status)
    elif answer_contract.get("answer_type") == "evidence_limited":
        score = min(score, 0.45); reasons.append("evidence_limited")
    overall = _confidence(score, reasons)
    understanding_score = 0.9 if provenance.get("metric") and provenance.get("time_range") else 0.45
    steps = [
        {"step_id": "understand", "title": "理解分析问题", "status": "done" if status != "error" else "degraded",
         "user_message": "已识别指标、时间范围和分析意图。" if understanding_score >= .8 else "问题信息不完整，可能需要补充范围或指标。",
         "confidence": _confidence(understanding_score, ["metric_and_time_range" if understanding_score >= .8 else "partial_request"]),
         "safe_details": {"metric": provenance.get("metric") or analysis.get("metric_definition"), "time_range": provenance.get("time_range") or analysis.get("time_range"), "dimensions": analysis.get("dimensions") or []}},
        {"step_id": "permission", "title": "检查访问范围", "status": "blocked" if status == "blocked" else "done",
         "user_message": "访问范围已在服务端校验。" if status != "blocked" else "当前请求被安全策略或访问范围阻断。",
         "confidence": _confidence(0.0 if status == "blocked" else 1.0, ["policy_boundary"]), "safe_details": {}},
        {"step_id": "execute", "title": "准备并执行数据分析", "status": "done" if evidence_ids else ("blocked" if status == "blocked" else "degraded"),
         "user_message": "已获得可追溯的数据结果。" if evidence_ids else "本次未获得可验证的数据执行结果。",
         "confidence": _confidence(coverage if evidence_ids else 0.0, ["evidence_lineage_coverage"]),
         "safe_details": {"row_count": provenance.get("row_count"), "data_version": provenance.get("data_version")}},
        {"step_id": "validate", "title": "校验证据与输出范围", "status": "done" if gate.get("allowed") else "degraded",
         "user_message": "证据、时效、范围和引用已通过发布校验。" if gate.get("allowed") else "部分结论缺少有效证据，已降级或阻断。",
         "confidence": _confidence(coverage if gate.get("allowed") else 0.0, ["final_output_evidence_gate"]),
         "safe_details": {"lineage_coverage": coverage, "citation_failure_rate": gate_metrics.get("citation_validation_failure_rate", 0.0), "stale_evidence_rejection_rate": gate_metrics.get("stale_evidence_rejection_rate", 0.0)}},
        {"step_id": "report", "title": "生成分析报告", "status": "done" if status == "ok" else "degraded",
         "user_message": "已生成可查看的报告和下一步建议。" if status == "ok" else "报告已按安全策略降级为有限结果。",
         "confidence": overall, "safe_details": {"elapsed_ms": env.get("elapsed_ms"), "quality_score": quality.get("score")}},
    ]
    claims = []
    for index, fact in enumerate(facts):
        claims.append({"claim_id": fact.get("claim_id") or "fact_%s" % (index + 1), "text": _safe_chain_text(fact.get("text")), "type": "fact", "validation_status": "validated", "evidence_ids": list(fact.get("evidence_ids") or []), "evidence_state": "verified", "confidence": _confidence(coverage, ["verified_evidence"])})
    for index, item in enumerate(hypotheses):
        claims.append({"claim_id": item.get("claim_id") or "hypothesis_%s" % (index + 1), "text": _safe_chain_text(item.get("text")), "type": "hypothesis", "validation_status": "evidence_limited", "evidence_ids": [], "evidence_state": "limited", "confidence": _confidence(min(score, .45), ["requires_current_verified_execution_evidence"])})
    fallback_active = status != "ok" or not gate.get("allowed")
    if status == "blocked": fallback_message = "该请求未通过安全或权限边界，未执行不安全操作。"
    elif status == "error": fallback_message = "系统未能完成本次分析，内部错误细节未向用户公开。"
    elif not gate.get("allowed"): fallback_message = "部分结论无法由当前证据支持，已降级为假设或限制说明。"
    elif not evidence_ids: fallback_message = "没有可验证的执行证据，不能提供确定性数据结论。"
    else: fallback_message = ""
    return {"contract": "release_chain_v1", "trace_id": answer_contract.get("trace_id"), "session_id": env.get("session_id"), "audit_id": env.get("audit_id"), "overall_status": status, "overall_confidence": overall,
            "question_understanding": {"query": _safe_chain_text(env.get("query")), "metric": provenance.get("metric") or analysis.get("metric_definition"), "time_range": provenance.get("time_range") or analysis.get("time_range"), "dimensions": analysis.get("dimensions") or [], "filters": analysis.get("filters") or {}, "confidence": _confidence(understanding_score)},
            "steps": steps, "metrics": {"metric": provenance.get("metric") or analysis.get("metric_definition"), "time_range": provenance.get("time_range") or analysis.get("time_range"), "data_version": provenance.get("data_version") or analysis.get("data_version"), "row_count": provenance.get("row_count"), "elapsed_ms": env.get("elapsed_ms"), "quality_score": quality.get("score")},
            "claims": claims, "evidence_summary": {"evidence_count": len(evidence_ids), "valid_evidence_count": len(evidence_ids) if gate.get("allowed") else 0, "lineage_coverage": coverage, "gate_allowed": bool(gate.get("allowed")), "findings": [_safe_chain_text(item.get("message")) for item in gate.get("findings") or []]},
            "report_sections": {"summary": _safe_chain_text((env.get("answer") or {}).get("summary"), 1000), "limitations": [_safe_chain_text(item) for item in answer_contract.get("limitations") or []], "next_actions": [_safe_chain_text(item) for item in answer_contract.get("next_actions") or (env.get("answer") or {}).get("next_steps") or []]},
            "code_view": _safe_analysis_code(env), "conversation_state": {"turn_type": "followup" if env.get("session_id") else "initial", "can_follow_up": status in ("ok", "need_clarification", "no_answer"), "can_request_correction": bool(control.get("enabled")), "analysis_id": analysis.get("analysis_id"), "analysis_version": analysis.get("version")},
            "fallback": {"active": fallback_active, "reason": status if fallback_active else None, "safe_user_message": fallback_message, "suggested_next_actions": ["补充时间范围或筛选条件", "调整分析维度后重试", "如结果不符，可发起纠错或局部重算"] if fallback_active else []},
            "hidden_internal_fields": ["sql", "raw", "trace", "diagnostics", "backend_source", "internal_paths", "credentials"]}


def _envelope(query, session_id, result, started_ms, audit_id, access_context=None):
    status = result.get("status") or "error"
    if status not in ("ok", "need_clarification", "blocked", "error", "no_answer", "unsupported", "pending_human_review"):
        result = _runtime_error_result("release_api", "unknown_status", "未知终态：%s" % status, False, result.get("trace_id"))
        status = "error"
    _METRICS["total"] += 1
    _METRICS[status] = _METRICS.get(status, 0) + 1
    safe_result = _safe_release_payload(result)
    answer = _safe_release_payload(_answer(safe_result))
    answer_contract = safe_result.get("final_answer")
    if not answer_contract:
        answer_contract = build_final_answer_contract(safe_result, query=query)
    elapsed_ms = int(time.time() * 1000 - started_ms)
    env = {
        "contract": RELEASE_CONTRACT,
        "api_version": API_VERSION,
        "status": status,
        "terminal": status,
        "session_id": session_id,
        "audit_id": audit_id,
        "query": sanitize_text(query or ""),
        "answer": answer,
        "plan": safe_result.get("plan") or {},
        "sql": safe_result.get("sql"),
        "trace": safe_result.get("trace") or safe_result.get("diagnostics", {}).get("trace") or [],
        "credibility": safe_result.get("credibility") or {},
        "provenance": safe_result.get("provenance") or {},
        "diagnostics": safe_result.get("diagnostics") or safe_result.get("diagnosis") or {},
        "clarification": safe_result.get("clarification") or safe_result.get("clarification_session"),
        "raw": safe_result,
        "answer_contract": answer_contract,
        "elapsed_ms": elapsed_ms,
    }
    evidence_source_env = dict(env)
    evidence_source_env["raw"] = result
    evidence_bus = build_evidence_bus_from_envelope(evidence_source_env, case_id=session_id)
    env = apply_final_output_evidence_gate(
        env,
        evidence_bus=evidence_bus,
        case_id=session_id,
        access_context=access_context,
        require_evidence_bus=True,
        entrypoint="release_api",
    )
    safe_error = _safe_error_projection(safe_result, env.get("status"), audit_id)
    if safe_error:
        env["safe_error"] = safe_error
    env = _attach_analysis_control(env, safe_result, answer, answer_contract, access_context=access_context)
    env["quality"] = evaluate_release_envelope(env)
    # This is the only projection the product UI needs for explainable analysis.
    # Legacy fields remain for API compatibility but must not be rendered directly.
    env["release_chain"] = _build_release_chain_view(env)
    env = _attach_presentation_assist(env, query, bool(safe_result.get("use_llm_requested")))
    tenant_id = safe_result.get("tenant_id") or "default"
    get_metrics_registry().record(tenant_id=tenant_id, status=status, latency_ms=elapsed_ms,
        failure_stage=_failure_category(safe_result, status), quality_score=env["quality"].get("score"),
        governance=status == "blocked", human_review=status == "pending_human_review",
        pii_block=(safe_result.get("blocked_reason") == "sensitive_field"))
    _HISTORY.append({
        "timestamp": int(time.time()), "session_id": session_id, "audit_id": audit_id,
        "status": status, "metric": safe_result.get("metric"),
        "task_type": (safe_result.get("plan") or {}).get("task_type") or safe_result.get("intent"),
        "failure_category": _failure_category(safe_result, status), "elapsed_ms": elapsed_ms,
        "quality_score": env["quality"].get("score"), "tenant_id": tenant_id,
    })
    return env


def _release_precheck(query):
    text = (query or "").lower()
    dangerous = ["drop table", "delete from", "truncate", "alter table", "删除", "删表", "清空"]
    sensitive = ["密码", "password", "身份证", "手机号", "phone", "token", "secret"]
    if any(word in text for word in dangerous):
        return {"status": "blocked", "intent": "blocked", "blocked_reason": "dangerous_operation", "reason": "请求包含危险操作，Release v1 只允许只读分析。"}
    if any(word in text for word in sensitive):
        return {"status": "blocked", "intent": "blocked", "blocked_reason": "sensitive_field", "reason": "请求包含敏感字段，Release v1 不返回敏感信息。"}
    return None


def ask_release(query, session_id=None, use_llm=False, user_id="release_user", analysis_method=None,
                access_context=None, headers=None):
    started_ms = int(time.time() * 1000)
    trace_id = _new_id("trace")
    resolved_access = _resolve_access_context(headers=headers, access_context=access_context, user_id=user_id)
    sid, facade = _facade(session_id)
    _remember_session_access(sid, resolved_access)
    precheck = _release_precheck(query)
    if precheck:
        result = precheck
    else:
        result = _execute_with_runtime_guard(
            lambda: facade.ask(query, use_llm=use_llm,
                               access_context=resolved_access,
                               analysis_method=analysis_method),
            trace_id)
    if isinstance(result, dict):
        result.setdefault("tenant_id", resolved_access.get("tenant_id") or "default")
        result["use_llm_requested"] = bool(use_llm)
    audit_id = _new_id("audit")
    _audit(resolved_access.get("user_id") or user_id, query, result, audit_id)
    return _envelope(query, sid, result, started_ms, audit_id, access_context=resolved_access)



def _public_model_dict(value):
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    return dict(value or {})


def _graph_query_text(graph_type, payload):
    return "ecommerce_graph:%s:%s:%s" % (
        graph_type, payload.get("metric") or "", payload.get("time_range") or "")


def _validate_ecommerce_graph_payload(graph_type, payload, trace_id):
    if graph_type not in _ECOMMERCE_GRAPH_TYPES:
        return _blocked_result("unsupported_ecommerce_graph", "不支持的电商 Graph 类型。", trace_id)
    forbidden = sorted([key for key in payload.keys() if key in _ECOMMERCE_FORBIDDEN_BODY_KEYS])
    if forbidden:
        return _blocked_result("client_supplied_execution_evidence", "请求体不能注入执行器、运行时或 verified evidence：%s" % ",".join(forbidden), trace_id)
    if not payload.get("metric") or not payload.get("time_range"):
        return _blocked_result("missing_required_graph_slots", "缺少 metric 或 time_range。", trace_id)
    if graph_type == GRAPH_BREAKDOWN and not payload.get("dimensions"):
        return _blocked_result("missing_required_graph_slots", "breakdown graph 需要 dimensions。", trace_id)
    if graph_type == GRAPH_ROOT_CAUSE and not payload.get("dimensions"):
        return _blocked_result("missing_required_graph_slots", "root_cause graph 需要 dimensions 以限定候选贡献维度。", trace_id)
    if graph_type == GRAPH_COMPARISON and not payload.get("compare_time_range"):
        return _blocked_result("missing_required_graph_slots", "comparison graph 需要 compare_time_range。", trace_id)
    return None


def _build_controlled_graph_request(graph_type, payload, resolved_access, trace_id):
    rows = list(payload.get("rows") or [])
    req = {
        "graph_type": graph_type,
        "metric": payload.get("metric"),
        "time_range": payload.get("time_range"),
        "dimensions": list(payload.get("dimensions") or []),
        "filters": dict(payload.get("filters") or {}),
        "rows": rows,
        "query_id": _new_id("q"),
        "evidence_id": _new_id("ev"),
        "dataid": "ecommerce_orders",
        "data_version": "release_controlled_fixture_v1",
        "access_context": resolved_access,
        "constraints": {"readonly": True, "tenant_id": resolved_access.get("tenant_id") or "default"},
    }
    req["execution_envelope"] = build_execution_envelope(
        status="ok", stage="execute", query_id=req["query_id"], evidence_id=req["evidence_id"],
        dataid=req["dataid"], data_version=req["data_version"], row_count=len(rows),
        time_range=req["time_range"], authority="verified_execution",
        provenance={
            "trace_id": trace_id,
            "tenant_id": resolved_access.get("tenant_id") or "default",
            "user_id": resolved_access.get("user_id"),
            "permission_scope": resolved_access.get("permission_scope"),
        },
        metadata={
            "metric": req["metric"],
            "dimensions": req["dimensions"],
            "filters": req["filters"],
            "tenant_id": resolved_access.get("tenant_id") or "default",
            "user_id": resolved_access.get("user_id"),
            "permission_scope": resolved_access.get("permission_scope"),
        })
    if graph_type == GRAPH_COMPARISON:
        previous_rows = list(payload.get("previous_rows") or [])
        req["compare_time_range"] = payload.get("compare_time_range")
        req["previous_rows"] = previous_rows
        req["previous_query_id"] = _new_id("q")
        req["previous_evidence_id"] = _new_id("ev")
        req["previous_execution_envelope"] = build_execution_envelope(
            status="ok", stage="execute", query_id=req["previous_query_id"], evidence_id=req["previous_evidence_id"],
            dataid=req["dataid"], data_version=req["data_version"], row_count=len(previous_rows),
            time_range=req["compare_time_range"], authority="verified_execution",
            provenance={
                "trace_id": trace_id,
                "tenant_id": resolved_access.get("tenant_id") or "default",
                "user_id": resolved_access.get("user_id"),
                "permission_scope": resolved_access.get("permission_scope"),
            },
            metadata={
                "metric": req["metric"],
                "dimensions": req["dimensions"],
                "filters": req["filters"],
                "tenant_id": resolved_access.get("tenant_id") or "default",
                "user_id": resolved_access.get("user_id"),
                "permission_scope": resolved_access.get("permission_scope"),
            })
    return req


def ecommerce_graph_release(request_payload, user_id="release_user", access_context=None, headers=None):
    started_ms = int(time.time() * 1000)
    trace_id = _new_id("trace")
    resolved_access = _resolve_access_context(headers=headers, access_context=access_context, user_id=user_id)
    payload = _public_model_dict(request_payload)
    graph_type = payload.get("graph_type")
    sid = payload.get("session_id") or _new_id("sess")
    _remember_session_access(sid, resolved_access)
    query = _graph_query_text(graph_type, payload)
    validation = _validate_ecommerce_graph_payload(graph_type, payload, trace_id)
    if validation:
        result = validation
    else:
        def _run():
            graph_request = _build_controlled_graph_request(graph_type, payload, resolved_access, trace_id)
            graph_result = run_ecommerce_graph(graph_type, graph_request, trace_id=trace_id, session_id=sid)
            adapted = adapt_ecommerce_graph_to_result(graph_result, query=query, trace_id=trace_id, task_id=_new_id("task"))
            adapted["intent"] = "ecommerce_graph"
            adapted["metric"] = payload.get("metric")
            adapted["dimensions"] = list(payload.get("dimensions") or [])
            adapted["time_range"] = payload.get("time_range")
            adapted["filters"] = dict(payload.get("filters") or {})
            adapted["data_version"] = "release_controlled_fixture_v1"
            if graph_type == GRAPH_COMPARISON:
                # Keep the comparison range in the release projection so the
                # final evidence boundary can validate both server-produced
                # period receipts against the declared comparison scope.
                adapted["compare_time_range"] = payload.get("compare_time_range")
            adapted["tenant_id"] = resolved_access.get("tenant_id") or "default"
            adapted["answer_type"] = adapted.get("final_answer", {}).get("answer_type") or "analysis"
            return adapted
        result = _execute_with_runtime_guard(_run, trace_id)
    if isinstance(result, dict):
        result.setdefault("tenant_id", resolved_access.get("tenant_id") or "default")
        result["use_llm_requested"] = False
        result.setdefault("trace_id", trace_id)
    audit_id = _new_id("audit")
    _audit(resolved_access.get("user_id") or user_id, query, result, audit_id)
    return _envelope(query, sid, result, started_ms, audit_id, access_context=resolved_access)


def followup_release(query, session_id, use_llm=False, user_id="release_user", analysis_method=None,
                     access_context=None, headers=None):
    started_ms = int(time.time() * 1000)
    trace_id = _new_id("trace")
    resolved_access = _resolve_access_context(headers=headers, access_context=access_context, user_id=user_id)
    sid, facade = _facade(session_id)
    if not _can_access_session(sid, resolved_access):
        result = _blocked_result("session_access_denied", "无权访问该会话或会话不存在。", trace_id)
    else:
        facade.access_context = resolved_access
        # AgentFacade owns bounded session context; a follow-up is deliberately
        # routed through the same governed ask pipeline instead of a separate
        # shortcut.  This preserves governance, permission scope, evidence
        # publication and final-output validation for every turn.
        result = _execute_with_runtime_guard(
            lambda: facade.ask(query, use_llm=use_llm,
                               access_context=resolved_access,
                               analysis_method=analysis_method),
            trace_id)
    if isinstance(result, dict):
        result.setdefault("tenant_id", resolved_access.get("tenant_id") or "default")
        result["use_llm_requested"] = bool(use_llm)
    audit_id = _new_id("audit")
    _audit(resolved_access.get("user_id") or user_id, query, result, audit_id)
    return _envelope(query, sid, result, started_ms, audit_id, access_context=resolved_access)


def resume_release(session_id, choice_id, user_id="release_user", access_context=None, headers=None):
    started_ms = int(time.time() * 1000)
    trace_id = _new_id("trace")
    resolved_access = _resolve_access_context(headers=headers, access_context=access_context, user_id=user_id)
    sid, facade = _facade(session_id)
    if not _can_access_session(sid, resolved_access):
        result = _blocked_result("session_access_denied", "无权访问该会话或会话不存在。", trace_id)
    else:
        facade.access_context = resolved_access
        result = _execute_with_runtime_guard(lambda: facade.resume_clarification(choice_id), trace_id)
    if isinstance(result, dict):
        result.setdefault("tenant_id", resolved_access.get("tenant_id") or "default")
    audit_id = _new_id("audit")
    _audit(resolved_access.get("user_id") or user_id, "resume:%s" % choice_id, result, audit_id)
    return _envelope("resume:%s" % choice_id, sid, result, started_ms, audit_id, access_context=resolved_access)


def release_history(limit=50, tenant_id=None, access_context=None):
    """Safe metadata-only history; never exposes query, SQL, rows or raw payloads."""
    limit = max(1, min(int(limit), 200))
    scoped_tenant = _tenant_for_access(access_context, tenant_id) if access_context is not None else tenant_id
    records = [x for x in _HISTORY if scoped_tenant is None or x.get("tenant_id", "default") == scoped_tenant]
    safe = [dict((k, v) for k, v in x.items() if k not in ("query", "sql", "raw", "rows")) for x in records]
    return {"contract": "release_v1_history", "api_version": API_VERSION,
            "items": list(reversed(safe[-limit:])), "total": len(safe)}


def record_gate_result(name, passed, total=0, failed=0, summary=""):
    """Store a safe aggregate gate record for dashboard/health consumers."""
    item = {"contract": "release_v1_gate_result", "name": str(name), "passed": bool(passed),
            "status": "passed" if passed else "failed", "timestamp": int(time.time()),
            "total": int(total or 0), "failed": int(failed or 0), "summary": str(summary or "")[:300]}
    _GATE_RESULTS.append(item)
    del _GATE_RESULTS[:-20]
    return item


def _read_yaml_text(path):
    try:
        f = open(path, "r", encoding="utf-8")
    except TypeError:
        f = open(path, "r")
    except Exception:
        return ""
    try:
        return f.read()
    finally:
        f.close()


def release_catalog():
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
    semantic = os.path.join(root, "semantic")
    return {
        "contract": "release_v1_catalog",
        "metrics_yaml": _read_yaml_text(os.path.join(semantic, "metrics.yaml")),
        "dimensions_yaml": _read_yaml_text(os.path.join(semantic, "dimensions.yaml")),
        "tables_yaml": _read_yaml_text(os.path.join(semantic, "tables.yaml")),
        "models_yaml": _read_yaml_text(os.path.join(semantic, "models.yaml")),
    }


def release_health():
    dashboard = compute_dashboard(_HISTORY, _METRICS, _GATE_RESULTS)
    production = _production_readiness_status()
    ready = bool(production.get("ready"))
    components = {"agent_facade": "ready", "audit": "ready", "dashboard": "ready", "masking": "ready"}
    llm_adapter, _llm_error = _llm_provider_adapter(_DEFAULT_LLM_PROVIDER)
    ollama = llm_adapter.health() if llm_adapter is not None else {"provider": _DEFAULT_LLM_PROVIDER, "ready": False, "reason": "unsupported_provider"}
    components["local_llm"] = "ready" if ollama.get("ready") else "degraded"
    if not ready:
        components["production_runtime"] = "blocked"
    else:
        components["production_runtime"] = "ready"
    return {
        "contract": "release_v1_health", "api_version": API_VERSION,
        "status": "healthy" if ready else "blocked",
        "ready": ready, "uptime_seconds": int(time.time() - _RELEASE_START),
        "sessions": len(_SESSIONS), "history_count": len(_HISTORY), "metrics": dict(_METRICS),
        "runtime": {"timeout_seconds": _DEFAULT_TIMEOUT_SECONDS, "circuit_breakers": get_circuit_breaker_registry().all_stats()},
        "success_rate": dashboard.get("success_rate", 0.0), "quality": dashboard.get("quality", {}),
        "recent_gate": dashboard.get("recent_gate"), "components": components,
        "production_readiness": production,
        "local_llm": ollama,
        "audit_path": DEFAULT_AUDIT_PATH,
    }


def release_metrics(tenant_id="default", access_context=None):
    return get_metrics_registry().summary(_tenant_for_access(access_context, tenant_id) if access_context is not None else tenant_id)

def release_metrics_prometheus(tenant_id="default", access_context=None):
    return get_metrics_registry().prometheus(_tenant_for_access(access_context, tenant_id) if access_context is not None else tenant_id)

def release_slo_status(tenant_id="default", policy=None, access_context=None):
    return evaluate_slo(release_metrics(tenant_id, access_context=access_context), policy)

def release_alerts(tenant_id="default", policy=None, access_context=None):
    return evaluate_alerts(release_slo_status(tenant_id, policy, access_context=access_context))

def release_quality_trend(tenant_id="default", limit=50, access_context=None):
    scoped_tenant = _tenant_for_access(access_context, tenant_id) if access_context is not None else tenant_id
    safe = [dict((k, v) for k, v in item.items() if k not in ("query", "sql", "raw")) for item in _HISTORY if item.get("tenant_id", "default") == scoped_tenant]
    return {"contract": "quality_trend_v1", "items": safe[-max(1, min(int(limit), 200)):], "total": len(safe), "tenant_id": scoped_tenant}

def release_approval_summary(tenant_id="default", access_context=None):
    scoped_tenant = _tenant_for_access(access_context, tenant_id) if access_context is not None else tenant_id
    items = [x for x in _HISTORY if x.get("tenant_id", "default") == scoped_tenant and x.get("status") == "pending_human_review"]
    return {"contract":"approval_summary_v1", "tenant_id": scoped_tenant, "pending_count":len(items), "latest_timestamp":items[-1].get("timestamp") if items else None}

def release_dashboard(access_context=None):
    if access_context is None:
        return compute_dashboard(_HISTORY, _METRICS, _GATE_RESULTS)
    tenant_id = _tenant_for_access(access_context, None)
    scoped = [x for x in _HISTORY if x.get("tenant_id", "default") == tenant_id]
    return compute_dashboard(scoped, _METRICS, _GATE_RESULTS)


def release_recent_audit(limit=20, access_context=None):
    path = audit_logger.path
    items = []
    try:
        f = open(path, "r", encoding="utf-8")
    except TypeError:
        f = open(path, "r")
    except Exception:
        return {"contract": "release_v1_audit", "items": [], "total": 0}
    try:
        lines = f.readlines()[-limit:]
    finally:
        f.close()
    for line in lines:
        try:
            item = json.loads(line)
        except Exception:
            continue
        if access_context is not None and not _is_admin_context(access_context):
            if item.get("user_id") != (access_context or {}).get("user_id"):
                continue
        details = dict(item.get("details") or {}) if isinstance(item.get("details"), dict) else {}
        for key in ("query", "sql", "raw", "rows", "result", "answer", "prompt"):
            item.pop(key, None)
            details.pop(key, None)
        safe = {"timestamp": item.get("timestamp"), "user_id": item.get("user_id") if _is_admin_context(access_context) else None,
                "status": item.get("status"), "intent": item.get("intent"), "trace_id": item.get("trace_id"),
                "blocked_reason": item.get("blocked_reason"), "details": details}
        items.append(_safe_release_payload(safe))
    return {"contract": "release_v1_audit", "items": items, "total": len(items),
            "limitations": ["该接口只返回审计元数据，不返回原始请求、SQL、prompt、执行载荷或行级数据。"]}


try:
    router = APIRouter(prefix="/api", tags=["release-v1"]) if APIRouter else None
except TypeError:
    # Some local FastAPI/Starlette combinations are incompatible at router
    # construction time. Keep the pure-python release contract importable so the
    # release gate can still validate the runnable agent path.
    router = None

if router is not None:
    @router.post("/ask")
    async def api_ask(req: AskRequest, request: Request):
        return ask_release(req.query, session_id=req.session_id, use_llm=req.use_llm,
                           analysis_method=req.analysis_method, headers=dict(request.headers))

    @router.post("/followup")
    async def api_followup(req: FollowupRequest, request: Request):
        return followup_release(req.query, session_id=req.session_id, use_llm=req.use_llm,
                                analysis_method=req.analysis_method, headers=dict(request.headers))

    @router.post("/resume")
    async def api_resume(req: ResumeRequest, request: Request):
        return resume_release(req.session_id, req.choice_id, headers=dict(request.headers))

    @router.post("/ecommerce/graph")
    async def api_ecommerce_graph(req: EcommerceGraphRequest, request: Request):
        return ecommerce_graph_release(req, headers=dict(request.headers))

    @router.post("/analysis/correct")
    async def api_analysis_correct(req: AnalysisCorrectionRequest, request: Request):
        return correct_analysis_release(req, headers=dict(request.headers))

    @router.get("/history")
    async def api_history(request: Request, limit=50, tenant_id=None):
        access = _resolve_access_context(headers=dict(request.headers))
        return release_history(limit=int(limit), tenant_id=tenant_id, access_context=access)

    @router.get("/catalog")
    async def api_catalog():
        return release_catalog()

    @router.get("/health")
    async def api_health():
        return release_health()

    @router.get("/audit/recent")
    async def api_audit_recent(request: Request, limit=20):
        access = _resolve_access_context(headers=dict(request.headers))
        return release_recent_audit(limit=int(limit), access_context=access)

    @router.get("/dashboard")
    async def api_dashboard(request: Request):
        access = _resolve_access_context(headers=dict(request.headers))
        return release_dashboard(access_context=access)

    @router.get("/metrics")
    async def api_metrics(request: Request, tenant_id="default"):
        access = _resolve_access_context(headers=dict(request.headers))
        return release_metrics(tenant_id, access_context=access)

    @router.get("/metrics/prometheus")
    async def api_metrics_prometheus(request: Request, tenant_id="default"):
        access = _resolve_access_context(headers=dict(request.headers))
        return release_metrics_prometheus(tenant_id, access_context=access)

    @router.get("/quality/trend")
    async def api_quality_trend(request: Request, tenant_id="default", limit=50):
        access = _resolve_access_context(headers=dict(request.headers))
        return release_quality_trend(tenant_id, int(limit), access_context=access)

    @router.get("/slo")
    async def api_slo(request: Request, tenant_id="default"):
        access = _resolve_access_context(headers=dict(request.headers))
        return release_slo_status(tenant_id, access_context=access)

    @router.get("/alerts")
    async def api_alerts(request: Request, tenant_id="default"):
        access = _resolve_access_context(headers=dict(request.headers))
        return release_alerts(tenant_id, access_context=access)

    @router.get("/approvals/summary")
    async def api_approval_summary(request: Request, tenant_id="default"):
        access = _resolve_access_context(headers=dict(request.headers))
        return release_approval_summary(tenant_id, access_context=access)


try:
    basestring
except NameError:  # pragma: no cover
    basestring = str


__all__ = [
    "RELEASE_CONTRACT", "router", "ask_release", "followup_release",
    "resume_release", "ecommerce_graph_release", "EcommerceGraphRequest", "correct_analysis_release", "AnalysisCorrectionRequest", "release_history", "release_catalog", "release_health",
    "release_recent_audit", "release_dashboard", "record_gate_result", "release_metrics", "release_metrics_prometheus", "release_slo_status", "release_alerts", "release_quality_trend", "release_approval_summary", "API_VERSION", "_production_readiness_status",
]
