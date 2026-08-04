# -*- coding: utf-8 -*-
from __future__ import unicode_literals, print_function

import json, math, os, sys, time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
SRC = os.path.join(ROOT, 'src')
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from rag_retriever import RagService
from rag_eval import RagQualityEvaluator

REPORTS_DIR = os.path.join(ROOT, 'harness', 'reports')
CASES_DIR = os.path.join(ROOT, 'harness', 'cases')


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


def ci(success, total):
    if total <= 0:
        return [0.0, 0.0]
    p = success / float(total)
    z = 1.96
    denom = 1 + z*z/total
    centre = p + z*z/(2*total)
    margin = z * math.sqrt((p*(1-p) + z*z/(4*total)) / total)
    return [round(max(0.0, (centre-margin)/denom), 4), round(min(1.0, (centre+margin)/denom), 4)]


def build_cases(service, target=80):
    evidence = service.retrieve('GMV 订单数 转化率 ROI 渠道 区域 品类', top_k=60).get('evidence') or []
    cases = []
    idx = 1
    # Auto-sampled RAG cases should not mark one arbitrary duplicate section as
    # the sole gold when several chunks share the same title/parent and any of
    # them would answer the question.  Build title/parent equivalence classes so
    # Recall@K measures semantic retrieval instead of synthetic id lottery.
    equiv = {}
    for e in evidence:
        cid = e.get('chunk_id') or e.get('id')
        if not cid:
            continue
        title = (e.get('title') or '').strip()
        parent = e.get('parent_id') or cid
        key = title.split('/')[0].strip() if title else parent
        equiv.setdefault(key, set()).add(cid)
        if parent:
            equiv[key].add(parent)
    seen_queries = set()
    for e in evidence:
        cid = e.get('chunk_id') or e.get('id')
        title = e.get('title') or ''
        content = e.get('supporting_extract') or e.get('content') or ''
        if not cid:
            continue
        key = title.split('/')[0].strip() if title else cid
        q = (key or content[:20]).strip() + ' 的定义是什么'
        if q in seen_queries:
            continue
        seen_queries.add(q)
        expected = sorted(equiv.get(key) or [cid])
        cases.append({'id': 'rag_thr_%03d' % idx, 'query': q, 'expected_chunk_ids': expected, 'expect_no_answer': False, 'tags': ['positive'], 'gold_semantics': 'title_or_parent_equivalence'})
        idx += 1
        if len(cases) >= target - 30:
            break
    neg = ['董事会并购决议是什么','员工银行卡号在哪里','火星仓库库存规则','未接入的CRM字段定义','CEO身份证号','删除知识库方法','内部融资协议','不存在的北极区域GMV口径','医疗诊断指南','天气预报接口文档']
    for q in neg * 4:
        if len(cases) >= target:
            break
        cases.append({'id': 'rag_thr_%03d' % idx, 'query': q, 'expected_chunk_ids': [], 'expect_no_answer': True, 'tags': ['negative_no_answer']})
        idx += 1
    return cases


def evaluate_threshold(service, cases, threshold):
    pos = [c for c in cases if c.get('expected_chunk_ids')]
    neg = [c for c in cases if c.get('expect_no_answer')]
    recall_hits = {1: 0, 3: 0, 5: 0, 10: 0}
    no_answer_correct = 0
    false_answer = 0
    false_abstain = 0
    citation_ok = 0
    rows = []
    for c in cases:
        r = service.retrieve(c['query'], top_k=10, min_confidence=threshold)
        got = [e.get('chunk_id') for e in r.get('evidence') or []]
        exp = set(c.get('expected_chunk_ids') or [])
        if exp:
            # Retrieval recall must measure whether the retriever returned the
            # expected document, not whether a stricter answer-generation gate
            # chose "ok".  The latter is reported separately as abstention and
            # otherwise depresses Recall@K for a correct candidate list.
            for k in recall_hits:
                if exp.intersection(set(got[:k])):
                    recall_hits[k] += 1
            if r.get('status') == 'no_answer':
                false_abstain += 1
        else:
            if r.get('status') == 'no_answer':
                no_answer_correct += 1
            else:
                false_answer += 1
        citations = r.get('citations') or []
        if citations and all(x.get('chunk_id') in set(got) for x in citations):
            citation_ok += 1
        elif not citations and not got:
            citation_ok += 1
        rows.append({'id': c['id'], 'query': c['query'], 'expect_no_answer': bool(c.get('expect_no_answer')), 'status': r.get('status'), 'confidence': r.get('confidence'), 'top_chunk': got[0] if got else None, 'retrieval_hit_at_5': bool(exp.intersection(set(got[:5])) if exp else not got)})
    pos_n = len(pos) or 1
    neg_n = len(neg) or 1
    return {
        'threshold': threshold,
        'recall@1': round(recall_hits[1] / float(pos_n), 4),
        'recall@3': round(recall_hits[3] / float(pos_n), 4),
        'recall@5': round(recall_hits[5] / float(pos_n), 4),
        'recall@10': round(recall_hits[10] / float(pos_n), 4),
        'no_answer_recall': round(no_answer_correct / float(neg_n), 4),
        'false_answer_rate': round(false_answer / float(neg_n), 4),
        'false_abstain_rate_on_positive': round(false_abstain / float(pos_n), 4),
        'citation_accuracy': round(citation_ok / float(len(cases) or 1), 4),
        'sample_rows': rows[:20]
    }


def main():
    started = time.time()
    service = RagService.local(reranker_provider='lexical')
    cases = build_cases(service, target=80)
    write_jsonl(os.path.join(CASES_DIR, 'rag_threshold_eval.jsonl'), cases)
    thresholds = [0.0, 0.05, 0.1, 0.2, 0.35, 0.5, 0.7, 0.9]
    curve = [evaluate_threshold(service, cases, t) for t in thresholds]
    best = sorted(curve, key=lambda x: (x['no_answer_recall'] >= 0.9, x['recall@5'], -x['false_answer_rate']), reverse=True)[0]
    report = {
        'manifest': {'generated_at': time.strftime('%Y-%m-%d %H:%M:%S'), 'mode': 'local_deterministic_rag_threshold_sweep', 'metric_semantics': {'recall_at_k': 'candidate retrieval hit independent of answerability/status gate', 'false_abstain_rate_on_positive': 'separate answerability-gate abstention measure'}, 'limitations': ['local deterministic embedding，不代表生产 embedding/向量库', 'case 来自当前知识库自动抽样 + 手写无答案负例', 'threshold 只用于诊断拒答曲线，未改生产默认参数']},
        'dataset': {'case_count': len(cases), 'positive_count': len([c for c in cases if c.get('expected_chunk_ids')]), 'negative_or_boundary_count': len([c for c in cases if c.get('expect_no_answer')])},
        'threshold_curve': curve,
        'selected_threshold_by_rule': best,
        'confidence_intervals_95': {'selected_recall@5': ci(int(round(best['recall@5'] * len([c for c in cases if c.get('expected_chunk_ids')]))), len([c for c in cases if c.get('expected_chunk_ids')])), 'selected_no_answer_recall': ci(int(round(best['no_answer_recall'] * len([c for c in cases if c.get('expect_no_answer')]))), len([c for c in cases if c.get('expect_no_answer')]))},
        'latency_total_ms': round((time.time() - started) * 1000, 3)
    }
    write_json(os.path.join(REPORTS_DIR, 'rag_threshold_eval_report.json'), report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
