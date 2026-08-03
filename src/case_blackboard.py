# -*- coding: utf-8 -*-
"""Case Blackboard built on top of verified EvidenceBus records.

The blackboard is the shared case workspace for multi-agent ecommerce diagnosis.
It stores typed case artifacts, hypotheses, action cards and outcomes, while
keeping factual support delegated to EvidenceBus verified execution records.
"""
from __future__ import unicode_literals

from case_contracts import (
    BusinessCase, CaseArtifact, Hypothesis, ActionCard, OutcomeMeasurement,
    CaseEvent, ARTIFACT_SIGNAL, ARTIFACT_CONTRIBUTION,
    EVENT_CASE_CREATED, EVENT_EVIDENCE_VERIFIED, EVENT_SIGNAL_DETECTED,
    EVENT_HYPOTHESIS_PROPOSED, EVENT_ACTION_DRAFTED, EVENT_OUTCOME_MEASURED,
)
from case_state_machine import CaseStateMachine
from evidence_bus import EvidenceBus
from claim_graduation import ClaimGraduationPolicy


def _as_dict(value):
    if isinstance(value, dict):
        return value
    if hasattr(value, 'to_dict'):
        try:
            return value.to_dict()
        except Exception:
            return {}
    return {}


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


class CaseBlackboard(object):
    def __init__(self, case_obj=None, evidence_bus=None, state_machine=None):
        self.case = case_obj if isinstance(case_obj, BusinessCase) else BusinessCase.from_dict(case_obj or {})
        self.evidence_bus = evidence_bus if evidence_bus is not None else EvidenceBus()
        # The view is the case-local authority for what may be consumed.  The
        # underlying bus remains append-only and can safely be shared by cases.
        self.evidence_view = self.evidence_bus.case_view(self.case.case_id)
        self.claim_policy = ClaimGraduationPolicy()
        self.state_machine = state_machine or CaseStateMachine()
        self.artifacts = {}
        self.hypotheses = {}
        self.actions = {}
        self.outcomes = {}
        self.events = []
        if not self.case.timeline:
            self.append_event(CaseEvent(self.case.case_id, EVENT_CASE_CREATED, source='case_blackboard'), apply_state=True)

    def append_event(self, event, apply_state=False):
        event = event if isinstance(event, CaseEvent) else CaseEvent.from_dict(event)
        self.events.append(event.to_dict())
        if apply_state:
            self.state_machine.apply(self.case, event)
        return event.to_dict()

    def record_execution_envelope(self, envelope, producer_task_id=None, trace_id=None, graph_type=None,
                                  expected_scope=None, ttl_seconds=None, now=None):
        record = self.evidence_bus.record_envelope(envelope, producer_task_id=producer_task_id,
                                                   trace_id=trace_id, graph_type=graph_type)
        if record:
            valid, rejected = self.evidence_view.link(
                [record.get('evidence_id')], expected_scope=expected_scope,
                ttl_seconds=ttl_seconds, now=now)
            if rejected:
                self._record_evidence_rejections(rejected, expected_scope=expected_scope, now=now,
                                                 source=producer_task_id)
                return None
            if not valid:
                return None
            self.append_event(CaseEvent(self.case.case_id, EVENT_EVIDENCE_VERIFIED,
                                        payload={'evidence_id': record.get('evidence_id')},
                                        evidence_ids=[record.get('evidence_id')], source=producer_task_id),
                              apply_state=False)
        return record

    def validate_evidence_ids(self, evidence_ids, expected_scope=None, ttl_seconds=None, now=None):
        # Artifacts/actions may consume only evidence explicitly accepted by this
        # case; a global record must first be linked through record_execution_envelope.
        valid, rejected = self.evidence_bus.validate_scope(evidence_ids, expected_scope=expected_scope,
                                                            ttl_seconds=ttl_seconds, now=now)
        accepted = set(self.evidence_view.accepted_ids)
        for evidence_id in list(valid):
            if evidence_id not in accepted:
                valid.remove(evidence_id)
                rejected.append({'evidence_id': evidence_id, 'error': 'evidence_not_linked_to_case'})
        return valid, rejected

    def prune_invalid_evidence(self, expected_scope=None, ttl_seconds=None, now=None):
        # Preserve the legacy diagnostic behaviour of inspecting all known
        # records, but never remove, link, or otherwise mutate shared evidence.
        # Invalid records become case-local rejection/audit entries only.  Any
        # accepted records already linked to this case are then refreshed so a
        # TTL/scope drift hides them from this case view.
        evidence_ids = list(self.evidence_bus.records.keys())
        valid, rejected = self.evidence_bus.validate_scope(
            evidence_ids, expected_scope=expected_scope,
            ttl_seconds=ttl_seconds, now=now)
        self.evidence_view.refresh(expected_scope=expected_scope,
                                   ttl_seconds=ttl_seconds, now=now)
        self._record_evidence_rejections(rejected, expected_scope=expected_scope, now=now,
                                         source='case_blackboard.prune_invalid_evidence')
        return rejected

    def _record_evidence_rejections(self, rejected, expected_scope=None, now=None, source=None):
        if not rejected:
            return []
        before = len(self.evidence_view.rejections)
        self.evidence_view._record_rejections(rejected, expected_scope or {}, now)
        new_audits = self.evidence_view.rejections[before:]
        # Some callers validate via CaseEvidenceView.link()/refresh() first; in
        # that path the audit already exists and this method is responsible only
        # for projecting it into the case timeline.
        if not new_audits:
            rejected_ids = set([item.get('evidence_id') for item in rejected])
            new_audits = [audit for audit in self.evidence_view.rejections
                          if audit.get('evidence_id') in rejected_ids]
        for audit in new_audits:
            self.append_event(CaseEvent(
                self.case.case_id, 'evidence.rejected', payload=audit,
                evidence_ids=[audit.get('evidence_id')] if audit.get('evidence_id') else [],
                source=source), apply_state=False)
        return new_audits

    def _case_evidence_records(self):
        return self.evidence_view.records()

    def add_artifact(self, artifact, expected_scope=None, require_verified=True, ttl_seconds=None, now=None):
        artifact = artifact if isinstance(artifact, CaseArtifact) else CaseArtifact.from_dict(artifact)
        if artifact.case_id != self.case.case_id:
            return {'ok': False, 'error': 'case_id_mismatch'}
        if require_verified and artifact.evidence_ids:
            valid, rejected = self.validate_evidence_ids(artifact.evidence_ids, expected_scope=expected_scope,
                                                         ttl_seconds=ttl_seconds, now=now)
            if rejected:
                return {'ok': False, 'error': 'invalid_evidence_refs', 'rejected': rejected}
            artifact.evidence_ids = valid
        if require_verified and not artifact.evidence_ids:
            return {'ok': False, 'error': 'missing_verified_evidence'}
        self.artifacts[artifact.artifact_id] = artifact.to_dict()
        event_type = EVENT_SIGNAL_DETECTED if artifact.artifact_type == ARTIFACT_SIGNAL else EVENT_EVIDENCE_VERIFIED
        self.append_event(CaseEvent(self.case.case_id, event_type, artifact_ids=[artifact.artifact_id],
                                    evidence_ids=artifact.evidence_ids, source=artifact.produced_by),
                          apply_state=True)
        return {'ok': True, 'artifact': artifact.to_dict(), 'case_status': self.case.status}

    def propose_hypothesis(self, hypothesis, expected_scope=None, ttl_seconds=None, now=None):
        hypothesis = hypothesis if isinstance(hypothesis, Hypothesis) else Hypothesis.from_dict(hypothesis)
        if hypothesis.case_id != self.case.case_id:
            return {'ok': False, 'error': 'case_id_mismatch'}
        if hypothesis.support_evidence_ids:
            valid, rejected = self.validate_evidence_ids(
                hypothesis.support_evidence_ids, expected_scope=expected_scope,
                ttl_seconds=ttl_seconds, now=now)
            if rejected:
                return {'ok': False, 'error': 'invalid_evidence_refs', 'rejected': rejected}
            hypothesis.support_evidence_ids = valid
        decision = self.claim_policy.evaluate({
            'kind': 'hypothesis', 'statement': hypothesis.statement,
            'support_evidence_ids': hypothesis.support_evidence_ids,
        }, evidence_bus=self.evidence_bus, expected_scope=expected_scope,
           ttl_seconds=ttl_seconds, now=now)
        if decision.get('rejected'):
            return {'ok': False, 'error': 'invalid_evidence_refs', 'rejected': decision['rejected']}
        hypothesis.support_evidence_ids = decision.get('evidence_ids') or []
        if decision.get('limitations'):
            hypothesis.metadata['graduation_limitations'] = list(decision['limitations'])
        self.hypotheses[hypothesis.hypothesis_id] = hypothesis.to_dict()
        self.append_event(CaseEvent(self.case.case_id, EVENT_HYPOTHESIS_PROPOSED,
                                    payload={'hypothesis_id': hypothesis.hypothesis_id},
                                    evidence_ids=hypothesis.support_evidence_ids), apply_state=True)
        return {'ok': True, 'hypothesis': hypothesis.to_dict(), 'case_status': self.case.status}

    def draft_action(self, action, expected_scope=None, ttl_seconds=None, now=None):
        action = action if isinstance(action, ActionCard) else ActionCard.from_dict(action)
        if action.case_id != self.case.case_id:
            return {'ok': False, 'error': 'case_id_mismatch'}
        decision = self.claim_policy.validate_action(action.to_dict(), evidence_bus=self.evidence_bus,
                                                     expected_scope=expected_scope,
                                                     ttl_seconds=ttl_seconds, now=now)
        if not decision.get('allowed'):
            return {'ok': False, 'error': 'action_not_graduated',
                    'limitations': decision.get('limitations') or [],
                    'rejected': decision.get('rejected') or []}
        valid, rejected = self.validate_evidence_ids(action.evidence_ids, expected_scope=expected_scope,
                                                     ttl_seconds=ttl_seconds, now=now)
        if rejected:
            return {'ok': False, 'error': 'invalid_evidence_refs', 'rejected': rejected}
        action.evidence_ids = valid
        self.actions[action.action_id] = action.to_dict()
        self.append_event(CaseEvent(self.case.case_id, EVENT_ACTION_DRAFTED,
                                    payload={'action_id': action.action_id}, evidence_ids=action.evidence_ids),
                          apply_state=True)
        return {'ok': True, 'action': action.to_dict(), 'case_status': self.case.status}

    def add_outcome(self, outcome, expected_scope=None, ttl_seconds=None, now=None):
        outcome = outcome if isinstance(outcome, OutcomeMeasurement) else OutcomeMeasurement.from_dict(outcome)
        if outcome.case_id != self.case.case_id:
            return {'ok': False, 'error': 'case_id_mismatch'}
        decision = self.claim_policy.validate_outcome(outcome.to_dict(), evidence_bus=self.evidence_bus,
                                                      expected_scope=expected_scope,
                                                      ttl_seconds=ttl_seconds, now=now)
        if not decision.get('allowed'):
            return {'ok': False, 'error': 'outcome_not_graduated',
                    'limitations': decision.get('limitations') or [],
                    'rejected': decision.get('rejected') or []}
        valid, rejected = self.validate_evidence_ids(outcome.evidence_ids, expected_scope=expected_scope,
                                                     ttl_seconds=ttl_seconds, now=now)
        if rejected:
            return {'ok': False, 'error': 'invalid_evidence_refs', 'rejected': rejected}
        outcome.evidence_ids = valid
        self.outcomes[outcome.measurement_id] = outcome.to_dict()
        self.append_event(CaseEvent(self.case.case_id, EVENT_OUTCOME_MEASURED,
                                    payload={'measurement_id': outcome.measurement_id}, evidence_ids=outcome.evidence_ids),
                          apply_state=True)
        return {'ok': True, 'outcome': outcome.to_dict(), 'case_status': self.case.status}

    def get_case_context(self):
        return {
            'case': self.case.to_dict(),
            'evidence_records': self._case_evidence_records(),
            'evidence_view': self.evidence_view.to_dict(),
            'artifacts': dict(self.artifacts),
            'hypotheses': dict(self.hypotheses),
            'actions': dict(self.actions),
            'outcomes': dict(self.outcomes),
            'events': list(self.events),
        }


__all__ = ['CaseBlackboard']
