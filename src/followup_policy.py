# -*- coding: utf-8 -*-
"""Declarative follow-up intent and context inheritance policy."""
from __future__ import unicode_literals

try:
    unicode
except NameError:
    unicode = str

FOLLOWUP_INTENTS = (
    "drill_down", "filter_override", "time_override", "dimension_override",
    "comparison_request", "explain_more", "new_topic",
)

# True means inherit from the previous completed task unless the follow-up patch
# explicitly supplies an override. New topics inherit nothing.
INHERITANCE_POLICY = {
    "drill_down": {"metric": True, "dimensions": False, "filters": True, "time_range": True, "task_type": True},
    "filter_override": {"metric": True, "dimensions": True, "filters": True, "time_range": True, "task_type": True},
    "time_override": {"metric": True, "dimensions": True, "filters": True, "time_range": False, "task_type": True},
    "dimension_override": {"metric": True, "dimensions": False, "filters": True, "time_range": True, "task_type": True},
    "comparison_request": {"metric": True, "dimensions": True, "filters": True, "time_range": True, "task_type": False},
    "explain_more": {"metric": True, "dimensions": True, "filters": True, "time_range": True, "task_type": True},
    "new_topic": {"metric": False, "dimensions": False, "filters": False, "time_range": False, "task_type": False},
}

FOLLOW_UP_RULES = [
    {"name": "metric_order_count", "keywords": ["换成", "订单数"], "patch": {"intent": "metric_query", "metric": "order_count", "state": "follow_up", "followup_intent": "new_topic"}},
    {"name": "metric_aov", "keywords": ["换成", "客单价"], "patch": {"intent": "metric_query", "metric": "aov", "state": "follow_up", "followup_intent": "new_topic"}},
    {"name": "metric_avg_price", "keywords": ["换成", "均价"], "patch": {"intent": "metric_query", "metric": "avg_price", "state": "follow_up", "followup_intent": "new_topic"}},
    {"name": "breakdown_channel", "keywords": ["渠道"], "patch": {"intent": "breakdown", "dimensions": ["channel"], "state": "drill_down", "followup_intent": "drill_down"}},
    {"name": "breakdown_region", "keywords": ["大区"], "patch": {"intent": "breakdown", "dimensions": ["region"], "state": "drill_down", "followup_intent": "dimension_override"}},
    {"name": "filter_region_east", "keywords": ["华东"], "patch": {"intent": "filter_replace", "filters": {"region": "华东"}, "state": "follow_up", "followup_intent": "filter_override"}},
    {"name": "filter_channel_taobao", "keywords": ["淘宝"], "patch": {"intent": "filter_replace", "filters": {"channel": "淘宝"}, "state": "follow_up", "followup_intent": "filter_override"}},
    {"name": "breakdown_category", "keywords": ["品类"], "patch": {"intent": "breakdown", "dimensions": ["category"], "state": "drill_down", "followup_intent": "dimension_override"}},
    {"name": "compare_previous_month", "keywords": ["上月", "比"], "patch": {"intent": "compare_periods", "task_type": "comparison", "compare_to": "previous_month", "state": "follow_up", "followup_intent": "comparison_request"}},
    {"name": "compare_previous_week", "keywords": ["上周", "比"], "patch": {"intent": "compare_periods", "task_type": "comparison", "compare_to": "previous_week", "state": "follow_up", "followup_intent": "comparison_request"}},
    {"name": "compare_yesterday", "keywords": ["昨天", "比"], "patch": {"intent": "compare_periods", "task_type": "comparison", "compare_to": "yesterday", "state": "follow_up", "followup_intent": "comparison_request"}},
    {"name": "time_last_30_days", "keywords": ["最近30天"], "patch": {"time_range": "last_30_days", "state": "follow_up", "followup_intent": "time_override"}},
    {"name": "explain_more", "keywords": ["解释"], "patch": {"intent": "explain", "state": "follow_up", "followup_intent": "explain_more"}},
    {"name": "continue_context", "keywords": ["继续"], "patch": {"intent": "follow_up", "state": "follow_up", "followup_intent": "explain_more"}},
]


def _to_unicode(value):
    if isinstance(value, unicode): return value
    if value is None: return u""
    try: return value.decode("utf-8")
    except AttributeError: return unicode(value)


def register_follow_up_rule(name, keywords, patch, priority=None):
    rule = {"name": name, "keywords": list(keywords or []), "patch": dict(patch or {}), "priority": priority if priority is not None else len(FOLLOW_UP_RULES)}
    FOLLOW_UP_RULES.append(rule)
    return rule


def list_follow_up_rules(): return [dict(rule) for rule in FOLLOW_UP_RULES]


def _last_context(session):
    last = session.last_turn() if session is not None else None
    result = last.result if last and last.result else {}
    ctx = getattr(session, "context", {}) or {}
    return {"model": ctx.get("model") or result.get("model"), "metric": ctx.get("metric") or result.get("metric"), "dimensions": list(ctx.get("dimensions") or result.get("dimensions") or []), "time_range": ctx.get("time_range") or result.get("time_range"), "filters": dict(ctx.get("filters") or result.get("filters") or {}), "task_type": ctx.get("task_type") or result.get("task_type")}


def _matches_rule(text, rule): return all(kw in text for kw in (rule.get("keywords") or []))


def classify_followup_intent(query, session=None):
    text = _to_unicode(query)
    for rule in FOLLOW_UP_RULES:
        if _matches_rule(text, rule): return rule.get("patch", {}).get("followup_intent", "drill_down")
    # Explicit analytical topics indicate a clean pivot, not a short-context continuation.
    if any(kw in text for kw in ["留存", "用户", "复购", "漏斗"]) and not any(kw in text for kw in ["继续", "刚才"]): return "new_topic"
    if session is not None and session.last_turn() and len(text) <= 16 and any(kw in text for kw in ["呢", "那", "换成", "只看", "按", "继续", "对比", "比"]): return "explain_more"
    return "new_topic"


def apply_context_policy(base, patch, followup_intent):
    """Apply explicit overrides while retaining only policy-approved fields."""
    base, patch = dict(base or {}), dict(patch or {})
    policy = INHERITANCE_POLICY.get(followup_intent, INHERITANCE_POLICY["new_topic"])
    resolved = {}
    for key in ["model", "metric", "dimensions", "filters", "time_range", "task_type"]:
        if policy.get(key):
            value = base.get(key)
            if key == "dimensions": value = list(value or [])
            if key == "filters": value = dict(value or {})
            resolved[key] = value
    if patch.get("dimensions") is not None: resolved["dimensions"] = list(patch["dimensions"])
    if patch.get("filters") is not None:
        current = dict(resolved.get("filters") or {}); current.update(patch["filters"]); resolved["filters"] = current
    for key, value in patch.items():
        if key not in ("dimensions", "filters", "followup_intent"): resolved[key] = value
    resolved["followup_intent"] = followup_intent
    return resolved


def detect_follow_up(query, session):
    if session is None or not session.last_turn(): return None
    text, patch, matched_rule = _to_unicode(query), {}, None
    for rule in FOLLOW_UP_RULES:
        if _matches_rule(text, rule): matched_rule, patch = rule, dict(rule.get("patch") or {})
    intent = classify_followup_intent(text, session)
    if intent == "new_topic" and not patch: return None
    resolved = apply_context_policy(_last_context(session), patch, intent)
    if matched_rule is not None: resolved["matched_rule"] = matched_rule.get("name")
    resolved["is_follow_up"] = True
    return resolved


def resolve_followup(query, session): return detect_follow_up(query, session)


def merge_context(query, session):
    follow_up = resolve_followup(query, session)
    if not follow_up: return query
    prefix = []
    for key in ["model", "metric", "dimensions", "time_range", "filters", "task_type", "intent", "compare_to"]:
        if follow_up.get(key): prefix.append("%s=%s" % (key, follow_up[key]))
    return "[context: %s] %s" % ("; ".join(prefix), query) if prefix else query

__all__ = ["detect_follow_up", "resolve_followup", "merge_context", "FOLLOW_UP_RULES", "FOLLOWUP_INTENTS", "INHERITANCE_POLICY", "classify_followup_intent", "apply_context_policy", "register_follow_up_rule", "list_follow_up_rules"]
