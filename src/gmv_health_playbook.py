# -*- coding: utf-8 -*-
"""Minimal GMV Health playbook for case-based multi-agent diagnosis."""
from __future__ import unicode_literals

from case_contracts import (
    BusinessCase, MissionContract, DynamicTaskSpec,
    EVIDENCE_LEVEL_VERIFIED_FACT, EVIDENCE_LEVEL_QUANTIFIED_DRIVER,
    EVIDENCE_LEVEL_SUPPORTED_HYPOTHESIS,
)
from multi_agent_contracts import WORKER_DATA_ANALYST, WORKER_DIAGNOSIS, WORKER_AUDITOR


def build_gmv_health_case(metric='gmv', time_range='last_7_days', dimensions=None,
                          filters=None, dataid='orders', data_version=None,
                          tenant_id=None, user_id=None, permission_scope=None):
    dimensions = list(dimensions or ['channel'])
    business_scope = {
        'metric': metric,
        'time_range': time_range,
        'dimensions': dimensions,
        'filters': filters or {},
        'dataid': dataid,
        'data_version': data_version,
        'tenant_id': tenant_id,
        'user_id': user_id,
        'permission_scope': permission_scope,
    }
    mission = MissionContract(
        scenario='gmv_health',
        objective='Diagnose current GMV health, quantify verified drivers, and draft evidence-bound next actions.',
        success_criteria=[
            'current GMV signal is verified by execution evidence',
            'top contribution dimensions are quantified before root-cause claims',
            'actions reference verified evidence and remain approval-gated',
        ],
        allowed_conclusion_levels=[
            EVIDENCE_LEVEL_VERIFIED_FACT,
            EVIDENCE_LEVEL_QUANTIFIED_DRIVER,
            EVIDENCE_LEVEL_SUPPORTED_HYPOTHESIS,
        ],
        termination_conditions=[
            'root cause supported by current verified evidence',
            'evidence insufficient and next evidence task proposed',
            'budget exhausted without confirmed conclusion',
        ],
        risk_level='medium',
        budget={'max_dynamic_tasks': 6, 'max_depth': 3},
        policy={'require_verified_evidence_for_actions': True, 'human_approval_for_business_action': True},
    )
    return BusinessCase(scenario='gmv_health', business_scope=business_scope,
                        mission=mission, tenant_id=tenant_id, user_id=user_id,
                        permission_scope=permission_scope, budget=mission.budget)


def gmv_health_expected_scope(case_obj):
    case_obj = case_obj if isinstance(case_obj, BusinessCase) else BusinessCase.from_dict(case_obj)
    scope = dict(case_obj.business_scope)
    return {
        'metric': scope.get('metric'),
        'allowed_time_ranges': [scope.get('time_range')],
        'dimensions': scope.get('dimensions') or [],
        'filters': scope.get('filters') or {},
        'dataid': scope.get('dataid'),
        'data_version': scope.get('data_version'),
        'tenant_id': scope.get('tenant_id'),
        'user_id': scope.get('user_id'),
        'permission_scope': scope.get('permission_scope'),
    }


def build_gmv_health_dynamic_tasks(case_obj):
    case_obj = case_obj if isinstance(case_obj, BusinessCase) else BusinessCase.from_dict(case_obj)
    scope = dict(case_obj.business_scope)
    base_inputs = [{'name': 'business_scope', 'value': scope}]
    return [
        DynamicTaskSpec(
            task_type='verify_gmv_signal',
            goal='Run the current scoped GMV query and verify whether GMV health changed.',
            inputs=base_inputs,
            required_evidence=['current_gmv_execution_envelope'],
            preconditions=['metric,time_range,dataid resolved', 'SQL preflight passes'],
            expected_information_gain=0.9,
            cost_budget={'max_queries': 1},
            termination_rule='stop when verified execution envelope is recorded or preflight blocks',
            worker_type=WORKER_DATA_ANALYST,
            intent='metric_query',
            risk_level='low',
            priority=10,
            metadata={'case_id': case_obj.case_id},
        ),
        DynamicTaskSpec(
            task_type='decompose_gmv_drivers',
            goal='Quantify GMV change contribution by approved dimensions before proposing root cause.',
            inputs=base_inputs,
            required_evidence=['verified_gmv_signal', 'dimension_contribution_execution_envelope'],
            preconditions=['verified GMV signal exists', 'grain safety passes'],
            expected_information_gain=0.8,
            cost_budget={'max_queries': 3},
            termination_rule='stop when top drivers are quantified or evidence is insufficient',
            worker_type=WORKER_DIAGNOSIS,
            intent='driver_decomposition',
            risk_level='medium',
            priority=20,
            metadata={'case_id': case_obj.case_id},
        ),
        DynamicTaskSpec(
            task_type='challenge_root_cause',
            goal='Challenge proposed root-cause hypotheses with counter evidence and scope checks.',
            inputs=base_inputs,
            required_evidence=['supporting_evidence_ids', 'counter_evidence_review'],
            preconditions=['at least one hypothesis proposed'],
            expected_information_gain=0.6,
            cost_budget={'max_reviews': 2},
            termination_rule='stop when hypothesis is supported/rejected or more data is required',
            worker_type=WORKER_AUDITOR,
            intent='hypothesis_audit',
            risk_level='medium',
            priority=30,
            metadata={'case_id': case_obj.case_id},
        ),
    ]


__all__ = ['build_gmv_health_case', 'build_gmv_health_dynamic_tasks', 'gmv_health_expected_scope']
