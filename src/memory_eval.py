# -*- coding: utf-8 -*-
"""Evaluation helpers for Phase D memory/context reliability."""
from __future__ import unicode_literals


class MemoryQualityEvaluator(object):
    def __init__(self, service):
        self.service = service

    def evaluate(self, cases):
        cases = list(cases or [])
        route_ok = 0
        inherit_ok = 0
        isolation_ok = 0
        budget_ok = 0
        for case in cases:
            previous = case.get('previous_context') or {}
            self._seed(case)
            result = self.service.build_context(
                case.get('user_id', 'u1'), case.get('session_id', 's1'), case.get('query', ''),
                previous_context=previous,
                system_prompt=case.get('system_prompt', 'system'),
                recent_messages=case.get('recent_messages') or [],
                current_plan=case.get('current_plan') or {},
                access_context=case.get('access_context') or {'user_id': case.get('user_id', 'u1'), 'tenant_id': case.get('tenant_id', 'global')},
            )
            route = result.get('route') or {}
            if route.get('route') == case.get('expected_route'):
                route_ok += 1
            expected_fields = set(case.get('expected_inherited_fields') or [])
            if expected_fields.issubset(set(route.get('inherited_fields') or [])):
                inherit_ok += 1
            forbidden = case.get('forbidden_text')
            if not forbidden or forbidden not in result.get('content', ''):
                isolation_ok += 1
            if result.get('tokens_used', 0) <= result.get('token_budget', 0):
                budget_ok += 1
        total = float(len(cases) or 1)
        return {
            'case_count': len(cases),
            'route_accuracy': route_ok / total,
            'inheritance_accuracy': inherit_ok / total,
            'isolation_accuracy': isolation_ok / total,
            'budget_pass_rate': budget_ok / total,
        }

    def pass_thresholds(self, metrics, thresholds=None):
        thresholds = thresholds or {
            'route_accuracy': 0.90,
            'inheritance_accuracy': 0.85,
            'isolation_accuracy': 1.00,
            'budget_pass_rate': 1.00,
        }
        failures = []
        for key, threshold in thresholds.items():
            if float(metrics.get(key, 0.0)) < float(threshold):
                failures.append({'metric': key, 'actual': metrics.get(key, 0.0), 'threshold': threshold})
        return failures

    def _seed(self, case):
        for pref in case.get('preferences') or []:
            self.service.remember_preference(
                pref.get('user_id') or case.get('user_id', 'u1'),
                pref.get('session_id') or case.get('session_id', 's1'),
                pref.get('key'), pref.get('value'), tenant_id=pref.get('tenant_id', 'global'),
                topic=pref.get('topic', 'preference'))


__all__ = ['MemoryQualityEvaluator']
