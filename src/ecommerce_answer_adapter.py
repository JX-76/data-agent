# -*- coding: utf-8 -*-
"""Adapter from ecommerce SupervisorRuntimeResult to final Answer Contract."""
from __future__ import unicode_literals

from answer_contracts import build_final_answer_contract
from claim_graduation import (audit_answer_contract_with_provenance,
                              DEFAULT_FINAL_EVIDENCE_TTL_SECONDS)
from evidence_bus import collect_evidence_from_graph_result
from multi_agent_contracts import RESULT_OK, RESULT_ERROR, RESULT_BLOCKED, RESULT_PARTIAL, RESULT_PENDING_HUMAN_REVIEW


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


def _canonical_status(graph_result):
    status = graph_result.get('status')
    if status == RESULT_OK:
        return 'ok'
    if status == RESULT_ERROR:
        return 'error'
    if status in (RESULT_BLOCKED, RESULT_PENDING_HUMAN_REVIEW):
        return 'blocked'
    if status == RESULT_PARTIAL:
        return 'no_answer'
    return 'error'


def _terminal_outputs(graph_result):
    final_output = _as_dict(graph_result.get('final_output'))
    outputs = []
    for result in _as_list(final_output.get('terminal_results')):
        outputs.append(_as_dict(_as_dict(result).get('output')))
    return outputs


def adapt_ecommerce_graph_to_result(graph_result, query=None, trace_id=None, task_id=None):
    """Create a conservative legacy result projection for build_final_answer_contract."""
    graph_result = _as_dict(graph_result)
    graph = _as_dict(graph_result.get('graph'))
    bus = collect_evidence_from_graph_result(graph_result)
    records = bus.to_list()
    evidence_ids = [r.get('evidence_id') for r in records if r.get('evidence_id')]
    status = _canonical_status(graph_result)
    facts = []
    finding_facts = []
    key_findings = []
    limitations = []
    for err in _as_list(graph_result.get('errors')):
        err = _as_dict(err)
        if err.get('error'):
            limitations.append(err.get('error'))
    if graph_result.get('blocked_reason'):
        limitations.append(graph_result.get('blocked_reason'))
    rows = None
    envelope = None
    for node_id, result in (_as_dict(graph_result.get('results'))).items():
        result = _as_dict(result)
        output = _as_dict(result.get('output'))
        limitations.extend(_as_list(output.get('limitations')))
        if result.get('status') == RESULT_OK:
            if output.get('execution_envelope') and envelope is None:
                envelope = output.get('execution_envelope')
                rows = output.get('rows')
            for fact in _as_list(output.get('facts')):
                fact = _as_dict(fact)
                ids = [x for x in _as_list(fact.get('evidence_ids')) if x in evidence_ids]
                if fact.get('text') and ids:
                    facts.append({'text': fact.get('text'), 'evidence_ids': ids})
            for finding in _as_list(output.get('findings')):
                finding = _as_dict(finding)
                ids = [x for x in _as_list(finding.get('evidence_ids')) if x in evidence_ids]
                if finding.get('text') and ids:
                    finding_facts.append({'text': finding.get('text'), 'evidence_ids': ids})
                    key_findings.append(finding.get('text'))
        else:
            for err in _as_list(result.get('errors')):
                err = _as_dict(err)
                if err.get('error'):
                    limitations.append(err.get('error'))
    facts = finding_facts + facts
    if status != 'ok' and not limitations:
        limitations.append('graph_did_not_complete_with_verified_evidence')
    answer = ''
    if status == 'ok':
        answer = '已完成受控电商多 Agent 图执行，所有输出事实均绑定当前 verified execution evidence。'
    elif status == 'blocked' and graph_result.get('blocked_reason') == 'missing_required_graph_slots':
        answer = '电商多 Agent 图缺少必要任务槽位，已在执行前阻断，未输出数据结论。'
    elif status == 'blocked':
        answer = '电商多 Agent 图被审计或依赖状态阻断，未输出数据结论。'
    elif status == 'no_answer':
        answer = '电商多 Agent 图未形成完整可验证证据链，已降级为无答案。'
    else:
        answer = '电商多 Agent 图执行失败，未输出数据结论。'
    result = {
        'status': status,
        'query': query,
        'answer': answer,
        'analysis': {'summary': answer, 'key_findings': key_findings},
        'results': rows,
        'results_summary': {'row_count': _as_dict(envelope).get('row_count')} if envelope else None,
        'execution': {'used_db': bool(envelope), 'tool_calls': len(graph_result.get('results') or {})},
        'execution_envelope': envelope,
        'evidence_refs': evidence_ids,
        'citations': evidence_ids,
        'facts': facts,
        'limitations': sorted(set([x for x in limitations if x])),
        'next_actions': [] if status == 'ok' else (
            ['provide_missing_required_slots'] if graph_result.get('blocked_reason') == 'missing_required_graph_slots'
            else ['retry_with_verified_execution_evidence']),
        'provenance': {
            'execution': {
                'query_id': _as_dict(envelope).get('query_id'),
                'evidence_id': _as_dict(envelope).get('evidence_id'),
                'dataid': _as_dict(envelope).get('dataid'),
                'data_version': _as_dict(envelope).get('data_version'),
                'row_count': _as_dict(envelope).get('row_count'),
                'time_range': _as_dict(envelope).get('time_range'),
                'metric': _as_dict(_as_dict(envelope).get('metadata')).get('metric'),
            },
            'semantic': {'metric': _as_dict(_as_dict(envelope).get('metadata')).get('metric')},
            'evidence_bus': bus.to_dict(),
            'graph': graph,
        },
        'trace_id': trace_id or graph_result.get('trace_id'),
        'task_id': task_id,
        'graph_result': graph_result,
    }
    result['final_answer'] = build_final_answer_contract(result, query=query)
    # build_final_answer_contract is conservative about generated claims; keep
    # graph-audited facts in the product contract explicitly.
    result['final_answer']['facts'] = facts if status == 'ok' else []
    result['final_answer']['citations'] = evidence_ids
    result['final_answer']['evidence_ids'] = evidence_ids
    result['final_answer']['provenance']['query_id'] = _as_dict(envelope).get('query_id')
    result['final_answer']['provenance']['evidence_id'] = _as_dict(envelope).get('evidence_id')
    result['final_answer']['provenance']['dataid'] = _as_dict(envelope).get('dataid')
    result['final_answer']['provenance']['data_version'] = _as_dict(envelope).get('data_version')
    result['final_answer']['provenance']['row_count'] = _as_dict(envelope).get('row_count')
    result['final_answer']['provenance']['time_range'] = _as_dict(envelope).get('time_range')
    result['final_answer']['provenance']['metric'] = _as_dict(_as_dict(envelope).get('metadata')).get('metric')
    result['final_answer']['limitations'] = sorted(set((result['final_answer'].get('limitations') or []) + result['limitations']))

    # The graph adapter is an evidence-producing final-output boundary.  Recheck
    # every fact against the serialized execution records rather than trusting
    # worker text or a pre-populated evidence id.
    metadata = _as_dict(_as_dict(envelope).get('metadata'))
    accepted_ranges = [x for x in [
        _as_dict(envelope).get('time_range'),
        _as_dict(_as_dict(graph_result).get('request')).get('compare_time_range')
    ] if x]
    expected_scope = {
        'metric': metadata.get('metric'),
        'dimensions': metadata.get('dimensions') or [],
        'filters': metadata.get('filters') or {},
        'dataid': _as_dict(envelope).get('dataid'),
        'data_version': _as_dict(envelope).get('data_version'),
        'tenant_id': metadata.get('tenant_id'),
        'user_id': metadata.get('user_id'),
        'permission_scope': metadata.get('permission_scope'),
        'allowed_time_ranges': accepted_ranges,
    }
    audited, findings, was_audited = audit_answer_contract_with_provenance(
        result['final_answer'], provenance=result['provenance'], scope=expected_scope,
        ttl_seconds=DEFAULT_FINAL_EVIDENCE_TTL_SECONDS,
        require_evidence_bus=True)
    if was_audited:
        result['final_answer'] = audited
        result['claim_graduation'] = {
            'contract': 'claim_graduation_audit_v1', 'audited': True,
            'findings': findings, 'expected_scope': expected_scope,
            'ttl_seconds': DEFAULT_FINAL_EVIDENCE_TTL_SECONDS,
            'require_evidence_bus': True,
        }
        result['citations'] = list(audited.get('citations') or audited.get('evidence_ids') or [])
        result['evidence_refs'] = list(audited.get('evidence_ids') or [])
        if audited.get('status') != result.get('status'):
            result['status'] = audited.get('status')
            result['answer_type'] = audited.get('answer_type')
            result['answer'] = '当前结果未保留可用于本范围的执行证据，已安全降级，不能输出已确认数据结论。'
            result['analysis'] = {'summary': result['answer'], 'key_findings': []}
            result['limitations'] = sorted(set((result.get('limitations') or []) + [
                'final_claim_graduation_failed']))
            result['next_actions'] = ['rerun_with_current_verified_execution_evidence']
    return result


__all__ = ['adapt_ecommerce_graph_to_result']
