# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.join(ROOT, 'src'))

from agent_harness import AgentHarness, _sanitize
from benchmark_scorer import score_suite
from harness_snapshot import save_json

DEFAULT_CASES = os.path.join(ROOT, 'harness', 'cases', 'release_100.jsonl')
DEFAULT_REPORT = os.path.join(ROOT, 'harness', 'reports', 'release_100_quality_latest.json')

RELEASE_100_THRESHOLDS = {
    'case_count': int(os.environ.get('RELEASE_100_EXPECTED_CASES', '100')),
    'pass_rate': float(os.environ.get('RELEASE_100_MIN_PASS_RATE', '0.80')),
    'status_accuracy': float(os.environ.get('RELEASE_100_MIN_STATUS_ACCURACY', '0.90')),
    'trace_contract_validity': float(os.environ.get('RELEASE_100_MIN_TRACE_CONTRACT_VALIDITY', '1.0')),
    'multiturn_completion_rate': float(os.environ.get('RELEASE_100_MIN_MULTITURN_COMPLETION_RATE', '0.70')),
}

REQUIRED_CATEGORIES = [
    'single_metric', 'breakdown_ranking', 'trend_comparison', 'diagnosis',
    'follow_up', 'security', 'failure_path',
]


def _ensure_cases_exist(path):
    if os.path.exists(path):
        return
    scripts_dir = os.path.dirname(__file__)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    import generate_release_100_cases
    generate_release_100_cases.main()


def _category_names(cases):
    names = []
    seen = set()
    for case in cases or []:
        name = case.get('category') or 'uncategorized'
        if name not in seen:
            seen.add(name)
            names.append(name)
    return names


def validate_case_mix(cases):
    cases = cases or []
    categories = _category_names(cases)
    missing = [name for name in REQUIRED_CATEGORIES if name not in categories]
    status_counts = {}
    evidence_required = 0
    for case in cases:
        expected = case.get('expected') or {}
        status = expected.get('status')
        status_counts[status] = status_counts.get(status, 0) + 1
        gate = case.get('release_gate') or {}
        if gate.get('must_have_evidence'):
            evidence_required += 1
    errors = []
    if len(cases) != RELEASE_100_THRESHOLDS['case_count']:
        errors.append('case_count=%s expected=%s' % (len(cases), RELEASE_100_THRESHOLDS['case_count']))
    if missing:
        errors.append('missing_categories=%s' % ','.join(missing))
    for status in ('ok', 'need_clarification', 'blocked', 'no_answer'):
        if not status_counts.get(status):
            errors.append('missing_status=%s' % status)
    if not evidence_required:
        errors.append('missing_evidence_required_cases')
    return {
        'valid': not errors,
        'errors': errors,
        'categories': categories,
        'status_counts': status_counts,
        'evidence_required_cases': evidence_required,
    }


def _lowest_categories(report, limit=7):
    rows = []
    metrics = report.get('category_metrics') or {}
    for name, item in metrics.items():
        rows.append({
            'category': name,
            'total': item.get('total', 0),
            'failed': item.get('failed', 0),
            'pass_rate': item.get('pass_rate', 0.0),
            'failure_breakdown': item.get('failure_breakdown') or {},
        })
    rows.sort(key=lambda x: (x.get('pass_rate', 0.0), -x.get('failed', 0), x.get('category') or ''))
    return rows[:limit]


def _print_release_100(report):
    print('RELEASE_100_GATE total=%d passed=%d failed=%d score=%.4f pass_rate=%.4f' % (
        report.get('total', 0), report.get('passed', 0), report.get('failed', 0),
        report.get('total_score', 0.0), report.get('pass_rate', 0.0)))
    compact = dict(report)
    compact.pop('case_scores', None)
    print('RELEASE_100_METRICS %s' % json.dumps(_sanitize(compact), ensure_ascii=False, sort_keys=True, default=str))
    failures = [c for c in report.get('case_scores') or [] if not c.get('passed')]
    failure_rows = []
    for f in failures[:30]:
        failure_rows.append({
            'id': f.get('id'),
            'category': f.get('category'),
            'failure_type': f.get('semantic_failure_type'),
            'stage': f.get('failure_stage'),
            'trace_id': f.get('trace_id'),
            'task_id': f.get('task_id'),
        })
    if failure_rows:
        print('RELEASE_100_FAILURES %s' % json.dumps(_sanitize(failure_rows), ensure_ascii=False, sort_keys=True, default=str))
        print('REPLAY_HINT py -3 scripts/replay_harness_failure.py %s <case_id>' % DEFAULT_CASES)
    print('RELEASE_100_THRESHOLDS %s' % json.dumps(RELEASE_100_THRESHOLDS, sort_keys=True))


def main(argv=None):
    argv = argv or sys.argv[1:]
    case_path = argv[0] if argv else DEFAULT_CASES
    _ensure_cases_exist(case_path)

    harness = AgentHarness()
    cases = harness.load_cases(case_path)
    mix = validate_case_mix(cases)
    suite = os.path.splitext(os.path.basename(case_path))[0]
    harness_report = harness.run_suite(suite, cases)
    quality = score_suite(harness_report.get('results') or [])
    quality['suite'] = suite
    quality['case_file'] = case_path
    quality['case_mix'] = mix
    quality['harness_metrics'] = harness_report.get('metrics') or {}
    quality['lowest_categories'] = _lowest_categories(quality)
    save_json(DEFAULT_REPORT, quality)
    _print_release_100(quality)

    actuals = {
        'case_count': quality.get('total') or 0,
        'pass_rate': quality.get('pass_rate') or 0.0,
        'status_accuracy': quality.get('status_accuracy') or 0.0,
        'trace_contract_validity': quality.get('trace_contract_validity') if quality.get('trace_contract_validity') is not None else 1.0,
        'multiturn_completion_rate': quality.get('multiturn_completion_rate') if quality.get('multiturn_completion_rate') is not None else 0.0,
    }
    failed = []
    if not mix.get('valid'):
        failed.extend(mix.get('errors') or [])
    for name, threshold in RELEASE_100_THRESHOLDS.items():
        if actuals[name] < threshold:
            failed.append('%s=%.4f < %.4f' % (name, actuals[name], threshold))
    if failed:
        print('RELEASE_100_GATE_FAILED %s' % '; '.join(failed))
        return 1
    print('RELEASE_100_GATE_PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
