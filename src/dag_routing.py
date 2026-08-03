# -*- coding: utf-8 -*-
"""DAG routing implementation.

This module owns the DAG routing logic that was previously embedded in
`dag_agent.py`.
"""

import datetime as dt
import json
import logging

try:
    unicode
except NameError:  # pragma: no cover - Python 3 compatibility
    unicode = str


def _to_unicode(value):
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
            return u"%s" % value

try:
    import structlog
    logger = structlog.get_logger("dag_routing")
except ImportError:
    logger = logging.getLogger("dag_routing")

DEEPSEEK_BASE = ""
DEEPSEEK_KEY = ""
ROUTER_MODEL = ""

from regex_router import RegexRouter
from router_core import build_clarification, ensure_plan_defaults, normalize_analysis_plan, parse_time_range_label
from semantic_utils import DANGEROUS, SENSITIVE, load_semantic_layer, yaml
from strategy_service import get_strategy_service
from task_types import ANOMALY, ATTRIBUTION, COMPARISON, DESCRIPTIVE, FUNNEL, FORECAST, RETENTION, infer_task_type, is_supported_execution
from intent_engine import IntentEngine
from model_routing import choose_model_for_stage, TIER_NO_LLM

SEM = load_semantic_layer() if yaml else None
router = RegexRouter()
intent_engine = IntentEngine(semantic_layer=SEM)
strategy_service = get_strategy_service()


def _attach_model_routing(data, query, decision):
    if data is None:
        data = {}
    if decision is None:
        return data
    decision_data = decision.to_dict() if hasattr(decision, "to_dict") else dict(decision)
    diagnostics = data.setdefault("diagnostics", {})
    diagnostics["model_routing"] = decision_data
    data["model_routing"] = decision_data
    data.setdefault("llm_policy", decision_data.get("llm_policy"))
    return data


REACT_TRIGGER_TOKENS = [
    u"探索", u"探查", u"看看数据", u"看看有哪些", u"有哪些表", u"有哪些字段",
    u"未知", u"先看", u"逐步", u"一步步", u"下钻", u"深挖", u"根因", u"为什么",
]


def infer_execution_mode(query, task_type=None, confidence=None, status=None):
    """Choose runtime mode from business intent, not implementation preference.

    Most business metric questions are deterministic and should use plan_act.
    ReAct is reserved for exploration / anomaly deep-dive where the next step
    depends on the previous observation.
    """
    q = _to_unicode(query)
    if status and status != "ok":
        if any(token in q for token in REACT_TRIGGER_TOKENS):
            return "react"
        return "plan_act"
    if task_type in (ANOMALY, ATTRIBUTION):
        return "react"
    if any(token in q for token in REACT_TRIGGER_TOKENS):
        return "react"
    try:
        if confidence is not None and float(confidence) < 0.55:
            return "react"
    except Exception:
        pass
    return "plan_act"


def _extract_metrics(query):
    """Return all matched metric IDs as a list.
    
    Returns a list of (metric_id, score) tuples sorted by score descending.
    """
    query = _to_unicode(query).lower()
    if not SEM:
        return []
    matches = []
    for metric_id, metric in SEM["metrics"].items():
        synonyms = [metric_id] + list(metric.get("synonyms", []))
        for synonym in synonyms:
            candidate = _to_unicode(synonym).lower().strip()
            if not candidate:
                continue
            if candidate in query:
                score = (len(candidate), 1 if candidate == metric_id.lower() else 0)
                matches.append((metric_id, score))
                break
    # Sort by score descending, then by metric_id for determinism
    matches.sort(key=lambda x: (-x[1][0], -x[1][1], x[0]))
    return matches


def _extract_metric(query):
    """Return the single best matching metric ID.
    
    Kept for backward compatibility. Delegates to _extract_metrics.
    """
    matches = _extract_metrics(query)
    return matches[0][0] if matches else None


def _extract_dimensions(query):
    query = _to_unicode(query)
    dims = []
    if not SEM:
        return dims
    for dim_id, dim in SEM["dimensions"].items():
        synonyms = dim.get("synonyms", [])
        if any(_to_unicode(s).lower() in query.lower() for s in synonyms):
            if dim_id not in dims:
                dims.append(dim_id)
    if (u"渠道" in query or u"按渠道" in query or u"各渠道" in query) and "channel" not in dims:
        dims.append("channel")
    if (u"大区" in query or u"区域" in query or u"地区" in query) and "region" not in dims:
        dims.append("region")
    if (u"品类" in query or u"类目" in query or u"分类" in query) and "category" not in dims:
        dims.append("category")
    if (u"日期" in query or u"按天" in query or u"每天" in query or u"趋势" in query) and "date" not in dims:
        dims.append("date")
    return dims


def _detect_time_label(query):
    query = _to_unicode(query)
    if u"最近30天" in query or u"近30天" in query or u"过去30天" in query or u"近一个月" in query or u"最近一个月" in query:
        return "last30d"
    if u"最近14天" in query or u"近两周" in query or u"过去两周" in query:
        return "last14d"
    if u"最近7天" in query or u"近7天" in query or u"过去7天" in query or u"近一周" in query or u"最近一周" in query:
        return "last7d"
    if u"本周" in query or u"这周" in query:
        return "this_week"
    if u"本月" in query or u"这个月" in query:
        return "this_month"
    if u"上周" in query:
        return "last_week"
    if u"昨天" in query or u"昨日" in query:
        return "yesterday"
    return "yesterday"


def _choose_model(metric, dims, query):
    if not SEM:
        return "order_detail"
    query = _to_unicode(query)
    candidates = list(SEM["models"].values())
    if any(token in query for token in [u"用户", u"会员", u"客户", u"user"]):
        candidates.sort(key=lambda m: 0 if m.get("id") == "user_summary" else 1)
    if any(token in query for token in [u"品类", u"类目", u"商品", u"产品", u"category", u"product"]):
        candidates.sort(key=lambda m: 0 if m.get("id") == "product_analysis" else 1)
    dim_set = set(dims or [])
    for model in candidates:
        visible = set(model.get("visible_dimensions", []))
        if dim_set.issubset(visible):
            return model.get("id")
    return None


def _needs_clarification(query, metric, dims):
    q = _to_unicode(query)
    if metric:
        return False
    if any(token in q for token in [u"口径", u"看看", u"分析", u"帮我", u"请帮", u"请看", u"销售"]):
        return True
    if len(q.strip()) <= 4:
        return True
    return False


def route_node(state):
    """Route the query with the RegexRouter and persist standard fields."""
    query = _to_unicode(state.get("query", ""))
    result = router.route(query)
    state["intent"] = result.intent
    state["time_range"] = result.time_range
    state["dims"] = result.dims
    state["metric"] = result.metric
    state["model"] = result.model
    state["filter_dim"] = result.filter_dim
    state["filter_val"] = result.filter_val
    state["status"] = "blocked" if result.intent == "blocked" else "ok"
    state["blocked_reason"] = getattr(result, "_blocked_reason", None)
    state["_routing"] = {
        "intent": result.intent,
        "time_range": result.time_range,
        "dims": result.dims,
        "metric": result.metric,
        "model": result.model,
    }
    return state


def _route_llm(query, model_name=None, token_budget=None):
    """LLM-based routing via governed model-tier selection."""
    try:
        from urllib import request as urllib_request  # Python 3
    except ImportError:  # pragma: no cover - Python 2.7 fallback
        import urllib2 as urllib_request

    if not DEEPSEEK_KEY:
        return router.route(query).__dict__

    models_summary = json.dumps(
        {k: {"base_table": v["base_table"], "visible_dimensions": v.get("visible_dimensions", [])} for k, v in SEM["models"].items()},
        ensure_ascii=False,
    )
    prompt = {
        "model": model_name or ROUTER_MODEL,
        "messages": [
            {"role": "system", "content": "You are a query router. Available models: %s" % models_summary},
            {"role": "user", "content": query},
        ],
        "max_tokens": int(token_budget or 512),
        "temperature": 0.0,
        "response_format": {"type": "json_object"},
    }
    req = urllib_request.Request(
        "%s/chat/completions" % DEEPSEEK_BASE,
        data=json.dumps(prompt).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer %s" % DEEPSEEK_KEY},
    )
    try:
        resp = urllib_request.urlopen(req, timeout=15)
        try:
            result = json.loads(resp.read().decode("utf-8"))
        finally:
            try:
                resp.close()
            except Exception:
                pass
        plan = json.loads(result["choices"][0]["message"]["content"])
        now = dt.datetime.now()
        tr = plan.get("time_range", "yesterday")
        if tr == "last7d":
            start = now - dt.timedelta(days=7)
        elif tr == "last30d":
            start = now - dt.timedelta(days=30)
        else:
            start = now - dt.timedelta(days=1)
        plan["time_range"] = (start.replace(hour=0, minute=0, second=0, microsecond=0), now.replace(hour=0, minute=0, second=0, microsecond=0))
        plan["status"] = "ok"
        return ensure_plan_defaults(plan, query)
    except Exception as e:
        logger.warning("bare_exception_caught", error=str(e))
        return ensure_plan_defaults(router.route(query).__dict__, query)


def _normalize_plan(plan, query):
    """Normalize rule-based plan to standard format."""
    query = _to_unicode(query)
    now = dt.datetime.now()
    start = now - dt.timedelta(days=1)
    if any(w in query for w in ["??7?", "?7?", "???"]):
        start = now - dt.timedelta(days=7)
    elif any(w in query for w in ["??30?", "?30?", "????"]):
        start = now - dt.timedelta(days=30)
    elif any(w in query for w in ["??", "???"]):
        start = now.replace(day=1)
    start = start.replace(hour=0, minute=0, second=0, microsecond=0)
    end = now.replace(hour=0, minute=0, second=0, microsecond=0)
    blocked = None
    if any(w in query.lower() for w in DANGEROUS):
        blocked = u"您的查询包含危险操作，仅支持只读数据查询"
    if any(w in query.lower() for w in SENSITIVE):
        blocked = u"您的查询包含敏感字段，已被拦截"
    result = {
        "status": "blocked" if blocked else "ok",
        "intent": plan.get("intent", "metric_query"),
        "model": plan.get("model", "order_detail"),
        "metric": plan.get("metric", "gmv"),
        "dimensions": plan.get("dimensions", []),
        "time_range": (start, end),
        "clarification": plan.get("clarification"),
        "blocked_reason": blocked,
        "source": plan.get("source", "rule"),
        "confidence": plan.get("confidence", 0.8),
        "rule_name": plan.get("rule_name", ""),
        "task_type": plan.get("task_type") or infer_task_type(query, plan.get("intent")),
    }
    for k in ("filter_dim", "filter_val", "metrics", "merge_on", "order"):
        if k in plan:
            result[k] = plan[k]
    return ensure_plan_defaults(result, query)


def route_and_plan(query, use_llm=False, llm_policy=None):
    """Route a query to produce an execution plan."""
    query = _to_unicode(query)
    strategy_service.groups()
    model_decision = choose_model_for_stage(query, stage="route", use_llm=use_llm, llm_policy=llm_policy)

    if model_decision.tier != TIER_NO_LLM and not model_decision.shadow:
        plan = _route_llm(query, model_name=model_decision.model, token_budget=model_decision.token_budget)
        plan = normalize_analysis_plan(plan, query)
        plan.diagnostics["route_path"] = "llm"
        plan.diagnostics.setdefault("execution_mode_reason", "llm_route_default")
        data = plan.to_dict()
        data["execution_mode"] = infer_execution_mode(query, data.get("task_type"), data.get("confidence"), data.get("status"))
        return _attach_model_routing(data, query, model_decision)

    blocked, reason = None, None
    if any(word in query.lower() for word in DANGEROUS):
        blocked = True
        reason = u"您的查询包含危险操作，仅支持只读数据查询"
    elif any(word in query.lower() for word in SENSITIVE):
        blocked = True
        reason = u"您的查询包含敏感字段，已被拦截"
    if blocked:
        plan = normalize_analysis_plan({
            "status": "blocked",
            "intent": "blocked",
            "model": "order_detail",
            "metric": "gmv",
            "dimensions": [],
            "time_range": parse_time_range_label("yesterday"),
            "blocked_reason": reason,
            "source": "rule",
            "task_type": DESCRIPTIVE,
        }, query)
        plan.diagnostics["route_path"] = "governance"
        data = plan.to_dict()
        data["execution_mode"] = "plan_act"
        return _attach_model_routing(data, query, model_decision)

    intent_result = intent_engine.parse(query)
    all_metrics = [(m, (len(m), 1)) for m in intent_result.get("metrics", [])]
    metric = intent_result.get("metric")
    dims = intent_result.get("dimensions") or []
    time_label = intent_result.get("time_range_label") or _detect_time_label(query)
    time_range = parse_time_range_label(time_label)
    task_type = intent_result.get("task_type") or infer_task_type(query)

    if intent_result.get("status") in ("need_clarification", "unsupported", "blocked", "no_answer"):
        plan = normalize_analysis_plan({
            "status": intent_result.get("status"),
            "intent": intent_result.get("intent"),
            "model": _choose_model(metric, dims, query) or "order_detail",
            "metric": metric or "gmv",
            "metrics": intent_result.get("metrics") or ([metric] if metric else ["gmv"]),
            "dimensions": dims,
            "time_range": time_range,
            "clarification": intent_result.get("clarification"),
            "blocked_reason": intent_result.get("blocked_reason"),
            "source": "intent_engine",
            "confidence": intent_result.get("confidence", 0.5),
            "task_type": DESCRIPTIVE if intent_result.get("task_type") == "unsupported" else intent_result.get("task_type", DESCRIPTIVE),
            "diagnostics": {
                "intent_parse": intent_result,
                "route_path": intent_result.get("route_path"),
                "matched_rules": intent_result.get("matched_rules", []),
                "missing_slots": intent_result.get("missing_slots", []),
                "ambiguities": intent_result.get("ambiguities", []),
                "chain_hint": intent_result.get("chain_hint", []),
            },
        }, query)
        data = plan.to_dict()
        data["execution_mode"] = infer_execution_mode(query, data.get("task_type"), data.get("confidence"), data.get("status"))
        return _attach_model_routing(data, query, model_decision)

    # Multi-metric detection: if 2+ metrics matched, enable multi_metric mode
    multi_metric = len(all_metrics) >= 2
    metric_ids = [m[0] for m in all_metrics] if multi_metric else ([metric] if metric else [])

    if task_type in (FUNNEL, RETENTION, FORECAST) or (metric is None and any(token in query for token in [u"留存", u"归因", u"漏斗", u"预测"])):
        plan = normalize_analysis_plan({
            "status": "unsupported",
            "intent": "unsupported",
            "model": "order_detail",
            "metric": metric or "gmv",
            "dimensions": dims,
            "time_range": time_range,
            "source": "rule",
            "task_type": DESCRIPTIVE,
            "diagnostics": {"reason": "unsupported_capability"},
        }, query)
        plan.diagnostics["route_path"] = "unsupported"
        data = plan.to_dict()
        data["execution_mode"] = "plan_act"
        return _attach_model_routing(data, query, model_decision)

    if metric is None and _needs_clarification(query, metric, dims):
        clarification_metric = metric or "gmv"
        plan = normalize_analysis_plan({
            "status": "need_clarification",
            "intent": "clarification",
            "model": "order_detail",
            "metric": clarification_metric,
            "dimensions": dims,
            "time_range": time_range,
            "clarification": build_clarification(
                metric=clarification_metric,
                reason=u"信息不足，需要用户确认分析口径",
                expected_next_step=u"用户补充指标或选择维度后继续分析",
            ),
            "source": "rule",
            "task_type": DESCRIPTIVE,
        }, query)
        plan.diagnostics["route_path"] = "clarification"
        data = plan.to_dict()
        data["execution_mode"] = "plan_act"
        return _attach_model_routing(data, query, model_decision)

    if metric == "gmv" and u"口径" in query:
        plan = normalize_analysis_plan({
            "status": "need_clarification",
            "intent": "clarification",
            "model": _choose_model(metric, dims, query) or "order_detail",
            "metric": metric,
            "dimensions": dims,
            "time_range": time_range,
            "clarification": build_clarification(
                metric=metric,
                reason=u"用户询问 GMV 口径，需要确认统计方式",
                expected_next_step=u"用户选择总量或拆分后继续分析",
            ),
            "source": "rule",
            "task_type": DESCRIPTIVE,
        }, query)
        plan.diagnostics["route_path"] = "clarification"
        data = plan.to_dict()
        data["execution_mode"] = "plan_act"
        return _attach_model_routing(data, query, model_decision)

    model = _choose_model(metric, dims, query)
    if not model:
        plan = normalize_analysis_plan({
            "status": "need_clarification",
            "intent": "clarification",
            "model": "order_detail",
            "metric": metric or "gmv",
            "dimensions": dims,
            "time_range": time_range,
            "clarification": build_clarification(
                metric=metric or "gmv",
                reason=u"没有可用的 AI 单表模型覆盖这些维度",
                expected_next_step=u"调整维度或切换主题后继续分析",
            ),
            "source": "rule",
            "task_type": DESCRIPTIVE,
        }, query)
        plan.diagnostics["route_path"] = "clarification"
        data = plan.to_dict()
        data["execution_mode"] = "plan_act"
        return _attach_model_routing(data, query, model_decision)

    intent = intent_result.get("intent") or "metric_query"
    plan_task_type = intent_result.get("task_type") or DESCRIPTIVE

    if metric is None:
        metric = "gmv"

    plan = {
        "status": "ok",
        "intent": intent,
        "model": model,
        "metric": metric,
        "metrics": metric_ids if multi_metric else [metric],
        "dimensions": dims,
        "time_range": time_range,
        "source": "intent_engine",
        "confidence": intent_result.get("confidence", 0.95),
        "task_type": plan_task_type,
        "diagnostics": {
            "intent_parse": intent_result,
            "route_path": intent_result.get("route_path"),
            "matched_rules": intent_result.get("matched_rules", []),
            "missing_slots": intent_result.get("missing_slots", []),
            "ambiguities": intent_result.get("ambiguities", []),
            "chain_hint": intent_result.get("chain_hint", []),
        },
    }
    if multi_metric:
        plan["multi_metric"] = True
    if intent == "comparison":
        plan["previous_time_range"] = None
        plan["compare_time_range"] = None
    plan["execution_mode"] = infer_execution_mode(query, plan_task_type, plan.get("confidence"), plan.get("status"))
    plan["diagnostics"]["execution_mode_reason"] = "business_intent_policy"
    normalized = normalize_analysis_plan(plan, query)
    route_path = intent_result.get("route_path") or "intent_engine"
    if route_path == "intent_engine.rule":
        # Preserve the legacy deterministic router contract: regex/rule routes
        # are exposed as router_node even though IntentEngine now owns parsing.
        route_path = "router_node"
    normalized.diagnostics["route_path"] = route_path
    normalized.diagnostics["execution_mode_reason"] = "business_intent_policy"
    return _attach_model_routing(normalized.to_dict(), query, model_decision)


__all__ = ["route_node", "route_and_plan", "infer_execution_mode"]
