# -*- coding: utf-8 -*-
from __future__ import print_function, unicode_literals
import argparse, json, os, sys
ROOT=os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)); SRC=os.path.join(ROOT,'src')
if SRC not in sys.path: sys.path.insert(0,SRC)
from evaluation_control_plane import EvaluationRunner, compare_baseline, load_cases, write_badcases, write_json

def markdown(report, regression):
    lines=['# Production Control Plane Evaluation Report','', '* Measurement: %s' % report['measurement_disclaimer'],'', '## Summary','', '- Suite: `%s`' % report['suite'],'- Cases: %s; passed: %s; failed: %s' % (report['case_count'],report['passed_count'],report['failed_count']),'- Baseline passed: `%s`' % regression['passed'],'', '## Metrics','']
    for key in sorted(report['metrics']): lines.append('- `%s`: `%s`' % (key,report['metrics'][key]))
    lines.extend(['','## Phase Breakdown',''])
    for key in sorted(report['phase_breakdown']): lines.append('- `%s`: %s/%s (%s)' % (key,report['phase_breakdown'][key]['passed'],report['phase_breakdown'][key]['total'],report['phase_breakdown'][key]['pass_rate']))
    if report['badcases']:
        lines.extend(['','## Badcases',''])
        for row in report['badcases']: lines.append('- `%s`: %s' % (row['case_id'],row['failure_category']))
    return '\n'.join(lines)+'\n'

def main(argv=None):
    parser=argparse.ArgumentParser(); parser.add_argument('--suite',default='full'); parser.add_argument('--cases',default=os.path.join(ROOT,'evaluation','cases','production_control_plane_full.jsonl')); parser.add_argument('--baseline',default=os.path.join(ROOT,'evaluation','baselines','production_control_plane_baseline.json')); parser.add_argument('--report',default=os.path.join(ROOT,'evaluation','reports','latest_evaluation_report.json')); parser.add_argument('--badcases',default=os.path.join(ROOT,'evaluation','badcases','latest_badcases.jsonl'))
    args=parser.parse_args(argv); cases=load_cases(args.cases)
    with open(args.baseline,'r') as handle: baseline=json.load(handle)
    report=EvaluationRunner().evaluate(cases,args.suite); regression=compare_baseline(report,baseline); report['regression']=regression
    write_json(args.report,report); write_badcases(args.badcases,report['badcases'])
    md=args.report.rsplit('.',1)[0]+'.md'
    with open(md,'w') as handle: handle.write(markdown(report,regression))
    print('EVALUATION_REPORT '+json.dumps({'contract':report['contract'],'suite':args.suite,'passed':regression['passed'],'case_count':report['case_count'],'metrics':report['metrics'],'badcase_count':len(report['badcases']),'report':args.report},sort_keys=True))
    return 0 if regression['passed'] else 1
if __name__=='__main__': sys.exit(main())
