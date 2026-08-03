# -*- coding: utf-8 -*-
"""Run the 55 ecommerce Agent clinical cases and emit a hotspot report."""
from __future__ import unicode_literals

import codecs
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.join(ROOT, 'src'))

from agent_harness import AgentHarness, _sanitize
from ecommerce_diagnosis_harness import EcommerceDiagnosisEvaluator, flatten_result_for_report

CASE_PATH = os.path.join(ROOT, 'harness', 'cases', 'ecommerce_agent_diagnosis_55.jsonl')
REPORT_PATH = os.path.join(ROOT, 'harness', 'reports', 'ecommerce_agent_diagnosis_55_latest.json')


def main(argv=None):
    argv = argv or sys.argv[1:]
    case_path = argv[0] if argv else CASE_PATH
    harness = AgentHarness()
    evaluator = EcommerceDiagnosisEvaluator()
    cases = harness.load_cases(case_path)
    evaluated = []
    for case in cases:
        item = harness.run_case(case)
        clinical = evaluator.evaluate(case, item.get('result') or {}, item.get('trace') or [])
        clinical['result'] = flatten_result_for_report(item.get('result') or {}, item.get('trace') or [])
        clinical['duration_ms'] = item.get('duration_ms')
        clinical['trace_summary'] = item.get('trace_summary') or {}
        evaluated.append(clinical)
    report = {'suite': 'ecommerce_agent_diagnosis_55',
              'case_path': case_path,
              'metrics': evaluator.summarize(evaluated),
              'results': evaluated,
              'failures': [x for x in evaluated if not x.get('passed')]}
    with codecs.open(REPORT_PATH, 'w', encoding='utf-8') as f:
        f.write(json.dumps(_sanitize(report), ensure_ascii=False, indent=2, sort_keys=True))
    m = report['metrics']
    print('ECOMMERCE_DIAGNOSIS total=%d passed=%d failed=%d pass_rate=%.4f' %
          (m['total'], m['passed'], m['failed'], m['pass_rate']))
    print('HOTSPOTS %s' % json.dumps(m['architecture_hotspots'], ensure_ascii=False, sort_keys=True))
    print('REPORT %s' % REPORT_PATH)
    return 0 if not m['failed'] else 1


if __name__ == '__main__':
    sys.exit(main())
