# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import json, os, sys, tempfile
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__))); SRC=os.path.join(ROOT,'src')
if SRC not in sys.path: sys.path.insert(0,SRC)
from evaluation_control_plane import EvalCase, EvaluationRunner, compare_baseline, build_report, load_cases, write_badcases, validate_gold_provenance


def test_eval_case_schema_and_weak_supervision_not_blocking():
    case=EvalCase.from_dict({'case_id':'c1','phase':'P0','assertions':[{'scorer':'field_equals','path':'a','value':1}], 'metadata': {'weak_supervision': True}})
    assert case.validate()==[] and case.to_dict()['contract']=='eval_case_v1'
    bad=EvalCase.from_dict({'case_id':'c2','phase':'P0','assertions':[{'scorer':'field_equals','path':'a','value':1}], 'metadata': {'weak_supervision': True, 'blocking': True}})
    assert 'weak_supervision_cannot_be_blocking' in bad.validate()


def test_generic_scorers_and_badcase_taxonomy():
    runner=EvaluationRunner()
    ok=runner.evaluate_case({'case_id':'ok','phase':'P4','category':'tool_governance','observation':{'tool':{'allowed':False,'count':0}},'assertions':[{'scorer':'field_equals','path':'tool.allowed','value':False},{'scorer':'zero','path':'tool.count','metric':'unauthorized_action_count'}]})
    assert ok['passed'] is True and ok['metric_breakdown']['unauthorized_action_count']==0
    fail=runner.evaluate_case({'case_id':'fail','phase':'P4','category':'tool_governance','observation':{'tool':{'allowed':True}},'assertions':[{'scorer':'field_equals','path':'tool.allowed','value':False}]})
    assert fail['passed'] is False and fail['failure_category']=='tool_selection_error'


def test_rag_metrics_recall_mrr_ndcg_fixture():
    result=EvaluationRunner().evaluate_case({'case_id':'rag','phase':'P2','category':'retrieval_quality','observation':{'retrieval':{'documents':['gold','other']}},'gold':{'document_ids':['gold']},'assertions':[{'scorer':'recall_at_k','path':'retrieval.documents','min':1.0},{'scorer':'mrr','path':'retrieval.documents','min':1.0},{'scorer':'ndcg','path':'retrieval.documents','min':1.0}]})
    assert result['passed'] is True
    assert result['metric_breakdown']['retrieval_recall_at_k']==1.0
    assert result['metric_breakdown']['retrieval_mrr']==1.0
    assert result['metric_breakdown']['retrieval_ndcg']==1.0


def test_report_baseline_zero_tolerance_and_badcases():
    good=EvaluationRunner().evaluate_case({'case_id':'good','phase':'P0','category':'final_output_evidence','observation':{'x':1},'assertions':[{'scorer':'field_equals','path':'x','value':1,'metric':'evidence_lineage_coverage'}]})
    report=build_report([good])
    regression=compare_baseline(report, {'quality_thresholds': {'overall_pass_rate': 1.0, 'evidence_lineage_coverage': 1.0}, 'zero_tolerance': ['scope_leakage_count']})
    assert regression['passed'] is True
    bad=EvaluationRunner().evaluate_case({'case_id':'bad','phase':'SECURITY','category':'cross_phase_security','metadata': {'failure_category': 'scope_permission_issue'},'observation':{'scope':{'leakage':1}},'assertions':[{'scorer':'zero','path':'scope.leakage'}]})
    report2=build_report([bad])
    regression2=compare_baseline(report2, {'quality_thresholds': {'overall_pass_rate': 1.0}, 'zero_tolerance': ['scope_leakage_count']})
    assert regression2['passed'] is False and report2['metrics']['scope_leakage_count']==1


def test_load_full_case_corpus_and_write_badcases():
    path=os.path.join(ROOT,'evaluation','cases','production_control_plane_full.jsonl')
    cases=load_cases(path)
    assert len(cases) >= 25
    report=EvaluationRunner().evaluate(cases,'full')
    assert report['contract']=='evaluation_report_v1' and report['passed'] is True
    tmp=tempfile.NamedTemporaryFile(delete=False); tmp.close()
    write_badcases(tmp.name, report['badcases'])
    with open(tmp.name,'r') as handle: assert handle.read()==''
    os.unlink(tmp.name)


def test_gold_provenance_blocks_fake_human_labels():
    assert validate_gold_provenance({'label_source':'model_assisted','verification_status':'unverified','model_ref':'m','prompt_version':'p','weak_supervision':True,'blocking':False}) == []
    errors=validate_gold_provenance({'label_source':'model_assisted','verification_status':'human_verified','model_ref':'m','prompt_version':'p','blocking':True})
    assert 'model_assisted_cannot_be_blocking' in errors
    assert 'model_assisted_requires_source_promotion_before_human_verified' in errors
    assert 'human_verified_requires_annotator_ref' in validate_gold_provenance({'label_source':'human_verified','verification_status':'human_verified'})


def test_synthetic_business_candidates_are_nonblocking_weak_supervision():
    path=os.path.join(ROOT,'evaluation','gold_candidates','synthetic_business_cases.jsonl')
    cases=load_cases(path)
    assert len(cases) >= 5
    for case in cases:
        assert case.metadata.get('label_source') == 'model_assisted'
        assert case.metadata.get('weak_supervision') is True
        assert case.metadata.get('blocking') is False
        assert case.validate() == []
