# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import os, sys
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__))); SRC=os.path.join(ROOT,'src')
if SRC not in sys.path: sys.path.insert(0,SRC)
from analysis_control_plane import AnalysisContract, ChartSpec, InsightClaim, apply_analysis_patch, classify_user_dispute, create_correction_plan, invalidate_downstream, make_result_checksum, validate_analysis_contract, validate_claims_for_release


def make_analysis():
    a=AnalysisContract('analysis-1','case-1','revenue','last_30_days',timezone='Asia/Shanghai',dimensions=['region'],filters={'region':'全国'},grain='day',permission_scope={'tenant_id':'t1','role':'analyst'},data_version='warehouse-2026-08-01',chart_spec=ChartSpec('chart-1','line'),evidence_ids=['ev-1'])
    a.result_checksum=make_result_checksum([{'day':'2026-08-01','revenue':100}]); a.recompute_hashes()
    return a


def test_analysis_contract_has_traceable_query_and_chart_fields():
    a=make_analysis(); data=a.to_dict()
    assert validate_analysis_contract(a)==[]
    assert data['query_hash'] and data['result_checksum']
    assert data['chart_spec']['contract']=='chart_spec_v1'


def test_data_patch_invalidates_all_downstream_and_drops_old_evidence():
    a=make_analysis(); old_hash=a.query_hash
    result=apply_analysis_patch(a,[{'field':'filters','value':{'region':'华东'}},{'field':'time_range','value':'previous_quarter'}],requested_by='u1',reason='user requested different data')
    assert result['passed'] is True
    updated=result['analysis']; patch=result['patch']
    assert updated.version==2 and updated.stale_status=='needs_recompute'
    assert updated.evidence_ids==[] and updated.result_checksum is None and updated.query_hash != old_hash
    assert set(['query_result','evidence','chart_spec','insight_claims','report_section']).issubset(set(patch['invalidates']))


def test_presentation_patch_does_not_requery_or_discard_evidence():
    a=make_analysis(); old_hash=a.query_hash
    result=apply_analysis_patch(a,[{'field':'chart_spec.chart_type','value':'bar'}])
    assert result['passed'] is True
    assert result['patch']['requires_recompute'] is False
    assert result['analysis'].chart_spec.chart_type=='bar'
    assert result['analysis'].evidence_ids==['ev-1'] and result['analysis'].query_hash==old_hash
    assert result['patch']['invalidates']==['chart_spec','report_section']


def test_unsupported_claim_is_downgraded_and_unknown_evidence_is_blocked():
    claim=InsightClaim('claim-1','收入下降 20%','fact',evidence_ids=[])
    checked=validate_claims_for_release([claim],['ev-1'])
    assert checked['passed'] is False
    assert checked['claims'][0].claim_type=='hypothesis'
    assert checked['claims'][0].validation_status=='evidence_limited'
    unknown=InsightClaim('claim-2','收入上升','trend',evidence_ids=['not-real'])
    checked2=validate_claims_for_release([unknown],['ev-1'])
    assert checked2['passed'] is False and checked2['claims'][0].validation_status=='evidence_limited'


def test_user_dispute_routes_to_correct_local_recompute_plan():
    a=make_analysis(); dispute=classify_user_dispute('不要全国数据，换成华东区', 'chart')
    plan=create_correction_plan(a,dispute,'chart-1')
    assert dispute['dispute_type']=='data_or_filter_change'
    assert 'recompute_query' in plan['steps'] and 'evidence' in plan['invalidates']
    chart_plan=create_correction_plan(a,classify_user_dispute('请把折线图改成柱状图'),'chart-1')
    assert chart_plan['dispute_type']=='chart_spec_issue'
    assert chart_plan['invalidates']==['chart_spec','report_section']


def test_upstream_failure_stales_all_downstream_claims():
    a=make_analysis(); claims=[InsightClaim('c1','trend', 'trend', evidence_ids=['ev-1'],validation_status='validated')]
    result=invalidate_downstream(a,claims,'execution_receipt_unknown')
    assert result['state']=='stale' and a.stale_status=='stale'
    assert claims[0].validation_status=='stale'
    assert 'insight_claims' in result['invalidated']
