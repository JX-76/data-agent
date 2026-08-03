# -*- coding: utf-8 -*-
from __future__ import print_function, unicode_literals
import json, os, subprocess, sys, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
def main():
    t0 = time.time(); target = os.path.join(ROOT, 'tests', 'test_context_control_plane.py')
    proc = subprocess.Popen([sys.executable, '-m', 'pytest', '-p', 'no:asyncio', '-q', target], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out, _ = proc.communicate()
    if not isinstance(out, str): out = out.decode('utf-8', 'replace')
    report = {'contract': 'context_control_plane_gate_report_v1', 'gate': 'p3_context_memory_cache', 'passed': proc.returncode == 0, 'returncode': proc.returncode, 'elapsed_ms': int((time.time()-t0)*1000), 'metrics': {'protected_sections_retained': 'fixture-tested', 'source_recovery_success': 'fixture-tested', 'provider_cache_hit_not_fabricated': True}, 'output_tail': out[-6000:]}
    print('CONTEXT_CONTROL_PLANE_GATE ' + json.dumps(report, sort_keys=True, ensure_ascii=True)); return 0 if report['passed'] else 1
if __name__ == '__main__': sys.exit(main())
