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

DEFAULT_CASES = os.path.join(ROOT, 'harness', 'cases', 'benchmark_r20.jsonl')
DEFAULT_REPORT = os.path.join(ROOT, 'harness', 'reports', 'benchmark_r20_quality_latest.json')
DEFAULT_BASELINE = os.path.join(ROOT, 'harness', 'reports', 'quality_baseline.json')

# Product-quality thresholds, intentionally stricter than the former
# permissive smoke thresholds. Environment overrides support staged rollout
# without changing how the complete benchmark is scored.
QUALITY_THRESHOLDS = {
    'pass_rate': float(os.environ.get('AGENT_QUALITY_MIN_PASS_RATE', '0.80')),
    'status_accuracy': float(os.environ.get('AGENT_QUALITY_MIN_STATUS_ACCURACY', '0.90')),
    'task_type_accuracy': float(os.environ.get('AGENT_QUALITY_MIN_TASK_TYPE_ACCURACY', '0.85')),
    # P2 release-readiness: every evaluated facade case must have a replayable
    # trace envelope. Missing stage/error correlation blocks the quality gate.
    'trace_contract_validity': float(os.environ.get('AGENT_QUALITY_MIN_TRACE_CONTRACT_VALIDITY', '1.0')),
}


def _load_baseline(path=DEFAULT_BASELINE):
    if not path or not os.path.exists(path):
        return {}
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def _baseline_diff(report, baseline=None):
    baseline = baseline or {}
    base_quality = baseline.get('agent_quality_gate') or {}
    fields = ['pass_rate', 'status_accuracy', 'task_type_accuracy']
    diff = {}
    for field in fields:
        current = report.get(field)
        base = base_quality.get(field)
        if current is None or base is None:
            continue
        diff[field] = {
            'baseline': base,
            'current': current,
            'delta': round(current - base, 4),
        }
    return diff


def _lowest_categories(report, limit=5):
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


def _print_quality(report):
    print('AGENT_QUALITY_GATE total=%d passed=%d failed=%d score=%.4f pass_rate=%.4f' % (
        report.get('total', 0), report.get('passed', 0), report.get('failed', 0),
        report.get('total_score', 0.0), report.get('pass_rate', 0.0)))
    compact = dict(report)
    compact.pop('case_scores', None)
    print('QUALITY_METRICS %s' % json.dumps(_sanitize(compact), ensure_ascii=False, sort_keys=True, default=str))
    failures = [c for c in report.get('case_scores') or [] if not c.get('passed')]
    failure_rows = []
    for f in failures[:20]:
        failure_rows.append({
            'id': f.get('id'),
            'failure_type': f.get('semantic_failure_type'),
            'raw_failure_type': f.get('raw_failure_type'),
            'stage': f.get('failure_stage'),
            'trace_id': f.get('trace_id'),
            'task_id': f.get('task_id'),
            'session_id': f.get('session_id'),
        })
    print('QUALITY_FAILURES %s' % json.dumps(_sanitize(failure_rows), ensure_ascii=False, sort_keys=True, default=str))
    print('QUALITY_THRESHOLDS %s' % json.dumps(QUALITY_THRESHOLDS, sort_keys=True))
    if report.get('baseline_diff'):
        print('QUALITY_BASELINE_DIFF %s' % json.dumps(_sanitize(report.get('baseline_diff')), ensure_ascii=False, sort_keys=True, default=str))
    if report.get('lowest_categories'):
        print('QUALITY_LOWEST_CATEGORIES %s' % json.dumps(_sanitize(report.get('lowest_categories')), ensure_ascii=False, sort_keys=True, default=str))
    if report.get('failure_stage_breakdown'):
        print('QUALITY_FAILURE_STAGES %s' % json.dumps(_sanitize(report.get('failure_stage_breakdown')), ensure_ascii=False, sort_keys=True, default=str))
    if failure_rows:
        print('REPLAY_HINT py -3 scripts/replay_harness_failure.py benchmark_r20 <case_id>')


def main(argv=None):
    argv = argv or sys.argv[1:]
    case_path = argv[0] if argv else DEFAULT_CASES
    harness = AgentHarness()
    cases = harness.load_cases(case_path)
    suite = os.path.splitext(os.path.basename(case_path))[0]
    harness_report = harness.run_suite(suite, cases)
    quality = score_suite(harness_report.get('results') or [])
    quality['suite'] = suite
    quality['case_file'] = case_path
    quality['harness_metrics'] = harness_report.get('metrics') or {}
    baseline = _load_baseline()
    quality['baseline_diff'] = _baseline_diff(quality, baseline)
    quality['lowest_categories'] = _lowest_categories(quality)
    quality['observability'] = {
        'failure_stage_breakdown': quality.get('failure_stage_breakdown') or {},
        'correlation_fields': ['trace_id', 'task_id', 'session_id'],
        'case_score_debug_fields': ['trace_events', 'trace_validation_errors', 'duration_ms', 'raw_failure_type'],
    }
    save_json(DEFAULT_REPORT, quality)
    _print_quality(quality)

    pass_rate = quality.get('pass_rate') or 0.0
    status_acc = quality.get('status_accuracy') or 0.0
    task_type_acc = quality.get('task_type_accuracy')
    if task_type_acc is None:
        task_type_acc = 1.0
    actuals = {
        'pass_rate': pass_rate,
        'status_accuracy': status_acc,
        'task_type_accuracy': task_type_acc,
        'trace_contract_validity': quality.get('trace_contract_validity') if quality.get('trace_contract_validity') is not None else 1.0,
    }
    failed = []
    for name, threshold in QUALITY_THRESHOLDS.items():
        if actuals[name] < threshold:
            failed.append('%s=%.4f < %.4f' % (name, actuals[name], threshold))
    if failed:
        print('QUALITY_GATE_FAILED %s' % '; '.join(failed))
        return 1
    print('QUALITY_GATE_PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
