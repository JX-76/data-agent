# -*- coding: utf-8 -*-
"""Case-based teaming contracts for ecommerce operations.

These contracts are deliberately lightweight and Python 2.7 compatible.  They
provide the control-plane objects for the next architecture layer without
replacing the existing SupervisorRuntime, AgentTask, EvidenceBus or Answer
Contract.
"""
from __future__ import unicode_literals

import time
import uuid


CASE_CONTRACT_VERSION = 'business_case_v1'
MISSION_CONTRACT_VERSION = 'mission_contract_v1'
ARTIFACT_CONTRACT_VERSION = 'case_artifact_v1'
HYPOTHESIS_CONTRACT_VERSION = 'case_hypothesis_v1'
ACTION_CONTRACT_VERSION = 'action_card_v1'
OUTCOME_CONTRACT_VERSION = 'outcome_measurement_v1'
DYNAMIC_TASK_CONTRACT_VERSION = 'dynamic_task_spec_v1'
CASE_EVENT_CONTRACT_VERSION = 'case_event_v1'

CASE_NEW = 'NEW'
CASE_SCOPING = 'SCOPING'
CASE_SIGNAL_CONFIRMED = 'SIGNAL_CONFIRMED'
CASE_INVESTIGATING = 'INVESTIGATING'
CASE_HYPOTHESIS_PENDING = 'HYPOTHESIS_PENDING'
CASE_EVIDENCE_INSUFFICIENT = 'EVIDENCE_INSUFFICIENT'
CASE_ROOT_CAUSE_CONFIRMED = 'ROOT_CAUSE_CONFIRMED'
CASE_ACTION_DRAFTED = 'ACTION_DRAFTED'
CASE_PENDING_APPROVAL = 'PENDING_APPROVAL'
CASE_ACTION_IN_PROGRESS = 'ACTION_IN_PROGRESS'
CASE_OUTCOME_MEASURING = 'OUTCOME_MEASURING'
CASE_RESOLVED = 'RESOLVED'
CASE_LEARNED = 'LEARNED'
CASE_CLOSED = 'CLOSED'

ARTIFACT_SIGNAL = 'signal'
ARTIFACT_DRIVER_TREE = 'driver_tree'
ARTIFACT_CONTRIBUTION = 'contribution'
ARTIFACT_DRILLDOWN = 'drilldown'
ARTIFACT_DIAGNOSIS = 'diagnosis'
ARTIFACT_POLICY_DECISION = 'policy_decision'

HYPOTHESIS_PROPOSED = 'proposed'
HYPOTHESIS_CHALLENGED = 'challenged'
HYPOTHESIS_NEEDS_EVIDENCE = 'needs_evidence'
HYPOTHESIS_SUPPORTED = 'supported'
HYPOTHESIS_REJECTED = 'rejected'
HYPOTHESIS_CONFIRMED = 'confirmed'

ACTION_DRAFT = 'draft'
ACTION_POLICY_CHECKED = 'policy_checked'
ACTION_PENDING_APPROVAL = 'pending_approval'
ACTION_APPROVED = 'approved'
ACTION_REJECTED = 'rejected'
ACTION_HANDED_OFF = 'handed_off'
ACTION_EXECUTED = 'executed'
ACTION_NOT_EXECUTED = 'not_executed'
ACTION_MEASURING = 'measuring'
ACTION_MEASURED = 'measured'
ACTION_CLOSED = 'closed'

EVIDENCE_LEVEL_NONE = 'none'
EVIDENCE_LEVEL_VERIFIED_FACT = 'verified_fact'
EVIDENCE_LEVEL_QUANTIFIED_DRIVER = 'quantified_driver'
EVIDENCE_LEVEL_SUPPORTED_HYPOTHESIS = 'supported_hypothesis'
EVIDENCE_LEVEL_CONFIRMED_CAUSAL = 'confirmed_causal'

EVENT_CASE_CREATED = 'case.created'
EVENT_SCOPE_RESOLVED = 'scope.resolved'
EVENT_SIGNAL_DETECTED = 'signal.detected'
EVENT_EVIDENCE_VERIFIED = 'evidence.verified'
EVENT_DRIVER_DECOMPOSED = 'driver.decomposed'
EVENT_HYPOTHESIS_PROPOSED = 'hypothesis.proposed'
EVENT_HYPOTHESIS_CHALLENGED = 'hypothesis.challenged'
EVENT_ROOT_CAUSE_CONFIRMED = 'root_cause.confirmed'
EVENT_EVIDENCE_INSUFFICIENT = 'evidence.insufficient'
EVENT_ACTION_DRAFTED = 'action.drafted'
EVENT_APPROVAL_REQUESTED = 'approval.requested'
EVENT_APPROVAL_APPROVED = 'approval.approved'
EVENT_ACTION_EXECUTED = 'action.executed'
EVENT_OUTCOME_MEASURED = 'outcome.measured'
EVENT_CASE_CLOSED = 'case.closed'


def _new_id(prefix):
    return '%s_%s' % (prefix, str(uuid.uuid4()).replace('-', '')[:16])


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


def _copy_dict(value):
    return dict(_as_dict(value))


class MissionContract(object):
    def __init__(self, scenario='gmv_health', objective='', success_criteria=None,
                 allowed_conclusion_levels=None, termination_conditions=None,
                 risk_level='medium', budget=None, policy=None):
        self.scenario = scenario or 'gmv_health'
        self.objective = objective or ''
        self.success_criteria = _as_list(success_criteria)
        self.allowed_conclusion_levels = _as_list(allowed_conclusion_levels or [
            EVIDENCE_LEVEL_VERIFIED_FACT, EVIDENCE_LEVEL_QUANTIFIED_DRIVER,
            EVIDENCE_LEVEL_SUPPORTED_HYPOTHESIS])
        self.termination_conditions = _as_list(termination_conditions)
        self.risk_level = risk_level or 'medium'
        self.budget = _copy_dict(budget)
        self.policy = _copy_dict(policy)

    @classmethod
    def from_dict(cls, data):
        data = _as_dict(data)
        return cls(
            scenario=data.get('scenario') or 'gmv_health',
            objective=data.get('objective') or '',
            success_criteria=data.get('success_criteria') or [],
            allowed_conclusion_levels=data.get('allowed_conclusion_levels') or [],
            termination_conditions=data.get('termination_conditions') or [],
            risk_level=data.get('risk_level') or 'medium',
            budget=data.get('budget') or {},
            policy=data.get('policy') or {},
        )

    def to_dict(self):
        return {
            'contract': MISSION_CONTRACT_VERSION,
            'scenario': self.scenario,
            'objective': self.objective,
            'success_criteria': list(self.success_criteria),
            'allowed_conclusion_levels': list(self.allowed_conclusion_levels),
            'termination_conditions': list(self.termination_conditions),
            'risk_level': self.risk_level,
            'budget': dict(self.budget),
            'policy': dict(self.policy),
        }


class BusinessCase(object):
    def __init__(self, case_id=None, scenario='gmv_health', business_scope=None,
                 mission=None, status=CASE_NEW, tenant_id=None, user_id=None,
                 permission_scope=None, budget=None, created_at=None, updated_at=None,
                 timeline=None, metadata=None):
        self.case_id = case_id or _new_id('case')
        self.scenario = scenario or 'gmv_health'
        self.business_scope = _copy_dict(business_scope)
        self.mission = mission if isinstance(mission, MissionContract) else MissionContract.from_dict(mission or {'scenario': self.scenario})
        self.status = status or CASE_NEW
        self.tenant_id = tenant_id or self.business_scope.get('tenant_id')
        self.user_id = user_id or self.business_scope.get('user_id')
        self.permission_scope = permission_scope or self.business_scope.get('permission_scope')
        self.budget = _copy_dict(budget)
        self.created_at = created_at if created_at is not None else time.time()
        self.updated_at = updated_at if updated_at is not None else self.created_at
        self.timeline = _as_list(timeline)
        self.metadata = _copy_dict(metadata)

    @classmethod
    def from_dict(cls, data):
        data = _as_dict(data)
        return cls(
            case_id=data.get('case_id'), scenario=data.get('scenario') or 'gmv_health',
            business_scope=data.get('business_scope') or {}, mission=data.get('mission') or {},
            status=data.get('status') or CASE_NEW, tenant_id=data.get('tenant_id'),
            user_id=data.get('user_id'), permission_scope=data.get('permission_scope'),
            budget=data.get('budget') or {}, created_at=data.get('created_at'),
            updated_at=data.get('updated_at'), timeline=data.get('timeline') or [],
            metadata=data.get('metadata') or {})

    def to_dict(self):
        return {
            'contract': CASE_CONTRACT_VERSION,
            'case_id': self.case_id,
            'scenario': self.scenario,
            'business_scope': dict(self.business_scope),
            'mission': self.mission.to_dict(),
            'status': self.status,
            'tenant_id': self.tenant_id,
            'user_id': self.user_id,
            'permission_scope': self.permission_scope,
            'budget': dict(self.budget),
            'created_at': self.created_at,
            'updated_at': self.updated_at,
            'timeline': list(self.timeline),
            'metadata': dict(self.metadata),
        }


class CaseArtifact(object):
    def __init__(self, case_id, artifact_type, payload=None, artifact_id=None,
                 status='ok', evidence_ids=None, produced_by=None, confidence=None,
                 scope=None, created_at=None, metadata=None):
        self.artifact_id = artifact_id or _new_id('art')
        self.case_id = case_id
        self.artifact_type = artifact_type
        self.status = status or 'ok'
        self.payload = _copy_dict(payload)
        self.evidence_ids = _as_list(evidence_ids)
        self.produced_by = produced_by
        self.confidence = confidence
        self.scope = _copy_dict(scope)
        self.created_at = created_at if created_at is not None else time.time()
        self.metadata = _copy_dict(metadata)

    @classmethod
    def from_dict(cls, data):
        data = _as_dict(data)
        return cls(data.get('case_id'), data.get('artifact_type'), payload=data.get('payload') or {},
                   artifact_id=data.get('artifact_id'), status=data.get('status') or 'ok',
                   evidence_ids=data.get('evidence_ids') or [], produced_by=data.get('produced_by'),
                   confidence=data.get('confidence'), scope=data.get('scope') or {},
                   created_at=data.get('created_at'), metadata=data.get('metadata') or {})

    def to_dict(self):
        return {
            'contract': ARTIFACT_CONTRACT_VERSION,
            'artifact_id': self.artifact_id,
            'case_id': self.case_id,
            'artifact_type': self.artifact_type,
            'status': self.status,
            'payload': dict(self.payload),
            'evidence_ids': list(self.evidence_ids),
            'produced_by': self.produced_by,
            'confidence': self.confidence,
            'scope': dict(self.scope),
            'created_at': self.created_at,
            'metadata': dict(self.metadata),
        }


class Hypothesis(object):
    def __init__(self, case_id, statement, hypothesis_id=None, status=HYPOTHESIS_PROPOSED,
                 confidence=0.0, support_evidence_ids=None, counter_evidence_ids=None,
                 confounders=None, validation_plan=None, created_at=None, updated_at=None,
                 metadata=None):
        self.hypothesis_id = hypothesis_id or _new_id('hyp')
        self.case_id = case_id
        self.statement = statement or ''
        self.status = status or HYPOTHESIS_PROPOSED
        self.confidence = float(confidence or 0.0)
        self.support_evidence_ids = _as_list(support_evidence_ids)
        self.counter_evidence_ids = _as_list(counter_evidence_ids)
        self.confounders = _as_list(confounders)
        self.validation_plan = _as_list(validation_plan)
        self.created_at = created_at if created_at is not None else time.time()
        self.updated_at = updated_at if updated_at is not None else self.created_at
        self.metadata = _copy_dict(metadata)

    @classmethod
    def from_dict(cls, data):
        data = _as_dict(data)
        return cls(data.get('case_id'), data.get('statement') or '', hypothesis_id=data.get('hypothesis_id'),
                   status=data.get('status') or HYPOTHESIS_PROPOSED, confidence=data.get('confidence') or 0.0,
                   support_evidence_ids=data.get('support_evidence_ids') or [],
                   counter_evidence_ids=data.get('counter_evidence_ids') or [],
                   confounders=data.get('confounders') or [], validation_plan=data.get('validation_plan') or [],
                   created_at=data.get('created_at'), updated_at=data.get('updated_at'), metadata=data.get('metadata') or {})

    def to_dict(self):
        return {
            'contract': HYPOTHESIS_CONTRACT_VERSION,
            'hypothesis_id': self.hypothesis_id,
            'case_id': self.case_id,
            'statement': self.statement,
            'status': self.status,
            'confidence': self.confidence,
            'support_evidence_ids': list(self.support_evidence_ids),
            'counter_evidence_ids': list(self.counter_evidence_ids),
            'confounders': list(self.confounders),
            'validation_plan': list(self.validation_plan),
            'created_at': self.created_at,
            'updated_at': self.updated_at,
            'metadata': dict(self.metadata),
        }


class ActionCard(object):
    def __init__(self, case_id, action_type, target=None, proposal=None, action_id=None,
                 strategy_id=None, strategy_version=None, expected_impact=None,
                 confidence=0.0, evidence_ids=None, constraints=None, violations=None,
                 risk_level='medium', approval_required=True, owner=None, due_at=None,
                 review_at=None, success_metrics=None, status=ACTION_DRAFT, metadata=None):
        self.action_id = action_id or _new_id('act')
        self.case_id = case_id
        self.action_type = action_type or 'investigate'
        self.strategy_id = strategy_id
        self.strategy_version = strategy_version
        self.target = _copy_dict(target)
        self.proposal = _copy_dict(proposal)
        self.expected_impact = _copy_dict(expected_impact)
        self.confidence = float(confidence or 0.0)
        self.evidence_ids = _as_list(evidence_ids)
        self.constraints = _as_list(constraints)
        self.violations = _as_list(violations)
        self.risk_level = risk_level or 'medium'
        self.approval_required = bool(approval_required)
        self.owner = owner
        self.due_at = due_at
        self.review_at = review_at
        self.success_metrics = _as_list(success_metrics)
        self.status = status or ACTION_DRAFT
        self.metadata = _copy_dict(metadata)

    @classmethod
    def from_dict(cls, data):
        data = _as_dict(data)
        return cls(data.get('case_id'), data.get('action_type'), target=data.get('target') or {},
                   proposal=data.get('proposal') or {}, action_id=data.get('action_id'),
                   strategy_id=data.get('strategy_id'), strategy_version=data.get('strategy_version'),
                   expected_impact=data.get('expected_impact') or {}, confidence=data.get('confidence') or 0.0,
                   evidence_ids=data.get('evidence_ids') or [], constraints=data.get('constraints') or [],
                   violations=data.get('violations') or [], risk_level=data.get('risk_level') or 'medium',
                   approval_required=data.get('approval_required', True), owner=data.get('owner'),
                   due_at=data.get('due_at'), review_at=data.get('review_at'),
                   success_metrics=data.get('success_metrics') or [], status=data.get('status') or ACTION_DRAFT,
                   metadata=data.get('metadata') or {})

    def to_dict(self):
        return {
            'contract': ACTION_CONTRACT_VERSION,
            'action_id': self.action_id,
            'case_id': self.case_id,
            'action_type': self.action_type,
            'strategy_id': self.strategy_id,
            'strategy_version': self.strategy_version,
            'target': dict(self.target),
            'proposal': dict(self.proposal),
            'expected_impact': dict(self.expected_impact),
            'confidence': self.confidence,
            'evidence_ids': list(self.evidence_ids),
            'constraints': list(self.constraints),
            'violations': list(self.violations),
            'risk_level': self.risk_level,
            'approval_required': self.approval_required,
            'owner': self.owner,
            'due_at': self.due_at,
            'review_at': self.review_at,
            'success_metrics': list(self.success_metrics),
            'status': self.status,
            'metadata': dict(self.metadata),
        }


class OutcomeMeasurement(object):
    def __init__(self, case_id, action_id, observed_impact=None, measurement_id=None,
                 baseline=None, executed_at=None, observation_window=None, method='matched_baseline',
                 confidence_interval=None, decision='need_more_data', learning='', evidence_ids=None,
                 limitations=None, status='measured', metadata=None):
        self.measurement_id = measurement_id or _new_id('out')
        self.case_id = case_id
        self.action_id = action_id
        self.baseline = _copy_dict(baseline)
        self.executed_at = executed_at
        self.observation_window = observation_window
        self.observed_impact = _copy_dict(observed_impact)
        self.method = method or 'matched_baseline'
        self.confidence_interval = _as_list(confidence_interval)
        self.decision = decision or 'need_more_data'
        self.learning = learning or ''
        self.evidence_ids = _as_list(evidence_ids)
        self.limitations = _as_list(limitations)
        self.status = status or 'measured'
        self.metadata = _copy_dict(metadata)

    @classmethod
    def from_dict(cls, data):
        data = _as_dict(data)
        return cls(data.get('case_id'), data.get('action_id'), observed_impact=data.get('observed_impact') or {},
                   measurement_id=data.get('measurement_id'), baseline=data.get('baseline') or {},
                   executed_at=data.get('executed_at'), observation_window=data.get('observation_window'),
                   method=data.get('method') or 'matched_baseline', confidence_interval=data.get('confidence_interval') or [],
                   decision=data.get('decision') or 'need_more_data', learning=data.get('learning') or '',
                   evidence_ids=data.get('evidence_ids') or [], limitations=data.get('limitations') or [],
                   status=data.get('status') or 'measured', metadata=data.get('metadata') or {})

    def to_dict(self):
        return {
            'contract': OUTCOME_CONTRACT_VERSION,
            'measurement_id': self.measurement_id,
            'case_id': self.case_id,
            'action_id': self.action_id,
            'baseline': dict(self.baseline),
            'executed_at': self.executed_at,
            'observation_window': self.observation_window,
            'observed_impact': dict(self.observed_impact),
            'method': self.method,
            'confidence_interval': list(self.confidence_interval),
            'decision': self.decision,
            'learning': self.learning,
            'evidence_ids': list(self.evidence_ids),
            'limitations': list(self.limitations),
            'status': self.status,
            'metadata': dict(self.metadata),
        }


class DynamicTaskSpec(object):
    def __init__(self, task_type, goal='', inputs=None, required_evidence=None,
                 preconditions=None, expected_information_gain=0.0, cost_budget=None,
                 termination_rule='', authority='analysis_only', worker_type=None,
                 intent=None, risk_level='low', task_id=None, priority=None, metadata=None):
        self.task_id = task_id or _new_id('dtask')
        self.task_type = task_type
        self.goal = goal or ''
        self.inputs = _as_list(inputs)
        self.required_evidence = _as_list(required_evidence)
        self.preconditions = _as_list(preconditions)
        self.expected_information_gain = float(expected_information_gain or 0.0)
        self.cost_budget = _copy_dict(cost_budget)
        self.termination_rule = termination_rule or ''
        self.authority = authority or 'analysis_only'
        self.worker_type = worker_type
        self.intent = intent
        self.risk_level = risk_level or 'low'
        self.priority = priority
        self.metadata = _copy_dict(metadata)

    @classmethod
    def from_dict(cls, data):
        data = _as_dict(data)
        return cls(data.get('task_type'), goal=data.get('goal') or '', inputs=data.get('inputs') or [],
                   required_evidence=data.get('required_evidence') or [], preconditions=data.get('preconditions') or [],
                   expected_information_gain=data.get('expected_information_gain') or 0.0,
                   cost_budget=data.get('cost_budget') or {}, termination_rule=data.get('termination_rule') or '',
                   authority=data.get('authority') or 'analysis_only', worker_type=data.get('worker_type'),
                   intent=data.get('intent'), risk_level=data.get('risk_level') or 'low', task_id=data.get('task_id'),
                   priority=data.get('priority'), metadata=data.get('metadata') or {})

    def to_dict(self):
        return {
            'contract': DYNAMIC_TASK_CONTRACT_VERSION,
            'task_id': self.task_id,
            'task_type': self.task_type,
            'goal': self.goal,
            'inputs': list(self.inputs),
            'required_evidence': list(self.required_evidence),
            'preconditions': list(self.preconditions),
            'expected_information_gain': self.expected_information_gain,
            'cost_budget': dict(self.cost_budget),
            'termination_rule': self.termination_rule,
            'authority': self.authority,
            'worker_type': self.worker_type,
            'intent': self.intent,
            'risk_level': self.risk_level,
            'priority': self.priority,
            'metadata': dict(self.metadata),
        }


class CaseEvent(object):
    def __init__(self, case_id, event_type, payload=None, event_id=None, source=None,
                 artifact_ids=None, evidence_ids=None, timestamp=None, metadata=None):
        self.event_id = event_id or _new_id('evt')
        self.case_id = case_id
        self.event_type = event_type
        self.payload = _copy_dict(payload)
        self.source = source
        self.artifact_ids = _as_list(artifact_ids)
        self.evidence_ids = _as_list(evidence_ids)
        self.timestamp = timestamp if timestamp is not None else time.time()
        self.metadata = _copy_dict(metadata)

    @classmethod
    def from_dict(cls, data):
        data = _as_dict(data)
        return cls(data.get('case_id'), data.get('event_type'), payload=data.get('payload') or {},
                   event_id=data.get('event_id'), source=data.get('source'),
                   artifact_ids=data.get('artifact_ids') or [], evidence_ids=data.get('evidence_ids') or [],
                   timestamp=data.get('timestamp'), metadata=data.get('metadata') or {})

    def to_dict(self):
        return {
            'contract': CASE_EVENT_CONTRACT_VERSION,
            'event_id': self.event_id,
            'case_id': self.case_id,
            'event_type': self.event_type,
            'payload': dict(self.payload),
            'source': self.source,
            'artifact_ids': list(self.artifact_ids),
            'evidence_ids': list(self.evidence_ids),
            'timestamp': self.timestamp,
            'metadata': dict(self.metadata),
        }


def validate_contract_payload(payload, expected_contract):
    data = _as_dict(payload)
    errors = []
    if data.get('contract') != expected_contract:
        errors.append('contract_mismatch')
    if expected_contract in (CASE_CONTRACT_VERSION, ARTIFACT_CONTRACT_VERSION,
                             HYPOTHESIS_CONTRACT_VERSION, ACTION_CONTRACT_VERSION,
                             OUTCOME_CONTRACT_VERSION, CASE_EVENT_CONTRACT_VERSION):
        if not data.get('case_id'):
            errors.append('missing_case_id')
    return {'ok': not errors, 'errors': errors}


__all__ = [
    'BusinessCase', 'MissionContract', 'CaseArtifact', 'Hypothesis', 'ActionCard',
    'OutcomeMeasurement', 'DynamicTaskSpec', 'CaseEvent', 'validate_contract_payload',
    'CASE_NEW', 'CASE_SCOPING', 'CASE_SIGNAL_CONFIRMED', 'CASE_INVESTIGATING',
    'CASE_HYPOTHESIS_PENDING', 'CASE_EVIDENCE_INSUFFICIENT', 'CASE_ROOT_CAUSE_CONFIRMED',
    'CASE_ACTION_DRAFTED', 'CASE_PENDING_APPROVAL', 'CASE_ACTION_IN_PROGRESS',
    'CASE_OUTCOME_MEASURING', 'CASE_RESOLVED', 'CASE_LEARNED', 'CASE_CLOSED',
    'ARTIFACT_SIGNAL', 'ARTIFACT_DRIVER_TREE', 'ARTIFACT_CONTRIBUTION', 'ARTIFACT_DRILLDOWN',
    'ARTIFACT_DIAGNOSIS', 'EVENT_CASE_CREATED', 'EVENT_SCOPE_RESOLVED', 'EVENT_SIGNAL_DETECTED',
    'EVENT_EVIDENCE_VERIFIED', 'EVENT_DRIVER_DECOMPOSED', 'EVENT_HYPOTHESIS_PROPOSED',
    'EVENT_HYPOTHESIS_CHALLENGED', 'EVENT_ROOT_CAUSE_CONFIRMED', 'EVENT_EVIDENCE_INSUFFICIENT',
    'EVENT_ACTION_DRAFTED', 'EVENT_APPROVAL_REQUESTED', 'EVENT_APPROVAL_APPROVED',
    'EVENT_ACTION_EXECUTED', 'EVENT_OUTCOME_MEASURED', 'EVENT_CASE_CLOSED',
    'EVIDENCE_LEVEL_NONE', 'EVIDENCE_LEVEL_VERIFIED_FACT', 'EVIDENCE_LEVEL_QUANTIFIED_DRIVER',
    'EVIDENCE_LEVEL_SUPPORTED_HYPOTHESIS', 'EVIDENCE_LEVEL_CONFIRMED_CAUSAL'
]
