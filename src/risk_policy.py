# -*- coding: utf-8 -*-
"""Risk policy for gating high-impact Data Agent actions.

Python 2.7 compatible, deterministic, and dependency-light.
"""

from __future__ import unicode_literals


class RiskAssessment(object):
    def __init__(self, level='low', reasons=None, requires_human_review=False, confidence=1.0):
        self.level = level
        self.reasons = reasons or []
        self.requires_human_review = requires_human_review
        self.confidence = confidence

    def to_dict(self):
        return {
            'risk_level': self.level,
            'reasons': list(self.reasons),
            'requires_human_review': self.requires_human_review,
            'confidence': self.confidence,
        }


class RiskPolicy(object):
    """Simple deterministic policy for risk classification.

    This is intentionally conservative: if a query looks like a business
    conclusion, root-cause claim, or automated action, it is escalated.
    """

    LOW_TOKENS = (u'最近', u'昨天', u'本周', u'本月', u'GMV', u'订单', u'渠道', u'区域', u'品类')
    MEDIUM_TOKENS = (u'分析', u'对比', u'比较', u'趋势', u'拆分')
    # Read-only analytical words such as “原因/结论/建议” must not trigger
    # human review by themselves; otherwise diagnostic RAG/multi-turn cases get
    # stuck before evidence retrieval. Escalate only actual execution/change
    # intents here and let governance/permission policy handle sensitive data.
    HIGH_TOKENS = (u'自动执行', u'审批', u'执行操作')
    CRITICAL_TOKENS = (u'删除', u'更新', u'写入', u'导出', u'脱敏', u'手机号', u'身份证', u'工资',
                       u'改成', u'调价', u'暂停', u'刷单', u'刷一下', u'发短信')

    def assess(self, query, plan=None):
        text = u'' if query is None else u'%s' % query
        lower = text.lower()
        plan = plan or {}
        reasons = []
        level = 'low'

        if any(token in lower for token in self.CRITICAL_TOKENS):
            level = 'critical'
            reasons.append('critical_token')
        elif plan.get('task_type') == 'attribution' and plan.get('status') == 'ok':
            # Read-only attribution/root-cause analysis is an analytical task.
            # Do not escalate it solely because the query contains “原因/归因”.
            level = 'medium'
            reasons.append('read_only_attribution')
        elif any(token in text for token in self.HIGH_TOKENS):
            level = 'high'
            reasons.append('high_impact_action')
        elif any(token in text for token in self.MEDIUM_TOKENS):
            level = 'medium'
            reasons.append('analysis_request')
        else:
            level = 'low'
            if any(token in text for token in self.LOW_TOKENS):
                reasons.append('standard_read_only_query')

        if plan.get('status') in ('blocked', 'unsupported'):
            level = 'critical' if plan.get('status') == 'blocked' else 'medium'
            reasons.append('plan_status_%s' % plan.get('status'))

        if plan.get('intent') in ('clarification', 'blocked'):
            reasons.append('intent_%s' % plan.get('intent'))

        requires_human_review = level in ('high', 'critical')
        confidence = 0.9 if level in ('low', 'medium') else 0.8
        return RiskAssessment(level=level, reasons=reasons, requires_human_review=requires_human_review, confidence=confidence)


def get_risk_policy():
    return RiskPolicy()


__all__ = ['RiskPolicy', 'RiskAssessment', 'get_risk_policy']
