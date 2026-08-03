# -*- coding: utf-8 -*-
"""Deterministic eight-dimension answer-quality scoring.

Scores intentionally measure observable contract/evidence properties only. They
never infer factual correctness from fluent prose.
"""
from __future__ import unicode_literals


def _dict(value):
    return value if isinstance(value, dict) else {}


def score_answer_quality(result, envelope=None):
    result = _dict(result)
    if envelope is None:
        try:
            from answer_contracts import build_answer_envelope
            envelope = build_answer_envelope(result, query=result.get('query'))
        except Exception:
            envelope = {}
    envelope = _dict(envelope)
    claims = envelope.get('claims') or []
    evidence = envelope.get('evidence_refs') or []
    audit = _dict(envelope.get('claim_audit') or result.get('claim_audit'))
    status = envelope.get('status') or result.get('status')
    governance = _dict(envelope.get('governance'))
    findings = envelope.get('hallucination_findings') or []
    numeric_claims = [x for x in claims if isinstance(x, dict) and x.get('numeric')]
    linked_numeric = [x for x in numeric_claims if x.get('evidence_ids')]
    limits = envelope.get('limitations') or []
    structured = _dict(envelope.get('structured_answer'))
    dimensions = {
        'factual_grounding': 100 if not numeric_claims else int(100 * len(linked_numeric) / float(len(numeric_claims))),
        'source_traceability': 100 if evidence else 0,
        'scope_consistency': 100 if result.get('metric') or status in ('blocked', 'need_clarification', 'unsupported') else 40,
        'uncertainty_calibration': 100 if limits or status in ('blocked', 'need_clarification', 'unsupported', 'degraded') else 50,
        'reasoning_integrity': 100 if audit.get('status') != 'blocked' else 0,
        'tool_usage_effectiveness': 100 if evidence or status != 'ok' else 0,
        'response_completeness': 100 if envelope.get('user_answer') and structured else 0,
        'governance_compliance': 100 if status not in ('pending_human_review',) or governance else 70,
    }
    weights = {'factual_grounding': 0.20, 'source_traceability': 0.15,
               'scope_consistency': 0.10, 'uncertainty_calibration': 0.10,
               'reasoning_integrity': 0.15, 'tool_usage_effectiveness': 0.10,
               'response_completeness': 0.10, 'governance_compliance': 0.10}
    score = int(round(sum(dimensions[key] * weights[key] for key in weights)))
    if audit.get('status') == 'blocked' or findings:
        score = min(score, 49)
    return {'contract': 'quality_score_v2', 'score': score, 'dimensions': dimensions,
            'weights': weights, 'numeric_claim_count': len(numeric_claims),
            'linked_numeric_claim_count': len(linked_numeric),
            'hallucination_blocked': audit.get('status') == 'blocked',
            'status': status}


__all__ = ['score_answer_quality']
