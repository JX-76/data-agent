# -*- coding: utf-8 -*-
"""Analysis/report control plane for chart, insight and user-correction workflows.

This module is intentionally deterministic and storage-light. It provides typed
contracts for report analysis objects, local patch/dispute handling, dependency
invalidation, and fail-closed claim validation. It does not execute SQL or call
external BI services.
"""
from __future__ import unicode_literals
import hashlib, json, time

ANALYSIS_CONTRACT = 'analysis_contract_v1'
ANALYSIS_PATCH_CONTRACT = 'analysis_patch_v1'
CHART_SPEC_CONTRACT = 'chart_spec_v1'
INSIGHT_CLAIM_CONTRACT = 'insight_claim_v1'
USER_DISPUTE_CONTRACT = 'user_dispute_v1'
CORRECTION_PLAN_CONTRACT = 'correction_plan_v1'
RECOMPUTE_PLAN_CONTRACT = 'recompute_plan_v1'
REPORT_SECTION_CONTRACT = 'report_section_v1'

DATA_FIELDS = set(['metric_definition','metric_version','time_range','timezone','dimensions','filters','grain','permission_scope','data_version'])
PRESENTATION_FIELDS = set(['chart_type','title','x_axis','y_axis','series','unit'])
ALLOWED_PATCH_FIELDS = DATA_FIELDS | set(['chart_spec.%s' % x for x in PRESENTATION_FIELDS])


def _now(): return time.time()
def _stable_json(value): return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':'), default=str)
def stable_hash(value): return hashlib.sha256(_stable_json(value).encode('utf-8')).hexdigest()


def _copy(value): return json.loads(json.dumps(value))
def _get_path(obj, path):
    cur=obj
    for part in str(path or '').split('.'):
        if not part: continue
        if not isinstance(cur, dict) or part not in cur: return None
        cur=cur[part]
    return cur

def _set_path(obj, path, value):
    cur=obj; parts=str(path).split('.')
    for part in parts[:-1]:
        cur=cur.setdefault(part,{})
    cur[parts[-1]]=value


class ChartSpec(object):
    def __init__(self, chart_spec_id, chart_type='line', x_axis=None, y_axis=None, series=None, title=None, unit=None):
        self.chart_spec_id=chart_spec_id; self.chart_type=chart_type; self.x_axis=x_axis; self.y_axis=y_axis; self.series=list(series or []); self.title=title; self.unit=unit
    def to_dict(self):
        return {'contract':CHART_SPEC_CONTRACT,'chart_spec_id':self.chart_spec_id,'chart_type':self.chart_type,'x_axis':self.x_axis,'y_axis':self.y_axis,'series':self.series,'title':self.title,'unit':self.unit}
    @classmethod
    def from_dict(cls, data):
        data=data or {}; return cls(data.get('chart_spec_id'), data.get('chart_type','line'), data.get('x_axis'), data.get('y_axis'), data.get('series'), data.get('title'), data.get('unit'))


class AnalysisContract(object):
    def __init__(self, analysis_id, case_id, metric_definition, time_range, timezone='UTC', dimensions=None, filters=None, grain='day', permission_scope=None, data_version=None, metric_version='v1', chart_spec=None, version=1, evidence_ids=None, stale_status='valid', query_hash=None, result_checksum=None, created_by=None, created_at=None):
        self.analysis_id=analysis_id; self.case_id=case_id; self.metric_definition=metric_definition; self.metric_version=metric_version
        self.time_range=time_range; self.timezone=timezone; self.dimensions=list(dimensions or []); self.filters=dict(filters or {}); self.grain=grain
        self.permission_scope=dict(permission_scope or {}); self.data_version=data_version; self.chart_spec=chart_spec if isinstance(chart_spec, ChartSpec) else ChartSpec.from_dict(chart_spec or {'chart_spec_id':analysis_id+':chart'})
        self.version=version; self.evidence_ids=list(evidence_ids or []); self.stale_status=stale_status; self.query_hash=query_hash; self.result_checksum=result_checksum; self.created_by=created_by; self.created_at=created_at or _now()
    def canonical_request(self):
        return {'metric_definition':self.metric_definition,'metric_version':self.metric_version,'time_range':self.time_range,'timezone':self.timezone,'dimensions':self.dimensions,'filters':self.filters,'grain':self.grain,'permission_scope':self.permission_scope,'data_version':self.data_version}
    def recompute_hashes(self):
        self.query_hash=stable_hash(self.canonical_request()); return self.query_hash
    def to_dict(self):
        if not self.query_hash: self.recompute_hashes()
        return {'contract':ANALYSIS_CONTRACT,'analysis_id':self.analysis_id,'case_id':self.case_id,'version':self.version,'metric_definition':self.metric_definition,'metric_version':self.metric_version,'time_range':self.time_range,'timezone':self.timezone,'dimensions':self.dimensions,'filters':self.filters,'grain':self.grain,'permission_scope':self.permission_scope,'data_version':self.data_version,'query_hash':self.query_hash,'result_checksum':self.result_checksum,'evidence_ids':self.evidence_ids,'chart_spec':self.chart_spec.to_dict(),'created_by':self.created_by,'created_at':self.created_at,'stale_status':self.stale_status}
    @classmethod
    def from_dict(cls, data):
        data=data or {}; return cls(data.get('analysis_id'),data.get('case_id'),data.get('metric_definition'),data.get('time_range'),data.get('timezone','UTC'),data.get('dimensions'),data.get('filters'),data.get('grain','day'),data.get('permission_scope'),data.get('data_version'),data.get('metric_version','v1'),data.get('chart_spec'),data.get('version',1),data.get('evidence_ids'),data.get('stale_status','valid'),data.get('query_hash'),data.get('result_checksum'),data.get('created_by'),data.get('created_at'))


class InsightClaim(object):
    def __init__(self, claim_id, text, claim_type='fact', confidence='medium', evidence_ids=None, metric_refs=None, time_range=None, scope=None, validation_status='unvalidated'):
        self.claim_id=claim_id; self.text=text; self.claim_type=claim_type; self.confidence=confidence; self.evidence_ids=list(evidence_ids or []); self.metric_refs=list(metric_refs or []); self.time_range=time_range; self.scope=dict(scope or {}); self.validation_status=validation_status
    def validate(self):
        errors=[]
        if self.claim_type in ('fact','comparison','trend','anomaly') and not self.evidence_ids:
            errors.append('evidence_required_for_fact_claim')
        if self.validation_status not in ('validated','hypothesis','evidence_limited','stale','blocked','superseded','unvalidated'):
            errors.append('invalid_validation_status')
        if errors and self.claim_type!='hypothesis':
            self.claim_type='hypothesis'; self.validation_status='evidence_limited'; self.confidence='low'
        return errors
    def mark_stale(self): self.validation_status='stale'
    def to_dict(self):
        return {'contract':INSIGHT_CLAIM_CONTRACT,'claim_id':self.claim_id,'text':self.text,'claim_type':self.claim_type,'confidence':self.confidence,'evidence_ids':self.evidence_ids,'metric_refs':self.metric_refs,'time_range':self.time_range,'scope':self.scope,'validation_status':self.validation_status}


class AnalysisRepository(object):
    def __init__(self): self.analyses={}; self.claims={}; self.disputes={}; self.plans={}
    def save_analysis(self, analysis): self.analyses[analysis.analysis_id]=analysis; return analysis
    def get_analysis(self, analysis_id): return self.analyses.get(analysis_id)
    def save_claim(self, analysis_id, claim): self.claims.setdefault(analysis_id,[]).append(claim); return claim
    def get_claims(self, analysis_id): return list(self.claims.get(analysis_id,[]))


def validate_analysis_contract(analysis):
    errors=[]; data=analysis.to_dict() if isinstance(analysis,AnalysisContract) else analysis
    for field in ['analysis_id','case_id','metric_definition','time_range','permission_scope','data_version','query_hash','chart_spec']:
        if not data.get(field): errors.append('%s_required'%field)
    return errors


def validate_claims_for_release(claims, valid_evidence_ids=None):
    valid=set(valid_evidence_ids or []); errors=[]; output=[]
    for claim in claims:
        c=claim if isinstance(claim,InsightClaim) else InsightClaim(**claim)
        errs=c.validate()
        missing=[eid for eid in c.evidence_ids if valid and eid not in valid]
        if missing:
            errs.append('unknown_evidence:%s'%','.join(missing)); c.claim_type='hypothesis'; c.validation_status='evidence_limited'; c.confidence='low'
        if errs: errors.append({'claim_id':c.claim_id,'errors':errs})
        output.append(c)
    return {'passed':not errors,'errors':errors,'claims':output}


def make_result_checksum(rows): return stable_hash(rows or [])


def apply_analysis_patch(analysis, patch_ops, requested_by=None, reason=None):
    base=AnalysisContract.from_dict(analysis.to_dict() if isinstance(analysis,AnalysisContract) else analysis)
    changed=[]; invalidates=set(); requires_recompute=False
    for op in patch_ops or []:
        field=op.get('field'); value=op.get('value')
        if field not in ALLOWED_PATCH_FIELDS:
            return {'contract':ANALYSIS_PATCH_CONTRACT,'passed':False,'error':'field_not_allowed:%s'%field,'base_version':base.version}
        before=_get_path(base.to_dict(), field)
        if field.startswith('chart_spec.'):
            setattr(base.chart_spec, field.split('.',1)[1], value); invalidates.update(['chart_spec','report_section'])
        else:
            setattr(base, field, value); requires_recompute=True; invalidates.update(['query_result','evidence','chart_spec','insight_claims','report_section'])
        changed.append({'field':field,'before':before,'after':value})
    base.version += 1; base.stale_status='needs_recompute' if requires_recompute else 'valid'
    if requires_recompute:
        base.evidence_ids=[]; base.result_checksum=None; base.recompute_hashes()
    patch={'contract':ANALYSIS_PATCH_CONTRACT,'patch_id':'patch_'+stable_hash({'analysis_id':base.analysis_id,'version':base.version,'changed':changed})[:12],'analysis_id':base.analysis_id,'base_version':base.version-1,'new_version':base.version,'operations':changed,'requires_recompute':requires_recompute,'invalidates':sorted(invalidates),'requested_by':requested_by,'reason':reason,'passed':True}
    return {'contract':ANALYSIS_PATCH_CONTRACT,'passed':True,'analysis':base,'patch':patch}


def classify_user_dispute(feedback, target_type='claim'):
    text=(feedback or '').lower()
    if any(x in text for x in ['数据','data','region','时间','time','范围','filter','换成','不要']): kind='data_or_filter_change'
    elif any(x in text for x in ['口径','metric','definition']): kind='metric_definition_issue'
    elif any(x in text for x in ['图','chart','柱状','折线']): kind='chart_spec_issue'
    elif any(x in text for x in ['权限','permission','看不到']): kind='permission_scope_issue'
    else: kind='claim_dispute'
    return {'contract':USER_DISPUTE_CONTRACT,'dispute_type':kind,'target_type':target_type,'feedback':feedback}


def create_correction_plan(analysis, dispute, target_id=None):
    kind=dispute.get('dispute_type') if isinstance(dispute,dict) else dispute
    if kind in ('data_or_filter_change','metric_definition_issue','permission_scope_issue'):
        steps=['inspect_analysis_contract','validate_patch','recompute_query','refresh_evidence','regenerate_chart','revalidate_claims']
        invalidates=['query_result','evidence','chart_spec','insight_claims','report_section']
    elif kind=='chart_spec_issue':
        steps=['inspect_chart_spec','apply_presentation_patch','regenerate_report_section']
        invalidates=['chart_spec','report_section']
    else:
        steps=['inspect_claim_evidence','revalidate_claim','downgrade_or_recompute']
        invalidates=['insight_claims','report_section']
    return {'contract':CORRECTION_PLAN_CONTRACT,'plan_id':'correction_'+stable_hash({'analysis_id':analysis.analysis_id,'kind':kind,'target':target_id})[:12],'analysis_id':analysis.analysis_id,'target_id':target_id,'dispute_type':kind,'steps':steps,'invalidates':invalidates,'requires_release_gate':True,'safe_response':'show_contract_diff_then_recompute_or_downgrade'}


def invalidate_downstream(analysis, claims, reason='upstream_failed'):
    analysis.stale_status='stale'; out=[]
    for claim in claims:
        claim.mark_stale(); out.append(claim)
    return {'contract':RECOMPUTE_PLAN_CONTRACT,'analysis_id':analysis.analysis_id,'state':'stale','reason':reason,'invalidated':['evidence','chart_spec','insight_claims','report_section'],'claims':out}


__all__=['AnalysisContract','ChartSpec','InsightClaim','AnalysisRepository','validate_analysis_contract','validate_claims_for_release','apply_analysis_patch','classify_user_dispute','create_correction_plan','invalidate_downstream','make_result_checksum','stable_hash']
