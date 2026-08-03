# -*- coding: utf-8 -*-
"""Observable quality evaluation for ecommerce diagnostic-agent cases.

The evaluator deliberately separates the existing terminal-status gate from an
answer-quality projection.  It is deterministic, dependency-free and Python
2.7 compatible: it never asks an LLM to invent a score or evidence.
"""
from __future__ import unicode_literals

import json


MAX_TEXT = 4000
MAX_ITEMS = 30


def _text(value):
    if value is None:
        return u''
    if isinstance(value, dict):
        return u' '.join([_text(k) + u' ' + _text(v) for k, v in value.items()])
    if isinstance(value, (list, tuple)):
        return u' '.join([_text(x) for x in value])
    try:
        return value.decode('utf-8', 'ignore') if isinstance(value, bytes) else unicode(value)
    except NameError:
        return str(value)
    except Exception:
        return u''


def _lower(value):
    return _text(value).lower()


def _trace_text(trace):
    names = []
    for event in trace or []:
        if isinstance(event, dict):
            name = event.get('name') or event.get('stage')
            if name:
                names.append(_text(name))
    return u' '.join(names).lower()


def _bounded(value, limit=MAX_ITEMS):
    if isinstance(value, list):
        return value[:limit]
    return value


def _compact(value, limit=MAX_TEXT):
    text = _text(value)
    if len(text) > limit:
        return text[:limit] + u'…[truncated]'
    return text


def _nonempty(value):
    return bool(_text(value).strip())


def _json_safe(value):
    """Convert report artifacts into bounded JSON primitives without raw payloads."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, dict):
        return dict((_text(key), _json_safe(item)) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value[:MAX_ITEMS]]
    try:
        unicode_type = unicode
    except NameError:
        unicode_type = str
    if isinstance(value, (bytes, unicode_type)):
        return _compact(value)
    return _compact(value)


def _answer_text(result):
    """Return the user-visible answer using the stable response fields first."""
    result = result or {}
    for key in ('user_answer', 'answer', 'report', 'response', 'message'):
        if _nonempty(result.get(key)):
            return _compact(result.get(key))
    insight = result.get('insight') or {}
    if isinstance(insight, dict):
        for key in ('summary', 'headline', 'answer', 'text'):
            if _nonempty(insight.get(key)):
                return _compact(insight.get(key))
    return u''


def _evidence_refs(result):
    result = result or {}
    refs = []
    for key in ('provenance', 'evidence', 'citations'):
        value = result.get(key)
        if value:
            refs.extend(value if isinstance(value, list) else [value])
    ledger = result.get('fact_ledger') or {}
    if isinstance(ledger, dict):
        refs.extend(ledger.get('evidence_refs') or [])
    if result.get('results') or result.get('results_summary'):
        refs.append({'source': 'execution_result'})
    return _bounded(refs)


def _claims(result, answer):
    """Preserve explicit claims; otherwise record a conservative observable claim."""
    result = result or {}
    claims = result.get('claims') or []
    if isinstance(claims, dict):
        claims = [claims]
    normalized = []
    for claim in claims:
        if isinstance(claim, dict):
            item = dict(claim)
            item['text'] = _compact(item.get('text') or item.get('claim'))
            item['evidence_refs'] = _bounded(item.get('evidence_refs') or [])
            normalized.append(item)
    claim_audit = result.get('claim_audit') or {}
    for text in claim_audit.get('unsupported_claims') or []:
        normalized.append({'text': _compact(text), 'claim_type': 'unsupported_claim',
                           'evidence_refs': [], 'support': 'unsupported'})
    # Do not extract numbers from arbitrary prose in V1: this avoids false
    # positives. Mark prose-only answers as non-claim-structured instead.
    return _bounded(normalized)


def build_answer_envelope(result, trace=None):
    """Build a bounded, serialisable Answer/Claim/Evidence observation contract."""
    result = result or {}
    insight = result.get('insight') or {}
    answer = _answer_text(result)
    facts = result.get('facts') or []
    hypotheses = result.get('hypotheses') or []
    actions = result.get('actions') or []
    limitations = result.get('limitations') or []
    if isinstance(insight, dict):
        limitations = limitations or insight.get('caveats') or []
        actions = actions or insight.get('next_steps') or []
    evidence = _evidence_refs(result)
    trace_names = []
    for event in trace or []:
        if isinstance(event, dict):
            name = event.get('name') or event.get('stage')
            if name:
                trace_names.append(name)
    return _json_safe({
        'contract': 'answer_observability_v1',
        'answer_present': bool(answer),
        'user_answer': answer,
        'structured_answer': {
            'conclusion': _compact((insight or {}).get('headline') or (insight or {}).get('summary')) if isinstance(insight, dict) else u'',
            'facts': _bounded(facts),
            'hypotheses': _bounded(hypotheses),
            'actions': _bounded(actions),
            'limitations': _bounded(limitations),
        },
        'claims': _claims(result, answer),
        'evidence_refs': evidence,
        'memory_refs': _bounded(((result.get('plan') or {}).get('memory_refs') or [])),
        'trace_events': _bounded(trace_names),
        'source_fields': [key for key in ('user_answer', 'answer', 'report', 'response', 'message', 'insight') if result.get(key)],
    })


class DeterministicQualityEvaluator(object):
    """V1 quality and hallucination audit based only on observable artifacts."""

    FACT_STATUSES = ('ok', 'degraded')

    def evaluate(self, case, result, trace=None):
        case = case or {}
        result = result or {}
        expected = case.get('expected') or {}
        envelope = build_answer_envelope(result, trace)
        status = result.get('status')
        findings = []
        score = 0
        observable = True

        if status in self.FACT_STATUSES and not envelope.get('answer_present'):
            findings.append({'type': 'missing_user_answer', 'severity': 'high',
                             'reason': '可执行终态没有保留用户可见回答'})
        if expected.get('requires_evidence') and status == 'ok' and not envelope.get('evidence_refs'):
            findings.append({'type': 'missing_evidence', 'severity': 'high',
                             'reason': '需要证据的成功回答没有证据引用'})

        unsupported = [c for c in envelope.get('claims') or []
                       if c.get('support') == 'unsupported' or c.get('claim_type') == 'unsupported_claim']
        for claim in unsupported:
            findings.append({'type': 'unsupported_claim', 'severity': 'critical',
                             'claim': claim.get('text'), 'reason': 'claim_audit 标记为无证据声明'})
        for claim in envelope.get('claims') or []:
            if claim.get('claim_type') in ('fact', 'numeric_fact', 'comparative_fact') and not claim.get('evidence_refs'):
                findings.append({'type': 'untraceable_fact_claim', 'severity': 'critical',
                                 'claim': claim.get('text'), 'reason': '事实声明未关联 evidence_refs'})

        # Scores are intentionally evidence-bound. A prose-only legacy answer
        # receives observability feedback rather than fabricated semantic credit.
        if status in ('need_clarification', 'unsupported', 'blocked', 'pending_human_review'):
            score += 20 if envelope.get('answer_present') else 0
        elif envelope.get('answer_present'):
            score += 15
        if envelope.get('evidence_refs'):
            score += 25
        if envelope.get('structured_answer', {}).get('actions'):
            score += 10
        if envelope.get('structured_answer', {}).get('limitations'):
            score += 10
        if envelope.get('claims'):
            score += 20
        if status in ('blocked', 'pending_human_review') and result.get('blocked_reason'):
            score += 10
        if findings:
            score = min(score, 49 if any(x.get('severity') == 'critical' for x in findings) else 69)
        score = min(100, score)

        if not envelope.get('answer_present') and not envelope.get('evidence_refs'):
            observable = False
        risk = 'none'
        if any(x.get('severity') == 'critical' for x in findings):
            risk = 'critical'
        elif any(x.get('severity') == 'high' for x in findings):
            risk = 'high'
        elif findings:
            risk = 'medium'
        grade = 'unscorable' if not observable else ('A' if score >= 85 else 'B' if score >= 70 else 'C' if score >= 50 else 'D')
        return {
            'contract': 'answer_quality_v1',
            'scorable': observable,
            'score': score if observable else None,
            'grade': grade,
            'dimensions': {
                'answer_presence': 15 if envelope.get('answer_present') else 0,
                'evidence_observability': 25 if envelope.get('evidence_refs') else 0,
                'structured_actions': 10 if envelope.get('structured_answer', {}).get('actions') else 0,
                'limitations': 10 if envelope.get('structured_answer', {}).get('limitations') else 0,
                'claim_traceability': 20 if envelope.get('claims') else 0,
            },
            'reasons': [x.get('reason') for x in findings],
            'answer_envelope': envelope,
            'hallucination': {
                'risk': risk,
                'findings': findings,
                'claims_checked': len(envelope.get('claims') or []),
                'numeric_traceability_rate': None,
            },
        }


class EcommerceDiagnosisEvaluator(object):
    """Evaluate one structured clinical case against an AgentFacade response."""

    def __init__(self, quality_evaluator=None):
        self.quality_evaluator = quality_evaluator or DeterministicQualityEvaluator()

    def evaluate(self, case, result, trace=None):
        case = case or {}
        result = result or {}
        expected = case.get('expected') or {}
        text = _lower(result)
        trace_text = _trace_text(trace)
        errors = []
        domains = []

        statuses = expected.get('allowed_statuses')
        if statuses is None and expected.get('status') is not None:
            statuses = [expected.get('status')]
        if statuses and result.get('status') not in statuses:
            errors.append('status expected one of=%s got=%s' % (statuses, result.get('status')))
            domains.append('terminal_status')
        for signal in expected.get('must_contain') or []:
            if _lower(signal) not in text:
                errors.append('missing_signal:%s' % signal)
                domains.append((case.get('failure_map') or {}).get('missing_signal', 'evidence_grounding'))
        for signal in expected.get('must_not_contain') or []:
            if _lower(signal) in text:
                errors.append('forbidden_signal:%s' % signal)
                domains.append((case.get('failure_map') or {}).get('forbidden_signal', 'governance'))
        for signal in expected.get('trace_contains') or []:
            if _lower(signal) not in trace_text:
                errors.append('missing_trace_signal:%s' % signal)
                domains.append((case.get('failure_map') or {}).get('missing_trace_signal', 'tool_selection'))
        if expected.get('requires_evidence') and result.get('status') == 'ok':
            if not _evidence_refs(result):
                errors.append('missing_evidence_footprint')
                domains.append('hallucination_guard')
        quality = self.quality_evaluator.evaluate(case, result, trace)
        domains = sorted(set([x for x in domains if x]))
        return {'id': case.get('id'), 'category': case.get('category'), 'passed': not errors,
                'errors': errors, 'architecture_domains': domains,
                'result_status': result.get('status'), 'expected': expected,
                'quality': quality, 'hallucination': quality.get('hallucination')}

    def summarize(self, evaluated):
        evaluated = evaluated or []
        domains, categories = {}, {}
        passed = 0
        scorable, score_total, critical, high = 0, 0, 0, 0
        for item in evaluated:
            category = item.get('category') or 'uncategorized'
            bucket = categories.setdefault(category, {'total': 0, 'passed': 0})
            bucket['total'] += 1
            if item.get('passed'):
                passed += 1
                bucket['passed'] += 1
            for domain in item.get('architecture_domains') or []:
                domains[domain] = domains.get(domain, 0) + 1
            quality = item.get('quality') or {}
            if quality.get('scorable') and quality.get('score') is not None:
                scorable += 1
                score_total += quality.get('score')
            risk = (quality.get('hallucination') or {}).get('risk')
            if risk == 'critical': critical += 1
            elif risk == 'high': high += 1
        for bucket in categories.values():
            bucket['pass_rate'] = round(bucket['passed'] * 1.0 / max(1, bucket['total']), 4)
        total = len(evaluated)
        return {'total': total, 'passed': passed, 'failed': total - passed,
                'pass_rate': round(passed * 1.0 / max(1, total), 4),
                'category_breakdown': categories, 'architecture_hotspots': domains,
                'quality': {'contract': 'quality_summary_v1', 'scorable_count': scorable,
                            'unscorable_count': total - scorable,
                            'average_score': round(score_total * 1.0 / max(1, scorable), 2) if scorable else None,
                            'critical_hallucination_count': critical,
                            'high_hallucination_risk_count': high}}


def flatten_result_for_report(result, trace=None):
    """Bounded observability projection; retains answer/evidence without raw payloads."""
    result = result or {}
    envelope = build_answer_envelope(result, trace)
    return {'status': result.get('status'), 'intent': result.get('intent'),
            'task_type': result.get('task_type'), 'metric': result.get('metric'),
            'trace_id': result.get('trace_id'), 'errors': result.get('errors') or [],
            'answer_observability': envelope,
            'claim_audit': _json_safe(_bounded(result.get('claim_audit') or {})),
            'fact_ledger': _json_safe(_bounded(result.get('fact_ledger') or {}))}


__all__ = ['EcommerceDiagnosisEvaluator', 'DeterministicQualityEvaluator',
           'build_answer_envelope', 'flatten_result_for_report']
