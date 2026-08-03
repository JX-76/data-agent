# -*- coding: utf-8 -*-
"""RegexRouter - 纯规则路由,零外部依赖。

把 dag_agent.py 里 150+ 行的正则路由抽出来,独立成模块。
出问题直接看这个文件,不用翻主逻辑。
"""

import re


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


class RouteResult(object):
    """路由结果,固定字段,方便下游使用。"""

    def __init__(self):
        self.intent = "metric_query"
        self.time_range = "yesterday"
        self.dims = []
        self.metric = "gmv"
        self.model = "order_detail"
        self.filter_dim = None
        self.filter_val = None
        self.window_func = None
        self.window_partition = []
        self.window_order = "DESC"
        self.top_n = 0
        self.subquery_type = None
        self.subquery_conditions = []
        self.subquery_target = None
        self.metrics = []
        self.funnel_template = None
        self._blocked_reason = None


class RegexRouter(object):
    """纯正则路由,规则优先,无LLM依赖。"""

    def __init__(self):
        self._time_patterns = [
            (r"昨天|昨日", "yesterday"),
            (r"最近7天|近7天|过去7天|近一周|最近一周", "last_7d"),
            (r"最近30天|近30天|过去30天|近一个月|最近一个月", "last_30d"),
            (r"最近14天|近两周|过去两周", "last_14d"),
            (r"本周|这周", "this_week"),
            (r"本月|这个月", "this_month"),
            (r"上周", "last_week"),
        ]
        self._intent_patterns = [
            (r"(?:同比|环比|上周|上月|本周|本月)\s*(?:对比|比较|vs)?", "compare_periods"),
            (r"(?:和|与|、)\s*(?:GMV|订单数|客单价)", "merge"),
            (r"(?:只看|仅看)\s*\S+", "filter_value"),
            (r"(?:各|每个|按|分)\s*(?:渠道|区域|品类|日期)", "breakdown"),
        ]
        self._dim_patterns = [
            (r"按天|每天|每日|逐日|各天|每一天|日维度|按日期分组", "date"),
            (r"渠道|每个渠道|各渠道|分渠道|按渠道", "channel"),
            (r"地区|区域|大区|每个区域|各区|分区|不同区域", "region"),
            (r"品类|类目|分类|各品类|按品类|不同品类", "category"),
        ]
        self._metric_patterns = [
            (r"ROI|roi", "roi"),
            (r"CPA|cpa", "cpa"),
            (r"转化率|conversion_rate", "conversion_rate"),
            (r"点击率|CTR|ctr", "ctr"),
            (r"曝光量|impressions", "impressions"),
            (r"留存率|retention_rate", "retention_rate"),
            (r"复购率|repurchase_rate", "repurchase_rate"),
            (r"缺货率|stockout_rate", "stockout_rate"),
            (r"履约率|fulfillment_rate", "fulfillment_rate"),
            (r"退货率|return_rate", "return_rate"),
            (r"库存周转率|inventory_turnover", "inventory_turnover"),
            (r"动销率|sell_through_rate", "sell_through_rate"),
            (r"售罄率|sold_out_rate", "sold_out_rate"),
            (r"订单数|订单量|订单数量|单量|orders", "order_count"),
            (r"平均单价|均价|平均价格|件单价|avg_price", "avg_price"),
            (r"客单价|平均订单价|aov", "aov"),
            (r"GMV|gmv|交易额|成交金额|销售额|收入|revenue|成交额|业绩", "gmv"),
        ]
        self._model_patterns = [
            (r"用户|客户|会员", "user_summary"),
            (r"品类|类目|商品|product|category", "product_analysis"),
        ]
        self._window_func_patterns = [
            (r"排名|排行|第几名|名次|排第几", "rank"),
            (r"累计|逐日累计|每日累计|running.?total|累计值", "cumulative"),
            (r"环比|同比|上月|上个月|上周|上周同期", "lag"),
            (r"移动平均|滑动平均|滚动平均|MA\d*", "moving_avg"),
            (r"领先|下一期|下期|前瞻|lead", "lead"),
            (r"分组排名|各组排名|分组排行", "rank"),
        ]
        self._subquery_patterns = [
            (r"(?:购买过|买过|买了).*?(?:又|也|同时).*?(?:购买|买)", "exists"),
            (r"(?:既|不仅).*?(?:又|也|还).*?", "exists"),
            (r"同时(?:购买|包含|拥有)", "exists"),
            (r"存在.*?(?:的|用户|订单|商品)", "exists"),
            (r"(?:在|从|于).*?中.*?(?:选择|查询|筛选|过滤)", "in"),
            (r"(?:交集|共同|重叠).*?(?:用户|客户|订单)", "intersect"),
            (r"(?:至少|最少).*?(?:购买|订单)", "exists"),
            (r"(?:也在|也在其中|也在里面)", "in"),
        ]
        self._subquery_cond_keywords = [r"购买过([\u4e00-\u9fff\w]+)", r"买了([\u4e00-\u9fff\w]+)"]
        self._funnel_patterns = [
            (r"漏斗|转化率|转化路径|流失分析", "custom"),
            (r"购买.*?转化|购物.*?漏斗|浏览.*?加购.*?下单", "ecommerce_purchase"),
            (r"注册.*?转化|注册.*?留存|新用户.*?转化", "user_onboarding"),
            (r"浏览.*?点击.*?转化|内容.*?互动", "content_engagement"),
        ]

    def _match_first(self, text, patterns):
        text = _to_unicode(text)
        for pattern, key in patterns:
            pattern = _to_unicode(pattern)
            if re.search(pattern, text, re.IGNORECASE):
                return key
        return None

    def _extract_multi_metrics(self, query):
        query = _to_unicode(query)
        metrics = []
        for pattern, key in self._metric_patterns:
            if re.search(_to_unicode(pattern), query, re.IGNORECASE):
                metrics.append(key)
        seen = set()
        result = []
        for m in metrics:
            if m not in seen:
                seen.add(m)
                result.append(m)
        return result if result else ["gmv"]

    def route(self, query):
        query = _to_unicode(query)
        result = RouteResult()
        ql = query.lower()
        if any(w in ql for w in [u"删除", u"更新", u"修改", u"写入", u"drop", u"delete", u"update", u"insert", u"alter", u"truncate", u"删库"]):
            result.intent = "blocked"
            result._blocked_reason = "您的查询包含危险操作，仅支持只读数据查询"
            return result
        if any(w in ql for w in [u"salary", u"user_phone", u"phone", u"id_card", u"身份证", u"手机号", u"工资"]):
            result.intent = "blocked"
            result._blocked_reason = "您的查询包含敏感字段，已被拦截"
            return result

        result.time_range = self._match_first(query, self._time_patterns) or u"yesterday"
        for pattern, dim in self._dim_patterns:
            if re.search(pattern, query, re.IGNORECASE) and dim not in result.dims:
                result.dims.append(dim)
        result.metrics = self._extract_multi_metrics(query)
        result.metric = result.metrics[0] if result.metrics else u"gmv"
        result.window_func = self._match_first(query, self._window_func_patterns)
        result.subquery_type = self._match_first(query, self._subquery_patterns)
        result.funnel_template = self._match_first(query, self._funnel_patterns)
        result.model = self._match_first(query, self._model_patterns) or u"order_detail"
        return result


_default_router = RegexRouter()

def route(query):
    return _default_router.route(query)
