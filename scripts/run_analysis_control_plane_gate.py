# -*- coding: utf-8 -*-
from __future__ import print_function, unicode_literals
import json, os, subprocess, sys, time
ROOT=os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))

def main():
    started=time.time(); cmd=[sys.executable,'-m','pytest','-p','no:asyncio','-q','tests/test_analysis_control_plane.py']
    proc=subprocess.Popen(cmd,cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.STDOUT); out,_=proc.communicate()
    if not isinstance(out,str): out=out.decode('utf-8','replace')
    report={'contract':'analysis_control_plane_gate_v1','gate':'analysis_control_plane','passed':proc.returncode==0,'returncode':proc.returncode,'elapsed_ms':int((time.time()-started)*1000),'output_tail':out[-4000:],'coverage':['analysis_contract','analysis_patch','chart_spec','insight_claim_evidence_gate','user_dispute','stale_propagation']}
    print('ANALYSIS_CONTROL_PLANE_GATE '+json.dumps(report,sort_keys=True,ensure_ascii=True)); return 0 if report['passed'] else 1
if __name__=='__main__': sys.exit(main())
