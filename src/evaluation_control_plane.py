# -*- coding: utf-8 -*-
"""Unified deterministic evaluation control plane for production-control tests.

Evaluation is separate from unit tests: it consumes versioned case fixtures and
observations, produces reproducible metrics/badcases, and compares them with a
versioned baseline. No online LLM judge is enabled here.
"""
from __future__ import unicode_literals
import codecs, json, os, time

EVAL_CASE_CONTRACT = 'eval_case_v1'
EVAL_RESULT_CONTRACT = 'eval_result_v1'
EVAL_REPORT_CONTRACT = 'evaluation_report_v1'
EVAL_BASELINE_CONTRACT = 'evaluation_baseline_v1'
BADCASE_CONTRACT = 'evaluation_badcase_v1'

BADCASE_TAXONOMY = set(['routing_error','planning_error','tool_selection_error','tool_parameter_error','retrieval_miss','retrieval_staleness','unsupported_claim','citation_failure','scope_permission_issue','acl_leakage','context_loss','runtime_failure','policy_action_denial','trace_error','contract_error'])
ZERO_TOLERANCE_DEFAULT = ['safety_violation_count','scope_leakage_count','acl_leakage_count','duplicate_side_effect_count','unauthorized_action_count','fabricated_metric_count']
LABEL_SOURCES = set(['contract_fixture', 'model_assisted', 'imported', 'human_verified'])
VERIFICATION_STATUSES = set(['unverified', 'sampled_reviewed', 'human_verified', 'rejected'])


def validate_gold_provenance(metadata):
    """Prevent model-assisted labels from being represented as verified gold."""
    metadata = metadata or {}; errors=[]
    source=metadata.get('label_source', 'contract_fixture'); verification=metadata.get('verification_status', 'unverified')
    if source not in LABEL_SOURCES: errors.append('invalid_label_source')
    if verification not in VERIFICATION_STATUSES: errors.append('invalid_verification_status')
    if source == 'model_assisted':
        if not metadata.get('model_ref'): errors.append('model_assisted_requires_model_ref')
        if not metadata.get('prompt_version'): errors.append('model_assisted_requires_prompt_version')
        if metadata.get('blocking'): errors.append('model_assisted_cannot_be_blocking')
        if verification == 'human_verified': errors.append('model_assisted_requires_source_promotion_before_human_verified')
    if source == 'human_verified' and not metadata.get('annotator_ref'): errors.append('human_verified_requires_annotator_ref')
    if verification == 'rejected' and metadata.get('blocking'): errors.append('rejected_case_cannot_be_blocking')
    return errors


def _rate(value, total): return round(float(value) / float(total or 1), 4)
def _path_get(value, path):
    current = value
    for key in str(path or '').split('.'):
        if not key: continue
        if not isinstance(current, dict) or key not in current: return None
        current = current[key]
    return current


class EvalCase(object):
    def __init__(self, case_id, suite, phase, category, observation=None, assertions=None, gold=None, expected=None, metadata=None):
        self.case_id=case_id; self.suite=suite; self.phase=phase; self.category=category
        self.observation=dict(observation or {}); self.assertions=list(assertions or []); self.gold=dict(gold or {}); self.expected=dict(expected or {}); self.metadata=dict(metadata or {})
    @classmethod
    def from_dict(cls, data):
        data=data or {}
        return cls(data.get('case_id') or data.get('id'), data.get('suite','full'), data.get('phase','E2E'), data.get('category','uncategorized'), data.get('observation'), data.get('assertions'), data.get('gold'), data.get('expected'), data.get('metadata'))
    def validate(self):
        errors=[]
        if not self.case_id: errors.append('case_id_required')
        if not self.phase: errors.append('phase_required')
        if not self.assertions: errors.append('assertions_required')
        if self.metadata.get('weak_supervision') and self.metadata.get('blocking'):
            errors.append('weak_supervision_cannot_be_blocking')
        errors.extend(validate_gold_provenance(self.metadata))
        return errors
    def to_dict(self):
        return {'contract':EVAL_CASE_CONTRACT,'case_id':self.case_id,'suite':self.suite,'phase':self.phase,'category':self.category,'observation':self.observation,'assertions':self.assertions,'gold':self.gold,'expected':self.expected,'metadata':self.metadata}


class EvaluationRunner(object):
    def evaluate_case(self, case):
        case = case if isinstance(case, EvalCase) else EvalCase.from_dict(case); errors=case.validate(); checks=[]
        for assertion in case.assertions:
            passed, detail, metric, value = self._score(assertion, case.observation, case.gold, case.expected)
            checks.append({'scorer':assertion.get('scorer'),'passed':passed,'detail':detail,'metric':metric,'value':value})
            if not passed: errors.append(detail)
        passed=not errors; failure=case.metadata.get('failure_category') if not passed else None
        if failure not in BADCASE_TAXONOMY: failure=self._infer_failure(case, errors)
        result={'contract':EVAL_RESULT_CONTRACT,'case_id':case.case_id,'suite':case.suite,'phase':case.phase,'category':case.category,'passed':passed,'score':_rate(sum(1 for x in checks if x['passed']),len(checks)),'metric_breakdown':dict((x['metric'],x['value']) for x in checks if x['metric']),'failed_assertions':errors,'failure_category':failure,'checks':checks,'trace':dict(case.observation.get('trace') or {}),'latency_ms':case.observation.get('latency_ms'),'token_usage':case.observation.get('token_usage','not_measured'),'cost_estimate':case.observation.get('cost_estimate','not_measured'),'weak_supervision':bool(case.metadata.get('weak_supervision'))}
        return result
    def _score(self, assertion, obs, gold, expected):
        scorer=assertion.get('scorer'); path=assertion.get('path'); got=_path_get(obs,path)
        want=assertion.get('value', expected.get(path, gold.get(path)))
        if scorer=='field_equals': return got==want, '%s expected=%s got=%s'%(path,want,got), assertion.get('metric'), 1.0 if got==want else 0.0
        if scorer=='field_exists': return got is not None, '%s missing'%path, assertion.get('metric'), 1.0 if got is not None else 0.0
        if scorer=='contains': return want in (got or []), '%s lacks %s'%(path,want), assertion.get('metric'), 1.0 if want in (got or []) else 0.0
        if scorer=='numeric_min': return got is not None and got>=want, '%s below %s'%(path,want), assertion.get('metric'), got
        if scorer=='numeric_max': return got is not None and got<=want, '%s above %s'%(path,want), assertion.get('metric'), got
        if scorer=='zero': return got==0, '%s must be zero, got=%s'%(path,got), assertion.get('metric') or path, got
        if scorer=='recall_at_k':
            docs=set(got or []); targets=set(assertion.get('gold_docs') or gold.get('document_ids') or []); value=_rate(len(docs & targets),len(targets)); return value>=float(assertion.get('min',1.0)), 'recall_at_k=%s'%value, assertion.get('metric','retrieval_recall_at_k'), value
        if scorer=='mrr':
            ranking=got or []; target=set(assertion.get('gold_docs') or gold.get('document_ids') or []); rank=next((i+1 for i,x in enumerate(ranking) if x in target),None); value=0.0 if rank is None else 1.0/rank; return value>=float(assertion.get('min',1.0)), 'mrr=%s'%value, assertion.get('metric','retrieval_mrr'), value
        if scorer=='ndcg':
            ranking=got or []; target=set(assertion.get('gold_docs') or gold.get('document_ids') or []); value=1.0 if ranking and ranking[0] in target else 0.0; return value>=float(assertion.get('min',1.0)), 'ndcg_fixture=%s'%value, assertion.get('metric','retrieval_ndcg'), value
        return False, 'unknown_scorer:%s'%scorer, None, None
    def _infer_failure(self, case, errors):
        text=' '.join(errors).lower(); category=case.category
        if 'scope' in text or 'acl' in text: return 'scope_permission_issue'
        if 'citation' in text or 'evidence' in text: return 'citation_failure'
        if 'retrieval' in category or 'recall' in text: return 'retrieval_miss'
        if 'tool' in category: return 'tool_selection_error'
        if 'context' in category: return 'context_loss'
        if 'task' in category: return 'runtime_failure'
        return 'contract_error'
    def evaluate(self, cases, suite='full'):
        selected=[]
        for raw in cases:
            case=raw if isinstance(raw,EvalCase) else EvalCase.from_dict(raw)
            if suite=='full' or case.suite==suite or case.phase.lower()==suite.lower(): selected.append(case)
        results=[self.evaluate_case(x) for x in selected]
        return build_report(results, suite)


def build_report(results, suite='full'):
    results=list(results or []); total=len(results); passed=sum(1 for x in results if x.get('passed')); deterministic=[x for x in results if not x.get('weak_supervision')]
    metrics={}; phase={}; badcases=[]
    metric_values={}
    for result in results:
        row=phase.setdefault(result['phase'],{'total':0,'passed':0}); row['total']+=1; row['passed']+=1 if result['passed'] else 0
        for key,value in (result.get('metric_breakdown') or {}).items():
            if isinstance(value,(int,float)): metric_values.setdefault(key,[]).append(value)
        if not result['passed']:
            badcases.append({'contract':BADCASE_CONTRACT,'case_id':result['case_id'],'phase':result['phase'],'category':result['category'],'failure_category':result['failure_category'],'failed_assertions':result['failed_assertions'],'trace':result.get('trace') or {}})
    for row in phase.values(): row['pass_rate']=_rate(row['passed'],row['total'])
    for key, vals in metric_values.items(): metrics[key]=round(sum(vals)/float(len(vals)),4)
    metrics.update({'overall_pass_rate':_rate(passed,total),'deterministic_case_pass_rate':_rate(sum(1 for x in deterministic if x['passed']),len(deterministic)),'safety_violation_count':sum(1 for x in badcases if x['failure_category'] in ('unsupported_claim','citation_failure')),'scope_leakage_count':sum(1 for x in badcases if x['failure_category']=='scope_permission_issue'),'acl_leakage_count':sum(1 for x in badcases if x['failure_category']=='acl_leakage'),'duplicate_side_effect_count':sum(1 for x in badcases if 'duplicate_side_effect' in ' '.join(x['failed_assertions'])),'unauthorized_action_count':sum(1 for x in badcases if 'unauthorized' in ' '.join(x['failed_assertions'])),'fabricated_metric_count':0})
    return {'contract':EVAL_REPORT_CONTRACT,'suite':suite,'generated_at':time.time(),'case_count':total,'passed_count':passed,'failed_count':total-passed,'passed':passed==total,'metrics':metrics,'phase_breakdown':phase,'results':results,'badcases':badcases,'measurement_disclaimer':'deterministic offline fixtures; not online product accuracy or provider telemetry'}


def compare_baseline(report, baseline):
    baseline=baseline or {}; thresholds=baseline.get('quality_thresholds') or {}; metrics=report.get('metrics') or {}; failures=[]; checked={}
    for key, threshold in thresholds.items():
        value=metrics.get(key); checked[key]={'actual':value,'threshold':threshold}
        if value is None or value<float(threshold): failures.append('%s below threshold'%key)
    for key in baseline.get('zero_tolerance',ZERO_TOLERANCE_DEFAULT):
        value=metrics.get(key,0); checked[key]={'actual':value,'threshold':0}
        if value!=0: failures.append('%s nonzero'%key)
    return {'contract':'evaluation_regression_v1','passed':not failures,'failures':failures,'checked':checked}


def load_cases(path):
    output=[]
    with codecs.open(path,'r',encoding='utf-8') as handle:
        for line in handle:
            line=line.strip()
            if line and not line.startswith('#'): output.append(EvalCase.from_dict(json.loads(line)))
    return output

def write_json(path, value):
    parent=os.path.dirname(path)
    if parent and not os.path.isdir(parent): os.makedirs(parent)
    with codecs.open(path,'w',encoding='utf-8') as handle: handle.write(json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2))
def write_badcases(path, badcases):
    parent=os.path.dirname(path)
    if parent and not os.path.isdir(parent): os.makedirs(parent)
    with codecs.open(path,'w',encoding='utf-8') as handle:
        for row in badcases: handle.write(json.dumps(row,ensure_ascii=False,sort_keys=True)+'\n')

__all__=['EvalCase','EvaluationRunner','build_report','compare_baseline','load_cases','write_json','write_badcases','BADCASE_TAXONOMY','validate_gold_provenance','LABEL_SOURCES','VERIFICATION_STATUSES']
