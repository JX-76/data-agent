# -*- coding: utf-8 -*-
"""Case-scoped evidence freshness policy and re-execution decisions.

This module deliberately reuses EvidenceBus scope validation.  It does not turn
historical worker output into evidence: it only decides whether a case may use
linked verified execution records or must rerun a verification task.
"""
from __future__ import unicode_literals

from claim_graduation import DEFAULT_FINAL_EVIDENCE_TTL_SECONDS
from gmv_health_playbook import gmv_health_expected_scope

FRESHNESS_CONTRACT = 'case_evidence_freshness_v2'
REEXECUTION_PLAN_CONTRACT = 'evidence_reexecution_plan_v1'


def _as_dict(value):
    if isinstance(value, dict):
        return value
    if hasattr(value, 'to_dict'):
        try:
            return value.to_dict()
        except Exception:
            return {}
    return {}


def _positive_seconds(value, fallback):
    try:
        value = float(value)
    except Exception:
        return float(fallback)
    # A non-positive TTL is an unsafe configuration: fail closed with the
    # configured default rather than silently allowing unlimited reuse.
    return value if value > 0 else float(fallback)


def resolve_evidence_ttl(case_obj, metric=None, default_ttl_seconds=None):
    """Resolve the TTL from a case mission, optionally overridden per metric.

    Precedence is metric policy, case policy, then the final-answer default.
    Invalid/non-positive custom values do not create an unlimited cache window.
    """
    case = getattr(case_obj, 'case', case_obj)
    mission = _as_dict(getattr(case, 'mission', None))
    if not mission and hasattr(getattr(case, 'mission', None), 'to_dict'):
        mission = case.mission.to_dict()
    policy = _as_dict(mission.get('policy'))
    scope = _as_dict(getattr(case, 'business_scope', None))
    metric = metric or scope.get('metric')
    metric_ttls = _as_dict(policy.get('evidence_ttl_by_metric'))
    value = metric_ttls.get(metric)
    if value is None:
        value = policy.get('evidence_ttl_seconds')
    fallback = DEFAULT_FINAL_EVIDENCE_TTL_SECONDS if default_ttl_seconds is None else default_ttl_seconds
    if value is None:
        value = fallback
    return _positive_seconds(value, fallback)


def _reason_from_rejections(rejected, has_valid):
    errors = [item.get('error') for item in rejected if isinstance(item, dict)]
    if 'evidence_ttl_expired' in errors:
        return 'evidence_ttl_expired'
    if 'evidence_scope_mismatch' in errors:
        return 'evidence_scope_mismatch'
    if 'evidence_not_found' in errors:
        return 'linked_evidence_not_found'
    if errors:
        return 'linked_evidence_rejected'
    return 'current_verified_evidence_missing' if not has_valid else 'current_verified_evidence_rejected'


def build_reexecution_plan(freshness, task_type='verify_gmv_signal'):
    """Create a serializable, fail-closed rerun instruction for a planner."""
    freshness = _as_dict(freshness)
    return {
        'contract': REEXECUTION_PLAN_CONTRACT,
        'required': bool(freshness.get('needs_reexecution')),
        'task_type': task_type,
        'reason': freshness.get('reexecution_reason'),
        'expected_scope': dict(_as_dict(freshness.get('expected_scope'))),
        'invalid_evidence_ids': [item.get('evidence_id') for item in freshness.get('rejected') or []
                                 if isinstance(item, dict) and item.get('evidence_id')],
        'ttl_seconds': freshness.get('ttl_seconds'),
    }


def assess_case_evidence_freshness(board, expected_scope=None, ttl_seconds=None, now=None):
    """Return a replayable decision for the case's linked execution evidence."""
    case = board.case
    expected_scope = _as_dict(expected_scope) or gmv_health_expected_scope(case)
    ttl_seconds = resolve_evidence_ttl(case) if ttl_seconds is None else _positive_seconds(
        ttl_seconds, resolve_evidence_ttl(case))
    evidence_ids = list(board.evidence_bus.case_links.get(case.case_id, []))
    valid, rejected = board.validate_evidence_ids(
        evidence_ids, expected_scope=expected_scope, ttl_seconds=ttl_seconds, now=now)
    needs_reexecution = bool(rejected) or not bool(valid)
    result = {
        'contract': FRESHNESS_CONTRACT,
        'case_id': case.case_id,
        'metric': expected_scope.get('metric'),
        'ttl_seconds': ttl_seconds,
        'checked_at': now,
        'expected_scope': dict(expected_scope),
        'evidence_ids': evidence_ids,
        'current_evidence_ids': valid,
        'rejected': rejected,
        'needs_reexecution': needs_reexecution,
        'reexecution_reason': _reason_from_rejections(rejected, bool(valid)),
    }
    result['reexecution_plan'] = build_reexecution_plan(result)
    return result


__all__ = ['FRESHNESS_CONTRACT', 'REEXECUTION_PLAN_CONTRACT',
           'resolve_evidence_ttl', 'assess_case_evidence_freshness',
           'build_reexecution_plan']
