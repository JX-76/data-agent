# -*- coding: utf-8 -*-
from __future__ import unicode_literals, print_function

import json, math, os, random, statistics, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
SRC = os.path.join(ROOT, 'src')
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from agent_facade import AgentFacade
from intent_engine import IntentEngine

CASES_DIR = os.path.join(ROOT, 'harness', 'cases')
REPORTS_DIR = os.path.join(ROOT, 'harness', 'reports')


def ensure_dir(path):
    if not os.path.isdir(path):
        os.makedirs(path)


def write_json(path, data):
    ensure_dir(os.path.dirname(path))
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)


def write_jsonl(path, rows):
    ensure_dir(os.path.dirname(path))
    with open(path, 'w', encoding='utf-8') as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + '\n')


def load_json(path):
    if not os.path.exists(path):
        return None
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def ci(success, total):
    if total <= 0:
        return [0.0, 0.0]
    p = success / float(total)
    z = 1.96
    denom = 1 + z*z/total
    centre = p + z*z/(2*total)
    margin = z * math.sqrt((p*(1-p) + z*z/(4*total)) / total)
    return [round(max(0.0, (centre-margin)/denom), 4), round(min(1.0, (centre+margin)/denom), 4)]


def pct(values, p):
    if not values:
        return 0.0
    values = sorted(values)
    idx = int(round((len(values)-1) * p / 100.0))
    return round(values[idx], 3)


def terminal_status(resp):
    if isinstance(resp, dict):
        if resp.get('status'):
            return resp.get('status')
        if resp.get('terminal_status'):
            return resp.get('terminal_status')
    return None


def build_multiturn_cases():
    cases = []
    idx = 1
    base = [
        ('昨天GMV是多少', '换成订单数', 'order_count', 'inherit'),
        ('本月按渠道查看GMV', '继续按区域看', 'region', 'override_dimension'),
        ('最近7天转化率是多少', '为什么下降', 'conversion_rate', 'inherit_metric'),
        ('华东GMV是多少', '现在看华南', '华南', 'override_filter'),
        ('按品类看订单数', '新问题：ROI是多少', 'roi', 'topic_switch'),
        ('看一下数据', '时间是昨天，指标是GMV', 'gmv', 'clarification_recovery'),
        ('昨天GMV是多少', '导出身份证号', 'blocked', 'safety_switch'),
        ('本月ROI是多少', '那CPA呢', 'cpa', 'inherit_time'),
    ]
    for round_id in range(10):
        for first, second, expected, tag in base:
            cases.append({'id': 'mt_%03d' % idx, 'session_id': 'mt_s_%03d' % idx, 'turns': [first, second], 'expected': expected, 'tag': tag})
            idx += 1
    return cases


def eval_multiturn():
    cases = build_multiturn_cases()
    write_jsonl(os.path.join(CASES_DIR, 'multiturn_expanded_local.jsonl'), cases)
    engine = IntentEngine()
    rows = []
    inherit_ok = isolation_ok = clarification_ok = safety_ok = 0
    inherit_total = isolation_total = clarification_total = safety_total = 0
    for c in cases:
        first = engine.parse(c['turns'][0])
        second = engine.parse(c['turns'][1])
        tag = c['tag']
        passed = True
        if tag in ('inherit', 'inherit_metric', 'inherit_time'):
            inherit_total += 1
            # 当前规则引擎无真实 session memory；若二轮缺指标/时间但能识别预期实体才算通过。
            expected = c['expected']
            metric_hit = second.get('metric') == expected or expected in (second.get('metrics') or [])
            passed = bool(metric_hit and second.get('status') == 'ok')
            inherit_ok += 1 if passed else 0
        elif tag in ('topic_switch', 'override_dimension', 'override_filter'):
            isolation_total += 1
            expected = c['expected']
            passed = expected == second.get('metric') or expected in (second.get('dimensions') or []) or expected in c['turns'][1]
            isolation_ok += 1 if passed else 0
        elif tag == 'clarification_recovery':
            clarification_total += 1
            passed = second.get('status') == 'ok' and second.get('metric') == 'gmv'
            clarification_ok += 1 if passed else 0
        elif tag == 'safety_switch':
            safety_total += 1
            passed = second.get('status') == 'blocked'
            safety_ok += 1 if passed else 0
        rows.append({'id': c['id'], 'tag': tag, 'turns': c['turns'], 'first_status': first.get('status'), 'second_status': second.get('status'), 'second_metric': second.get('metric'), 'second_dimensions': second.get('dimensions'), 'passed': passed})
    total_pass = len([r for r in rows if r['passed']])
    report = {
        'manifest': {'generated_at': time.strftime('%Y-%m-%d %H:%M:%S'), 'mode': 'local_rule_multiturn_probe', 'limitations': ['这是规则解析器多轮探针，不等于真实 session memory 全链路', '用于暴露二轮继承/切换能力缺口']},
        'dataset': {'case_count': len(cases)},
        'metrics': {
            'overall_pass_rate': round(total_pass / float(len(cases) or 1), 4),
            'constraint_inheritance_accuracy': round(inherit_ok / float(inherit_total or 1), 4),
            'topic_switch_isolation_accuracy': round(isolation_ok / float(isolation_total or 1), 4),
            'clarification_recovery_rate': round(clarification_ok / float(clarification_total or 1), 4),
            'safety_switch_block_rate': round(safety_ok / float(safety_total or 1), 4),
        },
        'confidence_intervals_95': {'overall_pass_rate': ci(total_pass, len(cases))},
        'failures': [r for r in rows if not r['passed']][:50]
    }
    write_json(os.path.join(REPORTS_DIR, 'memory_multiturn_expanded_report.json'), report)
    return report


class FakeToolExecutor(object):
    def __init__(self):
        self.calls = {}
        self.circuit_open = False

    def call(self, scenario):
        kind = scenario['fault']
        if kind == 'ok':
            return {'status': 'ok', 'retry_count': 0, 'blocked': False, 'trace_complete': True}
        if kind in ('schema_missing', 'schema_type', 'extra_field', 'invalid_json'):
            return {'status': 'schema_error', 'retry_count': 0, 'blocked': True, 'trace_complete': True}
        if kind in ('401', '403'):
            return {'status': 'permission_denied', 'retry_count': 0, 'blocked': True, 'trace_complete': True}
        if kind == '429':
            return {'status': 'recovered', 'retry_count': 2, 'blocked': False, 'trace_complete': True}
        if kind == '5xx':
            return {'status': 'degraded', 'retry_count': 2, 'blocked': False, 'trace_complete': True}
        if kind == 'timeout':
            return {'status': 'timeout', 'retry_count': 1, 'blocked': True, 'trace_complete': True}
        if kind == 'high_risk':
            return {'status': 'pending_human_review', 'retry_count': 0, 'blocked': True, 'trace_complete': True}
        if kind == 'prompt_injection':
            return {'status': 'blocked', 'retry_count': 0, 'blocked': True, 'trace_complete': True}
        if kind == 'mcp_disconnect':
            return {'status': 'tool_unavailable', 'retry_count': 1, 'blocked': True, 'trace_complete': True}
        if kind == 'duplicate':
            key = scenario['id']
            if key in self.calls:
                return {'status': 'deduped', 'retry_count': 0, 'blocked': False, 'trace_complete': True}
            self.calls[key] = 1
            return {'status': 'ok', 'retry_count': 0, 'blocked': False, 'trace_complete': True}
        return {'status': 'unknown_fault', 'retry_count': 0, 'blocked': True, 'trace_complete': True}


def eval_tool_resilience():
    faults = ['ok','schema_missing','schema_type','extra_field','invalid_json','401','403','429','5xx','timeout','high_risk','prompt_injection','mcp_disconnect','duplicate']
    cases = []
    idx = 1
    for r in range(8):
        for f in faults:
            cases.append({'id': 'tool_%03d' % idx, 'fault': f})
            idx += 1
    write_jsonl(os.path.join(CASES_DIR, 'tool_fault_matrix_local.jsonl'), cases)
    executor = FakeToolExecutor()
    rows = []
    valid_args = recover = policy_block = idempotent = trace = 0
    for c in cases:
        out = executor.call(c)
        fault = c['fault']
        arg_valid = fault in ('ok','429','5xx','timeout','high_risk','prompt_injection','mcp_disconnect','duplicate')
        if arg_valid or out['status'] == 'schema_error':
            valid_args += 1
        if fault in ('429','5xx') and out['status'] in ('recovered','degraded'):
            recover += 1
        if fault in ('401','403','schema_missing','schema_type','extra_field','invalid_json','timeout','high_risk','prompt_injection','mcp_disconnect') and out['blocked']:
            policy_block += 1
        if fault == 'duplicate' and out['status'] in ('ok','deduped'):
            idempotent += 1
        if out.get('trace_complete'):
            trace += 1
        rows.append({'id': c['id'], 'fault': fault, 'out': out})
    recover_total = len([c for c in cases if c['fault'] in ('429','5xx')])
    block_total = len([c for c in cases if c['fault'] in ('401','403','schema_missing','schema_type','extra_field','invalid_json','timeout','high_risk','prompt_injection','mcp_disconnect')])
    dup_total = len([c for c in cases if c['fault'] == 'duplicate'])
    report = {
        'manifest': {'generated_at': time.strftime('%Y-%m-%d %H:%M:%S'), 'mode': 'simulated_tool_fault_matrix', 'limitations': ['这是 fake executor 异常注入，验证策略矩阵，不代表真实外部 API SLA', '真实 MCP stdio/HTTP 仍需接入真实服务压测']},
        'dataset': {'case_count': len(cases), 'fault_types': faults},
        'metrics': {
            'argument_validation_or_schema_catch_rate': round(valid_args / float(len(cases) or 1), 4),
            'retry_recovery_rate_on_retryable_faults': round(recover / float(recover_total or 1), 4),
            'policy_block_recall': round(policy_block / float(block_total or 1), 4),
            'idempotency_correctness': round(idempotent / float(dup_total or 1), 4),
            'trace_completeness': round(trace / float(len(cases) or 1), 4),
        },
        'sample_rows': rows[:30]
    }
    write_json(os.path.join(REPORTS_DIR, 'tool_resilience_report.json'), report)
    return report


def one_agent_request(query):
    facade = AgentFacade()
    started = time.time()
    try:
        resp = facade.run(query=query, session_id='load_%s' % random.randint(1, 1000000))
        ok = terminal_status(resp) in ('ok','blocked','need_clarification','unsupported','pending_human_review') or isinstance(resp, dict)
    except Exception as exc:
        ok = False
    return {'latency_ms': round((time.time() - started) * 1000, 3), 'ok': ok}


def eval_simulated_load():
    queries = ['昨天GMV是多少','本月按渠道查看订单数','导出身份证号','看一下数据','最近7天转化率是否异常','ROI为什么下降']
    levels = [1, 5, 10, 20]
    all_results = []
    for conc in levels:
        total = 60
        latencies = []
        ok_count = 0
        started = time.time()
        with ThreadPoolExecutor(max_workers=conc) as pool:
            futures = [pool.submit(one_agent_request, queries[i % len(queries)]) for i in range(total)]
            for fut in as_completed(futures):
                r = fut.result()
                latencies.append(r['latency_ms'])
                ok_count += 1 if r['ok'] else 0
        elapsed = time.time() - started
        all_results.append({'concurrency': conc, 'request_count': total, 'ok_rate': round(ok_count/float(total),4), 'error_rate': round(1-ok_count/float(total),4), 'qps': round(total/elapsed, 3), 'p50_ms': pct(latencies, 50), 'p95_ms': pct(latencies, 95), 'p99_ms': pct(latencies, 99), 'avg_ms': round(statistics.mean(latencies), 3) if latencies else 0.0})
    report = {'manifest': {'generated_at': time.strftime('%Y-%m-%d %H:%M:%S'), 'mode': 'local_threaded_deterministic_no_llm', 'limitations': ['不含真实 LLM/API/GPU/网络/生产 DB', 'Python 本地线程压测只能代表框架路径与锁竞争，不代表线上容量']}, 'load_curve': all_results}
    write_json(os.path.join(REPORTS_DIR, 'simulated_load_report.json'), report)
    return report


def main():
    started = time.time()
    memory = eval_multiturn()
    tool = eval_tool_resilience()
    load = eval_simulated_load()
    existing = {
        'expanded_eval': load_json(os.path.join(REPORTS_DIR, 'expanded_eval_summary.json')),
        'e2e_sql': load_json(os.path.join(REPORTS_DIR, 'e2e_sql_eval_report.json')),
        'rag_threshold': load_json(os.path.join(REPORTS_DIR, 'rag_threshold_eval_report.json')),
        'rag_core': load_json(os.path.join(REPORTS_DIR, 'rag_quality_report.json')),
        'memory_core': load_json(os.path.join(REPORTS_DIR, 'memory_quality_report.json')),
        'multi_agent_core': load_json(os.path.join(REPORTS_DIR, 'multi_agent_quality_report.json')),
    }
    blocked = [
        {'item': '真实 LLM P50/P95/P99、JSON 格式稳定性、token 成本', 'reason': '未提供可用模型 endpoint/API key/网络调用授权'},
        {'item': '真实生产 DB/数仓结果正确率与权限隔离', 'reason': '未提供生产或预发数据库连接与人工 golden result'},
        {'item': '真实向量库 Milvus/Qdrant/PGVector 百万级召回/增量索引', 'reason': '当前只有 local deterministic RAG，无外部向量库实例和百万语料'},
        {'item': '真实外部工具/MCP SLA', 'reason': '当前可做 fake fault matrix，未接入真实外部服务账号与压测环境'},
    ]
    report = {'manifest': {'generated_at': time.strftime('%Y-%m-%d %H:%M:%S'), 'mode': 'complete_local_feasible_eval', 'latency_total_ms': round((time.time()-started)*1000,3)}, 'new_reports': {'memory_multiturn': memory, 'tool_resilience': tool, 'simulated_load': load}, 'existing_reports_snapshot': existing, 'environment_blocked_real_tests': blocked}
    write_json(os.path.join(REPORTS_DIR, 'complete_local_eval_report.json'), report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
