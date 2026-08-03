# -*- coding: utf-8 -*-
from __future__ import print_function, unicode_literals
import json, os, subprocess, sys, time
ROOT=os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))

def main():
    started=time.time()
    cmd=[sys.executable, os.path.join(ROOT,'scripts','run_evaluation_suite.py'), '--suite', 'full']
    proc=subprocess.Popen(cmd,cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
    out,_=proc.communicate()
    if not isinstance(out,str): out=out.decode('utf-8','replace')
    report={'contract':'production_control_plane_evaluation_gate_v1','gate':'production_control_plane_evaluation','passed':proc.returncode==0,'returncode':proc.returncode,'elapsed_ms':int((time.time()-started)*1000),'output_tail':out[-6000:]}
    print('PRODUCTION_CONTROL_PLANE_EVALUATION_GATE '+json.dumps(report,sort_keys=True,ensure_ascii=True))
    return 0 if report['passed'] else 1
if __name__=='__main__': sys.exit(main())
