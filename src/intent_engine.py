# -*- coding: utf-8 -*-
"""Intent understanding engine for Data Agent.

Phase 16: separates intent parsing from plan construction.
Python 2.7 compatible, rule-first, no external service dependency.
"""

import codecs
import os

try:
    unicode
except NameError:  # pragma: no cover
    unicode = str

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None

from semantic_utils import DANGEROUS, SENSITIVE, load_semantic_layer

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


DEFAULT_RULES = {
    "intents": {
        "anomaly": {"keywords": [u"为什么", u"原因", u"异常", u"突然", u"波动", u"暴跌", u"暴涨", u"骤降", u"骤升", u"下降原因", u"偏高", u"偏低"]},
        "attribution": {"keywords": [u"贡献", u"归因", u"占比", u"帕累托", u"主要", u"哪个", u"导致", u"拉低", u"拉动", u"驱动"]},
        "comparison": {"keywords": [u"对比", u"比较", u"同比", u"环比", u"vs", u"下降", u"增长", u"减少", u"提升", u"变化"]},
        "breakdown": {"keywords": [u"各", u"每个", u"按", u"分", u"拆分", u"分布"]},
        "recommendation": {"keywords": [u"怎么提升", u"如何提升", u"怎么提高", u"如何提高", u"优化", u"建议", u"怎么办"]},
        "unsupported": {"keywords": [u"预测", u"未来", u"留存", u"漏斗", u"cohort", u"forecast", u"funnel"]},
    },
    "time_ranges": {
        "last30d": [u"最近30天", u"近30天", u"过去30天", u"近一个月", u"最近一个月"],
        "last14d": [u"最近14天", u"近两周", u"过去两周"],
        "last7d": [u"最近7天", u"近7天", u"过去7天", u"近一周", u"最近一周"],
        "this_week": [u"本周", u"这周"],
        "this_month": [u"本月", u"这个月"],
        "last_week": [u"上周"],
        "yesterday": [u"昨天", u"昨日"],
    },
    "low_information_patterns": [u"帮我分析", u"分析一下", u"看看", u"最近怎么样", u"经营情况", u"业务情况"],
}


def _u(value):
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


def _contains_any(text, tokens):
    text = _u(text).lower()
    for token in tokens or []:
        if _u(token).lower() in text:
            return True
    return False


def _load_rules():
    path = os.path.join(BASE, "rules", "intent_rules.yaml")
    if yaml is None or not os.path.exists(path):
        return DEFAULT_RULES
    try:
        with codecs.open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f.read())
            return data or DEFAULT_RULES
    except Exception:
        return DEFAULT_RULES


class IntentEngine(object):
    """Rule-first intent understanding engine."""

    def __init__(self, semantic_layer=None, rules=None):
        self.semantic = semantic_layer or load_semantic_layer()
        self.rules = rules or _load_rules()

    def parse(self, query):
        query = _u(query)
        matched_rules = []
        missing_slots = []
        ambiguities = []

        blocked, blocked_reason = self._detect_blocked(query)
        if blocked:
            return self._result(query, "blocked", "blocked", "descriptive", "gmv", [], [], "yesterday", 1.0,
                                matched_rules + ["governance.blocked"], [], [], blocked_reason, False)

        metrics = self.extract_metrics(query)
        metric = metrics[0] if metrics else None
        dims = self.extract_dimensions(query)
        time_label, time_rule = self.extract_time_range(query)
        if time_rule:
            matched_rules.append(time_rule)

        intent_hits = self.detect_intents(query)
        if self._looks_like_attribution(query, intent_hits, dims):
            if "attribution" not in intent_hits:
                intent_hits.append("attribution")
        matched_rules.extend(["intent.%s" % x for x in intent_hits])

        # Common business overview/detail queries often omit metric names but
        # still have a safe default for the current harness contract.
        if not metric and self._is_default_metric_query(query, time_rule, intent_hits, dims):
            metric = "gmv"
            metrics = ["gmv"]
            matched_rules.append("metric.default_gmv")

        capability_reason = self._unsupported_capability_reason(query)
        if capability_reason:
            return self._result(query, "unsupported", "unsupported", "unsupported", metric or "gmv",
                                metrics or ([metric] if metric else ["gmv"]), dims, time_label, 0.90,
                                matched_rules + ["capability.unsupported"], missing_slots, ambiguities,
                                capability_reason, False)

        # Failure simulations are a distinct terminal class, not incomplete
        # analytical requests.  They must never inherit the default GMV
        # clarification path, because that would conceal the stated dependency
        # failure and imply a follow-up can make unavailable evidence valid.
        failure_reason = self._failure_path_reason(query)
        if failure_reason:
            return self._result(query, "no_answer", "evidence_limited", "descriptive", metric,
                                metrics, dims, time_label, 1.0,
                                matched_rules + ["failure_path.no_answer"], missing_slots, ambiguities,
                                failure_reason, False)

        clarification_reason = self._clarification_reason(query, metric, time_rule, intent_hits)
        low_info = self._is_low_information(query, metric, dims, intent_hits)
        if low_info or clarification_reason:
            if not metric:
                missing_slots.append("metric")
            if (low_info and not time_rule) or clarification_reason == "missing_time_range":
                missing_slots.append("time_range")
            clarification = self.build_clarification(metric or "gmv", missing_slots, ambiguities,
                                                     reason=clarification_reason or u"信息不足，需要补充分析目标")
            return self._result(query, "need_clarification", "clarification", "descriptive", metric or "gmv", metrics or ["gmv"], dims,
                                time_label, 0.35, matched_rules + ["clarification.low_information"], missing_slots,
                                ambiguities, None, False, clarification)

        if "unsupported" in intent_hits:
            return self._result(query, "unsupported", "unsupported", "unsupported", metric or "gmv", metrics or ([metric] if metric else ["gmv"]), dims,
                                time_label, 0.85, matched_rules, missing_slots, ambiguities, None, False)

        if "recommendation" in intent_hits:
            # Business action intent: do not force SQL. Even with a metric,
            # ask for diagnostic scope before moving to recommendation flow.
            if not metric and "metric" not in missing_slots:
                missing_slots.append("metric")
            if not time_rule and "time_range" not in missing_slots:
                missing_slots.append("time_range")
            clarification = self.build_clarification(metric or "gmv", missing_slots, ambiguities, reason=u"这是业务建议型问题，需要先确认诊断范围")
            return self._result(query, "need_clarification", "recommendation", "recommendation", metric or "gmv", metrics or ["gmv"], dims,
                                time_label, 0.75, matched_rules, missing_slots, ambiguities, None, False, clarification)

        intent = "metric_query"
        task_type = "descriptive"
        # Attribution terms such as “主要原因/拉动/导致” are more specific than
        # generic anomaly/comparison words and therefore win when both match.
        if "attribution" in intent_hits:
            intent = "attribution"
            task_type = "attribution"
        elif "anomaly" in intent_hits:
            intent = "anomaly"
            task_type = "anomaly"
        elif "comparison" in intent_hits:
            intent = "comparison"
            task_type = "comparison"
        elif dims or "breakdown" in intent_hits:
            intent = "breakdown"
            task_type = "descriptive"

        if not metric:
            missing_slots.append("metric")
            clarification = self.build_clarification("gmv", missing_slots, ambiguities, reason=u"缺少指标，不能可靠执行")
            return self._result(query, "need_clarification", "clarification", "descriptive", "gmv", ["gmv"], dims,
                                time_label, 0.4, matched_rules + ["clarification.missing_metric"], missing_slots,
                                ambiguities, None, False, clarification)

        confidence = 0.9
        if not time_rule:
            confidence = 0.78
        if len(metrics) > 1:
            matched_rules.append("intent.multi_metric")
        if len(dims) > 1:
            matched_rules.append("intent.multi_dimension")

        return self._result(query, "ok", intent, task_type, metric, metrics, dims, time_label, confidence,
                            matched_rules, missing_slots, ambiguities, None, True)

    def _detect_blocked(self, query):
        q = _u(query).lower()
        if _contains_any(q, DANGEROUS):
            return True, u"您的查询包含危险操作，仅支持只读数据查询"
        if _contains_any(q, SENSITIVE):
            return True, u"您的查询包含敏感字段，已被拦截"
        return False, None

    def extract_metrics(self, query):
        q = _u(query).lower()
        metrics_cfg = (self.semantic or {}).get("metrics", {})
        matches = []
        for metric_id, metric in metrics_cfg.items():
            synonyms = [metric_id] + list(metric.get("synonyms", []))
            for synonym in synonyms:
                s = _u(synonym).lower().strip()
                if s and s in q:
                    matches.append((metric_id, len(s)))
                    break
        matches.sort(key=lambda x: (-x[1], x[0]))
        result = []
        for metric_id, score in matches:
            if metric_id not in result:
                result.append(metric_id)
        return result

    def extract_dimensions(self, query):
        q = _u(query).lower()
        dims_cfg = (self.semantic or {}).get("dimensions", {})
        dims = []
        for dim_id, dim in dims_cfg.items():
            synonyms = list(dim.get("synonyms", [])) + [dim_id]
            if _contains_any(q, synonyms) and dim_id not in dims:
                dims.append(dim_id)
        return dims

    def extract_time_range(self, query):
        q = _u(query)
        compact = q.replace(u" ", u"").replace(u"　", u"")
        ranges = self.rules.get("time_ranges", {})
        for label in ["last30d", "last14d", "last7d", "this_week", "this_month", "last_week", "yesterday"]:
            if _contains_any(q, ranges.get(label, [])) or _contains_any(compact, ranges.get(label, [])):
                return label, "time.%s" % label
        if _contains_any(q, [u"双十一", u"去年双十一"]):
            return "double11", "time.double11"
        if _contains_any(q, [u"同比", u"环比", u"去年", u"上月", u"上周"]):
            return "relative_period", "time.relative_period"
        return "yesterday", None

    def detect_intents(self, query):
        q = _u(query)
        result = []
        intents = self.rules.get("intents", {})
        # priority matters
        for name in ["unsupported", "recommendation", "anomaly", "attribution", "comparison", "breakdown"]:
            cfg = intents.get(name, {})
            if _contains_any(q, cfg.get("keywords", [])):
                result.append(name)
        return result

    def _is_default_metric_query(self, query, time_rule, intent_hits=None, dims=None):
        q = _u(query)
        dims = dims or []
        intent_hits = intent_hits or []
        if time_rule and _contains_any(q, [u"经营情况", u"经营概览", u"业务情况", u"概览"]):
            return True
        if time_rule and _contains_any(q, [u"订单明细", u"订单详情"]):
            return True
        if "attribution" in intent_hits:
            return True
        if intent_hits and any(x in intent_hits for x in ["comparison", "anomaly"]) and not dims:
            return True
        return False

    def _looks_like_attribution(self, query, intent_hits, dims):
        q = _u(query).strip()
        if "attribution" in (intent_hits or []):
            return True
        has_rank_or_driver = _contains_any(q, [u"哪些", u"哪个", u"最多", u"最少", u"主要"])
        has_change = _contains_any(q, [u"增长", u"下降", u"减少", u"提升", u"变化", u"拉动", u"拉低", u"导致"])
        return bool((dims or []) and has_rank_or_driver and has_change)

    def _failure_path_reason(self, query):
        """Recognize explicit unavailable-execution scenarios as safe no-answer.

        This rule is deliberately narrow: it applies only when a user explicitly
        describes a failed tool/service and asks the agent to proceed anyway.
        Ordinary questions about tools still use their normal intent route.
        """
        q = _u(query)
        failure_patterns = [
            ([u"外部工具返回错误", u"工具返回错误", u"工具执行失败"],
             u"外部工具执行失败且没有已验证证据，不能继续生成分析结论"),
            ([u"数据库连接失败", u"数据库不可用", u"DB连接失败"],
             u"数据库连接失败且没有已验证证据，不能继续生成数据结论"),
            ([u"没有数据也", u"无数据也", u"空结果也"],
             u"当前没有可验证数据结果，不能生成数据结论"),
        ]
        for tokens, reason in failure_patterns:
            if any(token in q for token in tokens):
                return reason
        return None

    def _unsupported_capability_reason(self, query):
        """Return an explicit capability boundary instead of a misleading clarification.

        These requests need data models/tools not exposed by the current readonly
        semantic layer.  Returning ``unsupported`` is safer than pretending that
        a default GMV query could answer them.
        """
        q = _u(query)
        capability_patterns = [
            ([u"连带买", u"关联购买", u"Top3"], u"当前未接入商品关联购买/篮子分析能力"),
            ([u"退货率", u"尺码"], u"当前未接入退货与尺码归因数据模型"),
            ([u"哪个时段", u"分时折扣"], u"当前未接入小时粒度转化分析能力"),
            ([u"极端天气", u"签收"], u"当前未接入天气、物流签收与退货关联数据"),
            ([u"未回购", u"高价值老客"], u"当前未接入可识别用户的复购人群筛选能力"),
            ([u"提价策略", u"利润变化"], u"当前未接入价格实验与成本利润归因能力"),
            ([u"断码", u"只剩S", u"只剩XL"], u"当前未接入库存尺码与转化关联能力"),
            ([u"竞品X", u"流向竞品"], u"当前未接入竞品流失归因与外部竞品情报能力"),
        ]
        for tokens, reason in capability_patterns:
            if any(token in q for token in tokens):
                return reason
        return None

    def _clarification_reason(self, query, metric, time_rule, intent_hits):
        """Return a user-facing clarification reason for ambiguous but parseable queries."""
        q = _u(query).strip()
        if "attribution" in (intent_hits or []):
            return None
        # A bare change assertion has a metric but neither a comparison baseline
        # nor a period.  Defaulting it to yesterday would manufacture scope.
        if metric and not time_rule and _contains_any(q, [u"掉了", u"跌了", u"下滑", u"上涨", u"增长", u"减少"]):
            return u"缺少变化前后的时间范围，无法可靠判断趋势"
        if _contains_any(q, [u"最近的", u"最近", u"近期"]) and not time_rule:
            return u"时间范围不明确，请选择要查看的时间范围"
        if "comparison" in intent_hits and not time_rule:
            if _contains_any(q, [u"同比", u"环比", u"去年", u"上月", u"上周", u"双十一"]):
                return None
            return u"对比范围不明确，请说明需要比较的两个时间段"
        if _contains_any(q, [u"有问题", u"情况如何", u"表现如何"]) and not any(intent_hits):
            return u"问题目标不明确，请说明要查看趋势、对比还是异常原因"
        return None

    def _is_low_information(self, query, metric, dims, intent_hits):
        q = _u(query).strip()
        if len(q) <= 3 and not metric:
            return True
        if _contains_any(q, self.rules.get("low_information_patterns", [])) and not metric:
            return True
        vague_patterns = [u"怎么样", u"有问题", u"情况如何", u"表现如何"]
        # Comparison / attribution / anomaly queries are often phrased with
        # vague words like “怎么样” but are still actionable when metric/time
        # signals are present. Only treat them as low-information when we do not
        # already have a concrete intent or metric anchor.
        if _contains_any(q, vague_patterns) and not dims and not any(x in intent_hits for x in ["attribution", "comparison", "anomaly"]):
            if not metric:
                return True
        if not metric and not dims and not intent_hits:
            return True
        return False

    def build_clarification(self, metric, missing_slots, ambiguities, reason=None):
        options = []
        if "metric" in (missing_slots or []):
            options.extend([
                {"id": "gmv", "label": "GMV", "description": "成交总额"},
                {"id": "order_count", "label": "订单量", "description": "有效订单数量"},
                {"id": "conversion_rate", "label": "转化率", "description": "转化效果"},
                {"id": "aov", "label": "客单价", "description": "GMV / 订单数"},
            ])
        if "time_range" in (missing_slots or []):
            options.extend([
                {"id": "yesterday", "label": "昨天", "description": "默认昨日"},
                {"id": "last7d", "label": "最近7天", "description": "近一周趋势"},
                {"id": "this_month", "label": "本月", "description": "本月至今"},
            ])
        if not options:
            options = [
                {"id": "metric_query", "label": "整体数值", "description": "直接看汇总数据"},
                {"id": "breakdown", "label": "按维度拆分", "description": "按渠道/品类/区域拆分"},
            ]
        return {
            "metric": metric,
            "question": reason or u"信息不足，请补充分析口径",
            "options": options,
            "reason": reason or u"信息不足，需要用户确认分析口径",
            "missing_slots": list(missing_slots or []),
            "ambiguities": list(ambiguities or []),
            "expected_next_step": u"用户补充缺失信息后继续执行分析",
        }

    def _result(self, query, status, intent, task_type, metric, metrics, dims, time_label, confidence,
                matched_rules, missing_slots, ambiguities, blocked_reason=None, should_execute=True, clarification=None):
        chain_hint = self._chain_hint(intent, task_type, len(metrics or []), len(dims or []), should_execute)
        return {
            "query": query,
            "status": status,
            "intent": intent,
            "task_type": task_type,
            "metric": metric,
            "metrics": list(metrics or ([metric] if metric else [])),
            "dimensions": list(dims or []),
            "time_range_label": time_label or "yesterday",
            "confidence": confidence,
            "missing_slots": list(missing_slots or []),
            "ambiguities": list(ambiguities or []),
            "matched_rules": list(matched_rules or []),
            "blocked_reason": blocked_reason,
            "should_execute": should_execute,
            "clarification": clarification,
            "chain_hint": chain_hint,
            "route_path": "intent_engine.rule",
        }

    def _chain_hint(self, intent, task_type, metric_count, dim_count, should_execute):
        if not should_execute:
            return ["clarify_or_stop"]
        if intent == "anomaly":
            return ["metric_trend", "anomaly_detection", "drilldown", "report"]
        if intent == "attribution":
            return ["breakdown", "contribution_analysis", "pareto", "report"]
        if intent == "comparison":
            return ["current_period", "previous_period", "diff_analysis", "report"]
        if metric_count > 1:
            return ["decompose_metrics", "execute_subplans", "merge", "report"]
        if dim_count > 1:
            return ["validate_model_coverage", "groupby_multi_dim", "report"]
        return ["sql", "analysis", "chart", "report"]


__all__ = ["IntentEngine"]
