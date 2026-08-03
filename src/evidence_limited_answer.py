# -*- coding: utf-8 -*-
"""Useful, bounded responses when live evidence is unavailable.

This module deliberately distinguishes user-supplied facts, operational
hypotheses, and unavailable data.  It never turns a failed SQL/RAG lookup into
an asserted business fact.
"""
from __future__ import unicode_literals

import re


def _contains(query, terms):
    return any(term in (query or u"") for term in terms)


def _topic(query):
    if _contains(query, (u"加购", u"收藏")):
        return (u"加购意向", [u"按渠道、商品/SKU、时段拆分加购量与加购率",
                              u"核对价格、优惠门槛、库存和商品详情页变化",
                              u"对比投放人群、新老客与关键词覆盖变化"])
    if _contains(query, (u"转化", u"成交", u"支付")):
        return (u"支付转化", [u"按流量渠道、商品、时段拆分访客、加购和支付漏斗",
                              u"核对商品价格、权益、库存、详情页和评价变化",
                              u"排查投放人群结构及落地页承接变化"])
    if _contains(query, (u"退款", u"售后", u"差评", u"评价")):
        return (u"售后与口碑", [u"按 SKU、退款原因、评价标签和日期定位异常来源",
                                  u"核对批次质量、尺码/描述偏差和发货时效",
                                  u"区分售后上升与流量/转化变化的时间先后关系"])
    if _contains(query, (u"ROI", u"投产", u"直通车", u"消耗", u"出价")):
        return (u"投放效率", [u"按计划、关键词、人群和时段核对消耗、成交与投产",
                                u"先暂停低效且无增量证据的扩量动作",
                                u"比较预算变化、点击成本和支付转化的贡献"])
    if _contains(query, (u"库存", u"断货", u"出清")):
        return (u"库存与货品", [u"核对可售库存、在途库存、日销和预计售罄天数",
                                u"按毛利、保质期和滞销风险分层制定促销动作",
                                u"确认引流款断货对关联商品流量的影响"])
    return (u"经营问题", [u"按时间、渠道、商品和人群拆分核心指标",
                            u"核对价格、权益、库存、投放和页面变更记录",
                            u"先验证影响最大的变量，再决定经营动作"])


def _user_fact(query):
    """Return only an explicitly stated user fact; never infer one from a DB failure."""
    text = query or u""
    match = re.search(r"(?:是|为|达|跌了|降了|增长了?)\s*([^，。；;！？?]{1,24}(?:%|％|倍|元|件|单))", text)
    if not match:
        return None
    value = match.group(1).strip()
    if u"去年" in text or u"同比" in text:
        return u"你提供的输入是“%s”。该信息尚未由当前数据源核验。" % value
    return u"你提供了“%s”这一输入，以下建议以此为前提，尚未由当前数据源核验。" % value


def build_evidence_limited_answer(query, failure_type=None):
    """Produce a product-useful analysis without asserting unavailable facts."""
    name, checks = _topic(query)
    supplied = _user_fact(query)
    lines = [u"## 受限证据分析", u"当前无法从已接入数据源核验本题所需明细（%s），因此不会把原因或数值表述为已确认事实。" % (failure_type or u"数据暂不可用")]
    if supplied:
        lines.extend([u"\n### 已知输入", u"- %s" % supplied])
    lines.extend([u"\n### %s：建议优先验证" % name])
    for idx, item in enumerate(checks, 1):
        lines.append(u"%d. %s。" % (idx, item))
    lines.extend([u"\n### 可立即执行", u"- 先拉取上述维度最近 7 天及对比周期的数据；按变化幅度和业务影响排序。",
                  u"- 在数据到位前，避免基于未核验归因做大规模预算、价格或库存调整。",
                  u"\n### 最小补数清单", u"需要：日期、商品/SKU、渠道/计划、人群、访客、加购、支付、成交金额，以及价格/优惠/库存变更记录。"])
    return u"\n".join(lines)


__all__ = ["build_evidence_limited_answer"]
