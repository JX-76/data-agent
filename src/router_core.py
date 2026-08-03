# -*- coding: utf-8 -*-
"""Shared routing helpers."""

import datetime as dt

try:
    unicode
except NameError:  # pragma: no cover - Python 3 compatibility
    unicode = str


def _ensure_unicode(value):
    if value is None:
        return u""
    if isinstance(value, unicode):
        return value
    try:
        return value.decode("utf-8")
    except Exception:
        try:
            return unicode(value)
        except Exception:
            return u""

from semantic_utils import DANGEROUS, SENSITIVE
from clarification_policy import build_clarification
from schemas import AnalysisPlan
from task_types import DESCRIPTIVE, infer_task_type


def parse_time_range_label(label, now=None):
    current = now or dt.datetime.now()
    normalized = (label or "yesterday").lower()

    if normalized in ("yesterday", "last_1d"):
        start = current - dt.timedelta(days=1)
    elif normalized in ("last7d", "last_7d", "recent_7d"):
        start = current - dt.timedelta(days=7)
    elif normalized in ("last14d", "last_14d", "recent_14d"):
        start = current - dt.timedelta(days=14)
    elif normalized in ("last30d", "last_30d", "recent_30d"):
        start = current - dt.timedelta(days=30)
    elif normalized in ("this_week", "week"):
        start = current - dt.timedelta(days=current.weekday())
    elif normalized in ("this_month", "month"):
        start = current.replace(day=1)
    else:
        start = current - dt.timedelta(days=1)

    return (
        start.replace(hour=0, minute=0, second=0, microsecond=0),
        current.replace(hour=0, minute=0, second=0, microsecond=0),
    )


def detect_blocked_query(query):
    q = _ensure_unicode(query or u"").lower()
    if any(word in q for word in DANGEROUS):
        return True, "您的查询包含危险操作，仅支持只读数据查询"
    if any(word in q for word in SENSITIVE):
        return True, "您的查询包含敏感字段，已被拦截"
    return False, None


def normalize_analysis_plan(plan, query=""):
    query = _ensure_unicode(query or u"")
    result = dict(plan or {})
    result.setdefault("query", query)
    result.setdefault("status", "ok")
    result.setdefault("intent", "metric_query")
    result.setdefault("source", "fallback")
    result.setdefault("confidence", 0.5)
    result.setdefault("model", "order_detail")
    result.setdefault("metric", "gmv")
    result.setdefault("metrics", [])
    result.setdefault("dimensions", [])
    result.setdefault("filters", [])
    result.setdefault("task_steps", [])
    result.setdefault("fallback_policy", {})
    result.setdefault("verification_policy", {})
    result.setdefault("schema_version", "v1")
    result.setdefault("plan_version", "v1")
    result.setdefault("memory_refs", [])
    result.setdefault("diagnostics", {})
    result.setdefault("resume_payload", {})
    result.setdefault("task_type", infer_task_type(query, result.get("intent")) or DESCRIPTIVE)
    result.setdefault("execution_mode", "plan_act")
    blocked, reason = detect_blocked_query(query)
    if blocked:
        result["status"] = "blocked"
        result["blocked_reason"] = reason
    else:
        result.setdefault("blocked_reason", None)
    if "time_range" not in result:
        result["time_range"] = parse_time_range_label("yesterday")
    if not result.get("metrics"):
        result["metrics"] = [result.get("metric", "gmv")]
    return AnalysisPlan(
        query=result.get("query"),
        status=result.get("status"),
        intent=result.get("intent"),
        source=result.get("source"),
        confidence=result.get("confidence"),
        model=result.get("model"),
        metric=result.get("metric"),
        metrics=result.get("metrics"),
        dimensions=result.get("dimensions"),
        filters=result.get("filters"),
        time_range=result.get("time_range"),
        clarification=result.get("clarification"),
        blocked_reason=result.get("blocked_reason"),
        task_steps=result.get("task_steps"),
        fallback_policy=result.get("fallback_policy"),
        verification_policy=result.get("verification_policy"),
        schema_version=result.get("schema_version"),
        plan_version=result.get("plan_version"),
        memory_refs=result.get("memory_refs"),
        diagnostics=result.get("diagnostics"),
        task_id=result.get("task_id"),
        parent_task_id=result.get("parent_task_id"),
        resume_payload=result.get("resume_payload"),
        task_type=result.get("task_type"),
        execution_mode=result.get("execution_mode"),
    )


def ensure_plan_defaults(plan, query=""):
    return normalize_analysis_plan(plan, query).to_dict()


__all__ = ["parse_time_range_label", "detect_blocked_query", "build_clarification", "normalize_analysis_plan", "ensure_plan_defaults"]
