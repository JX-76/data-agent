# -*- coding: utf-8 -*-
"""Build a compact, client-facing provenance and confidence statement.

The payload is intentionally factual: it explains which plan/data/runtime facts
support a result without fabricating causal explanations or confidence scores.
"""


def build_credibility(plan, result):
    plan_data = plan.to_dict() if hasattr(plan, "to_dict") else dict(plan or {})
    data = result.to_dict() if hasattr(result, "to_dict") else dict(result or {})
    diagnostics = data.get("diagnostics") or {}
    execution = data.get("execution") or {}
    status = data.get("status") or plan_data.get("status") or "error"

    evidence = []
    if plan_data.get("metric"):
        evidence.append("metric:%s" % plan_data.get("metric"))
    if plan_data.get("time_range"):
        evidence.append("time_range")
    if data.get("sql"):
        evidence.append("compiled_sql")
    if diagnostics.get("sql_preflight", {}).get("valid"):
        evidence.append("sql_preflight")
    if execution.get("used_db") or data.get("results") is not None:
        evidence.append("query_execution")

    grain_rewrite = diagnostics.get("grain_rewrite")
    if grain_rewrite is None:
        grain_rewrite = ((diagnostics.get("strategy_metadata") or {}).get("compiled_sql") or {}).get("grain_rewrite")
    if grain_rewrite and grain_rewrite.get("selected"):
        evidence.append("grain_safe_preaggregation")
        if grain_rewrite.get("semijoin_pushdowns"):
            evidence.append("dimension_filter_semijoin_pushdown")

    limitations = []
    quality = diagnostics.get("quality") or {}
    if status != "ok":
        limitations.append("result_not_final")
    if quality.get("empty_result"):
        limitations.append("empty_result")
    if diagnostics.get("retry_exhausted"):
        limitations.append("retry_exhausted")
    if plan_data.get("clarification"):
        limitations.append("clarification_pending")
    if grain_rewrite and not grain_rewrite.get("selected"):
        reason = grain_rewrite.get("reason")
        if reason:
            limitations.append("grain_rewrite_not_applied:%s" % reason)

    return {
        "contract": "credibility_v1",
        "status": status,
        "metric": plan_data.get("metric"),
        "dimensions": list(plan_data.get("dimensions") or []),
        "time_range": plan_data.get("time_range"),
        "data_source": plan_data.get("model"),
        "evidence": evidence,
        "limitations": limitations,
        "grain_rewrite": grain_rewrite,
        "requires_user_confirmation": status == "need_clarification" or bool(plan_data.get("clarification")),
        "confidence": data.get("confidence") if data.get("confidence") is not None else plan_data.get("confidence"),
    }


__all__ = ["build_credibility"]
