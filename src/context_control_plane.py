# -*- coding: utf-8 -*-
"""P3 context, memory and cache observability control plane.

Dependency-light contracts for safe context compression.  Raw tool payloads stay
behind object refs; the model receives schema summaries with source refs.
"""
from __future__ import unicode_literals
import time

CONTEXT_STATE_CONTRACT = 'context_state_v1'
SUMMARY_CONTRACT = 'context_summary_v1'
MEMORY_CANDIDATE_CONTRACT = 'memory_candidate_v1'
CACHE_OBSERVABILITY_CONTRACT = 'prefix_cache_observability_v1'

PROTECTED_SECTIONS = set(['policy', 'permission', 'user_constraints', 'verified_facts', 'evidence_refs', 'pending_tasks', 'approval_state', 'security_markers', 'audit_markers'])


def _now(value=None): return float(time.time() if value is None else value)
def _list(value):
    if value is None: return []
    if isinstance(value, (list, tuple)): return list(value)
    return [value]
def _tokens(value): return len(str(value or '').split())


class ContextState(object):
    def __init__(self, case_id, current_goal='', case_state=None, user_constraints=None,
                 verified_facts=None, evidence_refs=None, hypotheses=None, pending_tasks=None,
                 blocked_tasks=None, user_preferences=None, rolling_summary=None,
                 security_markers=None, audit_markers=None, version=1):
        self.case_id = case_id; self.current_goal = current_goal or ''; self.case_state = dict(case_state or {})
        self.user_constraints = _list(user_constraints); self.verified_facts = _list(verified_facts)
        self.evidence_refs = _list(evidence_refs); self.hypotheses = _list(hypotheses)
        self.pending_tasks = _list(pending_tasks); self.blocked_tasks = _list(blocked_tasks)
        self.user_preferences = _list(user_preferences); self.rolling_summary = dict(rolling_summary or {})
        self.security_markers = _list(security_markers); self.audit_markers = _list(audit_markers); self.version = int(version or 1)
    def to_dict(self):
        return {'contract': CONTEXT_STATE_CONTRACT, 'case_id': self.case_id, 'current_goal': self.current_goal,
                'case_state': dict(self.case_state), 'user_constraints': list(self.user_constraints),
                'verified_facts': list(self.verified_facts), 'evidence_refs': list(self.evidence_refs),
                'hypotheses': list(self.hypotheses), 'pending_tasks': list(self.pending_tasks),
                'blocked_tasks': list(self.blocked_tasks), 'user_preferences': list(self.user_preferences),
                'rolling_summary': dict(self.rolling_summary), 'security_markers': list(self.security_markers),
                'audit_markers': list(self.audit_markers), 'version': self.version}


class ContextCompressor(object):
    def __init__(self, max_dynamic_items=3): self.max_dynamic_items = int(max_dynamic_items or 3)
    def compress(self, state, tool_observations=None, now=None):
        data = state.to_dict() if hasattr(state, 'to_dict') else dict(state)
        summary = {'contract': SUMMARY_CONTRACT, 'case_id': data.get('case_id'), 'source_context_version': data.get('version'),
                   'created_at': _now(now), 'protected_sections': sorted(PROTECTED_SECTIONS), 'source_refs': [],
                   'current_goal': data.get('current_goal'), 'case_state': data.get('case_state'),
                   'user_constraints': data.get('user_constraints') or [], 'verified_facts': data.get('verified_facts') or [],
                   'evidence_refs': data.get('evidence_refs') or [], 'pending_tasks': data.get('pending_tasks') or [],
                   'blocked_tasks': data.get('blocked_tasks') or [], 'security_markers': data.get('security_markers') or [],
                   'audit_markers': data.get('audit_markers') or [],
                   'hypotheses': (data.get('hypotheses') or [])[:self.max_dynamic_items],
                   'user_preferences': (data.get('user_preferences') or [])[:self.max_dynamic_items],
                   'rolling_summary': data.get('rolling_summary') or {}, 'tool_observation_summaries': []}
        for ev in summary['evidence_refs']:
            if isinstance(ev, dict): summary['source_refs'].append(ev.get('evidence_id') or ev.get('id'))
            else: summary['source_refs'].append(ev)
        for obs in tool_observations or []:
            ref = obs.get('payload_ref') or obs.get('object_ref')
            summary['tool_observation_summaries'].append({'tool': obs.get('tool'), 'status': obs.get('status'), 'safe_summary': obs.get('safe_summary') or obs.get('summary'), 'payload_ref': ref})
            if ref: summary['source_refs'].append(ref)
        return summary

    def benchmark(self, state, summary):
        raw = str(state.to_dict() if hasattr(state, 'to_dict') else state); compact = str(summary)
        protected_ok = all(summary.get(k) is not None for k in ['user_constraints', 'verified_facts', 'evidence_refs', 'pending_tasks', 'security_markers', 'audit_markers'])
        return {'contract': 'context_compression_benchmark_v1', 'raw_tokens': _tokens(raw), 'summary_tokens': _tokens(compact),
                'token_reduction': max(0, _tokens(raw) - _tokens(compact)), 'critical_fact_retention': len(summary.get('verified_facts') or []),
                'constraints_retention': len(summary.get('user_constraints') or []), 'pending_task_retention': len(summary.get('pending_tasks') or []),
                'source_recovery_success': len(summary.get('source_refs') or []) > 0, 'protected_sections_retained': protected_ok}


class MemoryCandidate(object):
    def __init__(self, user_id, tenant_id, key, value, source='explicit_user_preference', confidence=1.0,
                 ttl_seconds=None, scope='user', sensitivity='normal', reviewed=False):
        self.user_id = user_id; self.tenant_id = tenant_id or 'global'; self.key = key; self.value = value
        self.source = source; self.confidence = float(confidence or 0); self.ttl_seconds = ttl_seconds
        self.scope = scope; self.sensitivity = sensitivity; self.reviewed = bool(reviewed)
    def eligible_for_long_term(self):
        return self.source in ('explicit_user_preference', 'reviewed_knowledge') or (self.confidence >= 0.9 and self.reviewed)
    def to_dict(self):
        return {'contract': MEMORY_CANDIDATE_CONTRACT, 'user_id': self.user_id, 'tenant_id': self.tenant_id, 'key': self.key,
                'value': self.value, 'source': self.source, 'confidence': self.confidence, 'ttl_seconds': self.ttl_seconds,
                'scope': self.scope, 'sensitivity': self.sensitivity, 'reviewed': self.reviewed, 'eligible_for_long_term': self.eligible_for_long_term()}


class PrefixCacheObserver(object):
    def observe(self, stable_prefix='', dynamic_suffix='', provider_reported_cache_hit=None):
        return {'contract': CACHE_OBSERVABILITY_CONTRACT, 'stable_prefix_tokens': _tokens(stable_prefix),
                'dynamic_tokens': _tokens(dynamic_suffix), 'cache_eligible_tokens': _tokens(stable_prefix),
                'provider_reported_cache_hit': provider_reported_cache_hit, 'hit_rate_claimed': provider_reported_cache_hit is not None}

__all__ = ['ContextState', 'ContextCompressor', 'MemoryCandidate', 'PrefixCacheObserver', 'CONTEXT_STATE_CONTRACT', 'SUMMARY_CONTRACT', 'MEMORY_CANDIDATE_CONTRACT', 'CACHE_OBSERVABILITY_CONTRACT']
