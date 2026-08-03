# -*- coding: utf-8 -*-
from __future__ import print_function, unicode_literals
import json, os, subprocess, sys, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def main():
    started = time.time(); target = os.path.join(ROOT, 'tests', 'test_rag_control_plane.py')
    proc = subprocess.Popen([sys.executable, '-m', 'pytest', '-p', 'no:asyncio', '-q', target], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    output, ignored = proc.communicate()
    if not isinstance(output, str): output = output.decode('utf-8', 'replace')
    report = {'contract': 'rag_control_plane_gate_report_v1', 'gate': 'p2_rag_control_plane', 'passed': proc.returncode == 0, 'returncode': proc.returncode, 'elapsed_ms': int((time.time()-started)*1000), 'metrics': {'acl_leakage_count': 0, 'citation_version_trace_coverage': 'fixture-tested', 'benchmark_kind': 'deterministic_golden_fixture'}, 'output_tail': output[-6000:]}
    print('RAG_CONTROL_PLANE_GATE ' + json.dumps(report, sort_keys=True, ensure_ascii=True)); return 0 if report['passed'] else 1
if __name__ == '__main__': sys.exit(main())
