# -*- coding: utf-8 -*-
"""Session-memory resolver for safe, traceable follow-up context."""
from __future__ import unicode_literals

import time

from followup_policy import classify_followup_intent, apply_context_policy, INHERITANCE_POLICY

FOLLOWUP_DECISIONS = (
    "inherit_and_reexecute", "reuse_verified_evidence", "reuse_context_only",
    "need_clarification", "blocked",
)
EVIDENCE_TTL_SECONDS = 300


def _latest(memory, key):
    items = memory.recall(scope="session", key=key) if memory is not None else []
    return items[-1].value if items else {}


def _context_from_result(result):
    result = dict(result or {})
    plan = result.get("plan") or {}
    ledger = result.get("fact_ledger") or {}
    return {
        "model": result.get("model") or plan.get("model"),
        "metric": result.get("metric") or plan.get("metric"),
        "dimensions": list(result.get("dimensions") or plan.get("dimensions") or []),
        "filters": dict(result.get("filters") or plan.get("filters") or {}),
        "time_range": result.get("time_range") or plan.get("time_range") or plan.get("time_range_label"),
        "task_type": result.get("task_type") or plan.get("task_type"),
        "dataid": result.get("dataid") or ledger.get("dataid"),
        "data_version": result.get("data_version") or ledger.get("data_version"),
        "evidence_refs": list(result.get("evidence_refs") or ledger.get("evidence_refs") or []),
        "verified": bool(result.get("verified") or ledger.get("verified") or ledger.get("authority") == "verified_execution"),
        "captured_at": result.get("captured_at") or ledger.get("captured_at"),
        "status": result.get("status"),
    }


def _patch_for_query(query, intent):
    text = query or ""
    if "订单数" in text: return {"metric": "order_count"}
    if "客单价" in text: return {"metric": "aov"}
    if "均价" in text: return {"metric": "avg_price"}
    if "上周" in text and intent == "time_override": return {"time_range": "previous_week"}
    if "昨天" in text and intent == "time_override": return {"time_range": "yesterday"}
    if "淘宝" in text: return {"filters": {"channel": "淘宝"}}
    if "华东" in text: return {"filters": {"region": "华东"}}
    if "品类" in text: return {"dimensions": ["category"]}
    if "渠道" in text: return {"dimensions": ["channel"]}
    if "最近30天" in text: return {"time_range": "last_30_days"}
    if intent == "comparison_request":
        return {"task_type": "comparison", "compare_to": "previous_week" if "上周" in text else "previous_month"}
    return {}


def _is_ambiguous(query):
    text = (query or "").strip()
    return text in (u"继续", u"继续看", u"继续看一下", u"再看一下", u"看一下", u"这个呢", u"那这个呢")


def _evidence_is_fresh(base, now=None, ttl_seconds=EVIDENCE_TTL_SECONDS):
    captured_at = base.get("captured_at")
    if captured_at is None:
        return False
    try:
        return (now or time.time()) - float(captured_at) <= ttl_seconds
    except Exception:
        return False


def _changed_fields(base, resolved):
    changed = []
    for key in ("metric", "time_range", "filters", "task_type", "dataid", "data_version"):
        if resolved.get(key) is not None and base.get(key) != resolved.get(key):
            changed.append(key)
    # A dimension change generally changes grain; old aggregate rows are not reusable.
    if resolved.get("dimensions") is not None and list(base.get("dimensions") or []) != list(resolved.get("dimensions") or []):
        changed.append("dimensions")
    return changed


def _expected_evidence_scope(resolved):
    return {
        "metric": resolved.get("metric"),
        "allowed_time_ranges": [resolved.get("time_range")] if resolved.get("time_range") else [],
        "dimensions": list(resolved.get("dimensions") or []),
        "filters": dict(resolved.get("filters") or {}),
        "dataid": resolved.get("dataid"),
        "data_version": resolved.get("data_version"),
    }


def _validate_reusable_evidence(evidence_bus, base, resolved, now=None, ttl_seconds=EVIDENCE_TTL_SECONDS):
    refs = list(base.get("evidence_refs") or [])
    if not base.get("verified") or not refs:
        return False, ["no_verified_evidence_refs"], []
    if evidence_bus is not None:
        valid, rejected = evidence_bus.validate_scope(
            refs, expected_scope=_expected_evidence_scope(resolved),
            ttl_seconds=ttl_seconds, now=now)
        if valid and not rejected:
            return True, ["scope_compatible_and_evidence_fresh"], []
        reasons = []
        for item in rejected:
            error = item.get("error")
            if error == "evidence_scope_mismatch":
                reasons.append("evidence_scope_mismatch:%s" % ",".join(item.get("fields") or []))
            else:
                reasons.append(error or "evidence_reuse_rejected")
        if not reasons:
            reasons = ["evidence_reuse_rejected"]
        return False, reasons, rejected
    if _evidence_is_fresh(base, now=now, ttl_seconds=ttl_seconds):
        return True, ["scope_compatible_and_evidence_fresh"], []
    return False, ["no_fresh_verified_evidence"], []


def _decision_for_followup(previous, base, patch, resolved, intent, evidence_bus=None, now=None, ttl_seconds=EVIDENCE_TTL_SECONDS):
    if previous.get("status") not in (None, "ok"):
        return "blocked", ["previous_task_not_successful"]
    if _is_ambiguous(patch.get("query_text")):
        return "need_clarification", ["ambiguous_followup_missing_object"]
    required_missing = [key for key in ("metric", "time_range") if not resolved.get(key)]
    if required_missing:
        return "need_clarification", ["missing_required_%s" % key for key in required_missing]
    changed = _changed_fields(base, resolved)
    if changed:
        return "inherit_and_reexecute", ["scope_changed:%s" % ",".join(changed)]
    if intent == "explain_more":
        reusable, evidence_reasons, unused_rejected = _validate_reusable_evidence(
            evidence_bus, base, resolved, now=now, ttl_seconds=ttl_seconds)
        if reusable:
            return "reuse_verified_evidence", evidence_reasons
        return "reuse_context_only", evidence_reasons
    return "inherit_and_reexecute", ["followup_requires_current_execution"]


class FollowupContextResolver(object):
    def __init__(self, memory, clarification_state, session_id, evidence_bus=None,
                 evidence_ttl_seconds=EVIDENCE_TTL_SECONDS):
        self.memory = memory
        self.clarification_state = clarification_state
        self.session_id = session_id
        self.evidence_bus = evidence_bus
        self.evidence_ttl_seconds = evidence_ttl_seconds

    def resolve(self, query):
        if self.clarification_state is not None and self.clarification_state.has_pending(self.session_id):
            return {"blocked_by_pending_clarification": True, "followup_intent": "new_topic", "is_follow_up": False}
        previous = _latest(self.memory, "last_result")
        if not previous:
            return {"is_follow_up": False, "followup_intent": "new_topic", "resolved_query": query, "context_sources": []}
        intent = classify_followup_intent(query)
        base = _context_from_result(previous)
        patch = _patch_for_query(query, intent)
        # Metric-only short turns are contextual scope changes, not unrelated
        # new topics. They must inherit parse context but force re-execution.
        if intent == "new_topic" and patch.get("metric"):
            intent = "filter_override"
        if intent == "new_topic":
            return {"is_follow_up": False, "followup_intent": intent, "resolved_query": query, "parent_task_id": previous.get("task_id"), "context_sources": []}
        patch["query_text"] = query
        resolved = apply_context_policy(base, patch, intent)
        resolved.pop("query_text", None)
        # Preserve non-factual execution identity for resolver decisions only.
        for key in ("dataid", "data_version", "evidence_refs", "verified", "captured_at", "status"):
            if key not in resolved and base.get(key) is not None:
                resolved[key] = base.get(key)
        decision, reasons = _decision_for_followup(
            previous, base, patch, resolved, intent,
            evidence_bus=self.evidence_bus, ttl_seconds=self.evidence_ttl_seconds)
        sources = [key for key in ["metric", "dimensions", "filters", "time_range", "task_type"] if INHERITANCE_POLICY[intent].get(key) and base.get(key) is not None]
        prefix = ["%s=%s" % (key, resolved[key]) for key in ["metric", "dimensions", "filters", "time_range", "task_type", "compare_to"] if resolved.get(key)]
        payload = {"is_follow_up": True, "followup_intent": intent, "decision": decision,
                   "decision_reasons": reasons, "resolved_context": resolved,
                   "overrides": dict((k, v) for k, v in patch.items() if k != "query_text"),
                   "context_sources": sources, "parent_task_id": previous.get("task_id"),
                   "resolved_query": "[context: %s] %s" % ("; ".join(prefix), query) if prefix else query}
        if decision == "need_clarification":
            payload["clarification"] = {"reason": reasons[0] if reasons else "need_clarification",
                                         "question": u"请明确要继续分析的指标、时间范围或筛选对象。"}
        return payload


__all__ = ["FollowupContextResolver", "FOLLOWUP_DECISIONS"]
