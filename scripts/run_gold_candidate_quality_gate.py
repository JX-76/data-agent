# -*- coding: utf-8 -*-
from __future__ import print_function, unicode_literals
import json, os, sys
ROOT=os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)); SRC=os.path.join(ROOT,'src')
if SRC not in sys.path: sys.path.insert(0,SRC)
from evaluation_control_plane import load_cases

def main():
    path=os.path.join(ROOT,'evaluation','gold_candidates','synthetic_business_cases.jsonl'); cases=load_cases(path); failures=[]
    source_counts={}; pending=[]
    for case in cases:
        errors=case.validate(); meta=case.metadata; source=meta.get('label_source','contract_fixture'); source_counts[source]=source_counts.get(source,0)+1
        if errors: failures.append({'case_id':case.case_id,'errors':errors})
        if source=='model_assisted':
            pending.append(case.case_id)
            if meta.get('blocking') or not meta.get('weak_supervision'):
                failures.append({'case_id':case.case_id,'errors':['model_assisted_case_must_be_weak_supervision_nonblocking']})
    report={'contract':'gold_candidate_quality_gate_v1','passed':not failures,'candidate_count':len(cases),'source_counts':source_counts,'pending_human_review_count':len(pending),'blocking_human_verified_count':0,'failures':failures,'measurement_scope':'synthetic/model-assisted candidate labels only; not human-verified gold'}
    print('GOLD_CANDIDATE_QUALITY_GATE '+json.dumps(report,sort_keys=True,ensure_ascii=True)); return 0 if report['passed'] else 1
if __name__=='__main__': sys.exit(main())
