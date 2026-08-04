# -*- coding: utf-8 -*-
"""Evidence requirements for business analysis strategies.

This module is intentionally small and dict-compatible.  It prevents strategy
plugins from converting empty, failed or unverified execution payloads into
confirmed business conclusions.
"""
from __future__ import unicode_literals


TERMINAL_INSUFFICIENT_STATUS = "need_more_data"


_REQUIREMENTS = {
    "descriptive": {"min_rows": 1, "needs_verified_execution": True},
    "comparison": {"min_rows": 2, "needs_verified_execution": True, "needs_comparison": True},
    # Three observations establish a minimal current-vs-history window for the
    # deterministic anomaly strategy; production callers can require longer
    # history through their plan/policy before release.
    "anomaly": {"min_rows": 3, "needs_verified_execution": True, "needs_history": True},
    "attribution": {"min_rows": 2, "needs_verified_execution": True, "needs_dimension": True},
    "retention": {"min_rows": 1, "needs_verified_execution": True},
    "forecast": {"min_rows": 1, "needs_verified_execution": False, "needs_forecast_metadata": True},
    "experiment": {"min_rows": 2, "needs_verified_execution": False, "needs_experiment_metadata": True},
}


def _as_dict(value):
    if value is None:
        return {}
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if isinstance(value, dict):
        return value
    return {}


def _rows(execution_result):
    data = _as_dict(execution_result)
    rows = data.get("results") or data.get("rows") or []
    return rows if isinstance(rows, list) else []


def _execution_envelope(execution_result):
    data = _as_dict(execution_result)
    env = data.get("execution_envelope") or data.get("envelope") or {}
    if not isinstance(env, dict):
        env = {}
    return env


def evidence_ids_from_execution(execution_result):
    data = _as_dict(execution_result)
    ids = []
    env = _execution_envelope(data)
    for key in ("evidence_id",):
        if env.get(key):
            ids.append(env.get(key))
    for item in data.get("evidence_refs") or data.get("evidence_ids") or []:
        if isinstance(item, dict) and item.get("evidence_id"):
            ids.append(item.get("evidence_id"))
        elif item:
            ids.append(item)
    out = []
    for item in ids:
        if item and item not in out:
            out.append(item)
    return out


def has_verified_execution(execution_result):
    data = _as_dict(execution_result)
    if data.get("status") not in (None, "ok"):
        return False
    env = _execution_envelope(data)
    if env:
        return env.get("status") == "ok" and env.get("authority") == "verified_execution" and bool(env.get("evidence_id"))
    # Backward-compatible direct strategy callers have neither an execution
    # envelope nor an explicit status.  They are still presentation-only; final
    # release boundaries require an envelope and enforce evidence separately.
    return bool(evidence_ids_from_execution(data)) or bool(data.get("verified") is True) or data.get("status") is None


def _has_comparison(rows):
    if len(rows) < 2:
        return False
    roles = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        role = row.get("period") or row.get("window") or row.get("time_role")
        if role:
            roles.add(role)
        if row.get("current_value") is not None and row.get("previous_value") is not None:
            return True
    return bool(roles.intersection(set(["current", "this", "本期"]))) and bool(roles.intersection(set(["previous", "last", "上期"])))


def _has_dimension(rows, plan):
    dims = (_as_dict(plan).get("dimensions") or [])
    dim = dims[0] if dims else _as_dict(plan).get("attribution_dimension")
    if not dim:
        return False
    for row in rows:
        if isinstance(row, dict) and (row.get(dim) is not None or row.get("dimension") is not None):
            return True
    return False


def assess_strategy_evidence(task_type, plan, execution_result):
    task_type = task_type or "descriptive"
    req = dict(_REQUIREMENTS.get(task_type) or _REQUIREMENTS["descriptive"])
    data = _as_dict(execution_result)
    rows = _rows(data)
    diagnostics = data.get("diagnostics") or {}
    quality = diagnostics.get("quality") or {}
    reasons = []

    if data.get("status") not in (None, "ok"):
        reasons.append("execution_status_not_ok:%s" % data.get("status"))
    if quality.get("empty_result") or len(rows) < req.get("min_rows", 1):
        reasons.append("insufficient_rows:need_%s_got_%s" % (req.get("min_rows", 1), len(rows)))
    if req.get("needs_verified_execution") and not has_verified_execution(data):
        reasons.append("verified_execution_evidence_missing")
    if req.get("needs_comparison") and not _has_comparison(rows):
        reasons.append("comparison_baseline_missing")
    if req.get("needs_history") and len(rows) < req.get("min_rows", 1):
        reasons.append("history_window_missing")
    if req.get("needs_dimension") and not _has_dimension(rows, plan):
        reasons.append("attribution_dimension_missing")
    if req.get("needs_forecast_metadata"):
        if not diagnostics.get("method") or diagnostics.get("training_window") is None:
            reasons.append("forecast_metadata_missing")
    return {"ok": not reasons, "status": "ok" if not reasons else TERMINAL_INSUFFICIENT_STATUS, "reasons": reasons, "requirements": req, "row_count": len(rows), "evidence_ids": evidence_ids_from_execution(data)}


def build_need_more_data_analysis(task_type, plan, execution_result, assessment=None):
    assessment = assessment or assess_strategy_evidence(task_type, plan, execution_result)
    plan = _as_dict(plan)
    return {
        "type": task_type,
        "status": TERMINAL_INSUFFICIENT_STATUS,
        "definition": {"task_type": task_type, "metric": plan.get("metric"), "dimensions": plan.get("dimensions") or [], "time_range": plan.get("time_range")},
        "data_quality": {"row_count": assessment.get("row_count", 0), "empty_result": assessment.get("row_count", 0) == 0, "status": "insufficient", "messages": list(assessment.get("reasons") or [])},
        "items": [],
        "summary_facts": {"row_count": assessment.get("row_count", 0)},
        "hypotheses": [],
        "caveats": [u"当前执行证据不足，不能形成已确认的%s结论。" % task_type],
        "next_steps": [u"请补充时间范围、对比基线、拆解维度或重新执行数据查询。"],
        "evidence_assessment": assessment,
    }


__all__ = ["assess_strategy_evidence", "build_need_more_data_analysis", "evidence_ids_from_execution", "has_verified_execution", "TERMINAL_INSUFFICIENT_STATUS"]
