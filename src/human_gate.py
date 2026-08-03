# -*- coding: utf-8 -*-
"""Human-in-the-loop gate for high-risk Data Agent actions.

Python 2.7 compatible and deterministic.
"""

from __future__ import unicode_literals

from risk_policy import get_risk_policy


class HumanGateResult(object):
    def __init__(self, status='skipped', requires_human_review=False, approval_status='skipped', risk_level='low', review_checklist=None, reason=None):
        self.status = status
        self.requires_human_review = requires_human_review
        self.approval_status = approval_status
        self.risk_level = risk_level
        self.review_checklist = review_checklist or []
        self.reason = reason

    def to_dict(self):
        return {
            'status': self.status,
            'requires_human_review': self.requires_human_review,
            'approval_status': self.approval_status,
            'risk_level': self.risk_level,
            'review_checklist': list(self.review_checklist),
            'reason': self.reason,
        }


class HumanGatePolicy(object):
    """Decides whether the current task needs human approval before execution."""

    def __init__(self, risk_policy=None):
        self.risk_policy = risk_policy or get_risk_policy()

    def evaluate(self, query, plan=None, risk_assessment=None):
        plan = plan or {}
        risk_assessment = risk_assessment or self.risk_policy.assess(query, plan)
        level = risk_assessment.level
        # A terminal deny/unsupported result must never be transformed into a
        # review queue.  Review is only meaningful before a potentially allowed
        # high-risk action, and its ordering must remain stable across paths.
        if plan.get('status') in ('blocked', 'unsupported') or plan.get('intent') == 'blocked':
            return HumanGateResult(
                status='not_applicable', requires_human_review=False,
                approval_status='not_applicable', risk_level=level,
                review_checklist=[], reason='terminal_plan_%s' % plan.get('status', 'blocked'))
        if level in ('high', 'critical'):
            checklist = [
                '确认查询是否只读',
                '确认是否允许自动生成结论',
                '确认是否涉及敏感字段或写操作',
                '确认是否允许继续执行',
            ]
            return HumanGateResult(
                status='pending_human_review',
                requires_human_review=True,
                approval_status='pending',
                risk_level=level,
                review_checklist=checklist,
                reason='high_risk_%s' % level,
            )
        return HumanGateResult(
            status='approved',
            requires_human_review=False,
            approval_status='approved',
            risk_level=level,
            review_checklist=[],
            reason='auto_approved',
        )


__all__ = ['HumanGatePolicy', 'HumanGateResult']
