# -*- coding: utf-8 -*-
"""Evidence-only policy for graduating observations/hypotheses into claims."""
from __future__ import unicode_literals

from evidence_bus import EvidenceBus

LEVEL_CANDIDATE = 'candidate_observation'
LEVEL_SUPPORTED = 'supported_hypothesis'
LEVEL_QUANTIFIED = 'quantified_driver'
LEVEL_VERIFIED = 'verified_fact'
LEVEL_CONFIRMED_CAUSAL = 'confirmed_causal'
CAUSAL_WORDS = ('导致', '因为', '根因', 'caused', 'cause', 'root cause', 'confirmed causal')
# Final evidence must be fresh unless a caller explicitly supplies a tighter
# business/SLA-specific value.  This applies at serialized release boundaries,
# not to the in-process compatibility audit.
DEFAULT_FINAL_EVIDENCE_TTL_SECONDS = 300


def _as_dict(value):
    return value if isinstance(value, dict) else {}


def _as_list(value):
    if value is None:
        return []
    return list(value) if isinstance(value, (list, tuple)) else [value]


class ClaimGraduationPolicy(object):
    """Reject conclusion upgrades unless the required evidence and controls exist."""
    def evaluate(self, claim, evidence_bus=None, expected_scope=None, ttl_seconds=None, now=None):
        claim = _as_dict(claim)
        requested = claim.get('evidence_level') or claim.get('kind') or LEVEL_CANDIDATE
        evidence_ids = list(claim.get('evidence_ids') or claim.get('support_evidence_ids') or [])
        valid, rejected = (evidence_ids, [])
        if evidence_bus is not None:
            valid, rejected = evidence_bus.validate_scope(evidence_ids, expected_scope=expected_scope,
                                                          ttl_seconds=ttl_seconds, now=now)
        text = str(claim.get('text') or claim.get('statement') or '').lower()
        causal_wording = any(word in text for word in CAUSAL_WORDS)
        if requested in (LEVEL_VERIFIED, 'fact') and not valid:
            return self._blocked('verified_fact_requires_current_execution_evidence', rejected)
        if requested in (LEVEL_QUANTIFIED, 'contribution_candidate_not_causal'):
            if not valid:
                return self._blocked('quantified_driver_requires_current_execution_evidence', rejected)
            if causal_wording:
                return self._blocked('quantified_driver_cannot_use_causal_wording', rejected)
            required = ('baseline', 'metric_definition', 'grain_safe', 'lineage')
            missing = [key for key in required if not claim.get(key)]
            if missing:
                return self._blocked('quantified_driver_missing_methodology:%s' % ','.join(missing), rejected)
        if requested in (LEVEL_CONFIRMED_CAUSAL, 'causal'):
            strategy = claim.get('causal_identification_strategy') or claim.get('identification_strategy')
            if not valid or not strategy:
                return self._blocked('confirmed_causal_requires_identification_strategy_and_current_evidence', rejected)
        if requested in (LEVEL_SUPPORTED, 'hypothesis') and not valid:
            return {'allowed': True, 'level': LEVEL_CANDIDATE, 'evidence_ids': [],
                    'limitations': ['hypothesis_not_graduated_without_current_evidence'], 'rejected': rejected}
        return {'allowed': True, 'level': requested, 'evidence_ids': valid,
                'limitations': [], 'rejected': rejected}

    def validate_action(self, action, evidence_bus=None, expected_scope=None, ttl_seconds=None, now=None):
        action = _as_dict(action)
        required = ('risk_level', 'constraints', 'evidence_ids')
        missing = [key for key in required if not action.get(key)]
        if action.get('approval_required') is not True:
            missing.append('approval_required')
        decision = self.evaluate({'kind': 'fact', 'evidence_ids': action.get('evidence_ids') or []},
                                 evidence_bus=evidence_bus, expected_scope=expected_scope,
                                 ttl_seconds=ttl_seconds, now=now)
        if missing or not decision.get('allowed'):
            return self._blocked('action_requires_risk_constraints_approval_and_current_evidence',
                                 decision.get('rejected') or [])
        return decision

    def validate_outcome(self, outcome, evidence_bus=None, expected_scope=None, ttl_seconds=None, now=None):
        outcome = _as_dict(outcome)
        required = ('baseline', 'observation_window', 'method', 'evidence_ids')
        missing = [key for key in required if not outcome.get(key)]
        decision = self.evaluate({'kind': 'fact', 'evidence_ids': outcome.get('evidence_ids') or []},
                                 evidence_bus=evidence_bus, expected_scope=expected_scope,
                                 ttl_seconds=ttl_seconds, now=now)
        if missing or not decision.get('allowed'):
            return self._blocked('outcome_requires_baseline_window_method_and_current_evidence',
                                 decision.get('rejected') or [])
        return decision

    def _blocked(self, reason, rejected):
        return {'allowed': False, 'level': LEVEL_CANDIDATE, 'evidence_ids': [],
                'limitations': [reason], 'rejected': rejected}


def audit_final_answer_claims(answer_contract, evidence_bus=None, expected_scope=None,
                              ttl_seconds=None, now=None):
    """Demote unsupported final facts; returns a copy and structured findings."""
    answer_contract = dict(_as_dict(answer_contract))
    policy = ClaimGraduationPolicy()
    facts, hypotheses, findings = [], list(answer_contract.get('hypotheses') or []), []
    for fact in answer_contract.get('facts') or []:
        decision = policy.evaluate(fact, evidence_bus=evidence_bus, expected_scope=expected_scope,
                                   ttl_seconds=ttl_seconds, now=now)
        if decision['allowed'] and decision['evidence_ids']:
            copied = dict(fact); copied['evidence_ids'] = decision['evidence_ids']; facts.append(copied)
        else:
            hypotheses.append({'text': fact.get('text', ''),
                               'validation_needed': 'current_verified_execution_evidence'})
            findings.append({'code': 'fact_not_graduated', 'text': fact.get('text', ''),
                             'reason': decision['limitations'], 'rejected': decision['rejected']})
    answer_contract['facts'] = facts
    answer_contract['hypotheses'] = hypotheses
    answer_contract['evidence_ids'] = sorted(set([eid for fact in facts for eid in fact.get('evidence_ids', [])]))
    if findings:
        answer_contract['limitations'] = list(answer_contract.get('limitations') or []) + [
            'unsupported_claims_demoted_to_hypotheses']
        if answer_contract.get('status') == 'ok' and not facts:
            answer_contract['status'] = 'no_answer'
            answer_contract['answer_type'] = 'evidence_limited'
    return answer_contract, findings


def _demote_facts_without_bus(answer_contract):
    """Fail closed when an evidence-producing boundary lost its EvidenceBus."""
    answer_contract = dict(_as_dict(answer_contract))
    facts = list(answer_contract.get('facts') or [])
    hypotheses = list(answer_contract.get('hypotheses') or [])
    findings = []
    for fact in facts:
        fact = _as_dict(fact)
        hypotheses.append({
            'text': fact.get('text', ''),
            'validation_needed': 'serialized_current_execution_evidence',
        })
        findings.append({
            'code': 'evidence_bus_missing', 'text': fact.get('text', ''),
            'reason': ['serialized_evidence_bus_required'], 'rejected': [],
        })
    answer_contract['facts'] = []
    answer_contract['hypotheses'] = hypotheses
    answer_contract['evidence_ids'] = []
    answer_contract['citations'] = []
    if facts:
        answer_contract['limitations'] = list(answer_contract.get('limitations') or []) + [
            'serialized_evidence_bus_required']
        if answer_contract.get('status') == 'ok':
            answer_contract['status'] = 'no_answer'
            answer_contract['answer_type'] = 'evidence_limited'
    return answer_contract, findings


def audit_answer_contract_with_provenance(answer_contract, provenance=None, scope=None,
                                          ttl_seconds=None, now=None,
                                          require_evidence_bus=False):
    """Audit a final answer using the producer's serialized EvidenceBus.

    ``require_evidence_bus`` is for evidence-producing final-output boundaries.
    In that mode an absent or malformed serialized bus cannot be treated as an
    unaudited success: facts are demoted and an evidence-limited terminal answer
    is returned.  Legacy non-producing callers retain the previous three-tuple
    behavior when the flag is false.
    """
    provenance = _as_dict(provenance)
    bus_data = _as_dict(provenance.get('evidence_bus'))
    records = bus_data.get('records') if bus_data else None
    if not isinstance(records, list):
        if require_evidence_bus:
            return_value, findings = _demote_facts_without_bus(answer_contract)
            return return_value, findings, True
        return dict(_as_dict(answer_contract)), [], False
    return_value, findings = audit_final_answer_claims(
        answer_contract, evidence_bus=EvidenceBus(records), expected_scope=scope,
        ttl_seconds=ttl_seconds, now=now)
    return return_value, findings, True


__all__ = ['ClaimGraduationPolicy', 'audit_final_answer_claims',
           'audit_answer_contract_with_provenance', 'DEFAULT_FINAL_EVIDENCE_TTL_SECONDS',
           'LEVEL_CANDIDATE', 'LEVEL_SUPPORTED', 'LEVEL_QUANTIFIED', 'LEVEL_VERIFIED',
           'LEVEL_CONFIRMED_CAUSAL']
