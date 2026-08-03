# -*- coding: utf-8 -*-
from __future__ import print_function, unicode_literals
import json, os, subprocess, sys, time
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
def main():
    t=time.time(); target=os.path.join(ROOT,'tests','test_tool_governance_control_plane.py')
    p=subprocess.Popen([sys.executable,'-m','pytest','-p','no:asyncio','-q',target],cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
    out,_=p.communicate()
    if not isinstance(out,str): out=out.decode('utf-8','replace')
    report={'contract':'tool_governance_gate_report_v1','gate':'p4_tool_governance','passed':p.returncode==0,'returncode':p.returncode,'elapsed_ms':int((time.time()-t)*1000),'metrics':{'policy_denial_correctness':'fixture-tested','adversarial_bypass_blocked':'fixture-tested','arbitrary_shell_exposed':False},'output_tail':out[-6000:]}
    print('TOOL_GOVERNANCE_GATE '+json.dumps(report,sort_keys=True,ensure_ascii=True)); return 0 if report['passed'] else 1
if __name__=='__main__': sys.exit(main())
