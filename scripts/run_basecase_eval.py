# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import codecs
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.join(ROOT, 'src'))

from eval_baseline import EvalBaseline, evaluate_cases, evaluate_gate
from dag_routing import route_and_plan
from task_types import COMPARISON, DESCRIPTIVE

CASES_PATH = os.path.join(ROOT, 'evals', 'agent_scenario_test_cases.json')


def load_cases():
    with codecs.open(CASES_PATH, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return data['cases']


def pick_basecases(cases, limit=30):
    picked = []
    priority = ['A基础查询/单指标', 'A基础查询/维度拆分', 'B对比分析/时段对比', 'B对比分析/多指标合并']
    for cat in priority:
        for case in cases:
            if case.get('category') == cat:
                picked.append(case)
                if len(picked) >= limit:
                    return picked
    return picked


def _infer_task_type_from_category(category):
    if (category or '').startswith('B'):
        return COMPARISON
    return DESCRIPTIVE


def _adapt_expected(case):
    """Keep old eval JSON compatible while exposing the new benchmark schema.

    The production gate must not report route/task_type as null. Older generated
    cases store intent at top level and omit task_type, so this adapter promotes
    both into expected fields used by evaluate_cases.
    """
    data = dict(case)
    expected = dict(data.get('expected') or {})
    if data.get('intent') and 'intent' not in expected:
        expected['intent'] = data.get('intent')
    if 'task_type' not in expected:
        expected['task_type'] = _infer_task_type_from_category(data.get('category'))
    # Basecase CI is a deterministic routing/planning benchmark. It should not
    # require graph_agent or a live SQL execution path, so SQL presence is not
    # part of this gate.
    expected['requires_sql'] = False
    data['expected'] = expected
    return data


def _runner(query):
    return route_and_plan(query, use_llm=False)


def main():
    # DEPRECATION: 保留用于历史 basecase 兼容回归。新的跨能力功能请新增 case 到
    # harness/cases/ 并以 scripts/run_harness_gate.py 作为合入闸门。
    sys.stderr.write(
        '[deprecation] run_basecase_eval is legacy-compatible; '
        'prefer scripts/run_harness_gate.py for new work.\n')
    cases = load_cases()
    basecases = pick_basecases(cases, limit=30)


    eval_cases = [_adapt_expected(case) for case in basecases]
    run_result = evaluate_cases(eval_cases, _runner).to_dict()
    ci_baseline = EvalBaseline(
        name='basecase-ci',
        metrics={
            'pass_rate_min': 0.70,
            'contract_pass_rate_min': 0.95,
            'execution_success_rate_min': 0.60,
            'route_accuracy_min': 0.70,
            'task_type_accuracy_min': 0.70,
            'clarification_hit_rate_min': 0.70,
        },
        metadata={'scope': 'deterministic basecase smoke'},
    )
    gate = evaluate_gate(run_result, ci_baseline).to_dict()
    total = run_result['total']
    passed = run_result['passed']
    failed = run_result['failed']

    print('BASECASE_EVAL total=%d passed=%d failed=%d' % (total, passed, len(failed)))
    print('METRICS %s' % json.dumps(run_result['metrics'], ensure_ascii=False, sort_keys=True))
    print('GATE %s' % json.dumps(gate, ensure_ascii=False, sort_keys=True))
    for item in failed[:10]:
        print('--- FAIL %s | %s | %s' % (item['id'], item['category'], item['query']))
        for err in item['errors']:
            print('   - %s' % err)
        print('   result=%s' % item['result'])

    if failed:
        out = os.path.join(ROOT, 'evals', 'basecase_eval_failures.json')
        with codecs.open(out, 'w', encoding='utf-8') as f:
            f.write(json.dumps(failed, ensure_ascii=False, indent=2, default=str))
        print('Saved failures to %s' % out)

    if not gate['passed']:
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
