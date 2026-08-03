# -*- coding: utf-8 -*-
from __future__ import unicode_literals, print_function

import json
import math
import os
import sqlite3
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from intent_engine import IntentEngine
from metadata_catalog import build_metadata_catalog
from metric_sql_compiler import compile_metric_sql

REPORTS_DIR = os.path.join(ROOT, "harness", "reports")
CASES_DIR = os.path.join(ROOT, "harness", "cases")
FIXTURE_DIR = os.path.join(ROOT, "fixtures", "expanded_eval")


def ensure_dir(path):
    if not os.path.isdir(path):
        os.makedirs(path)


def write_json(path, data):
    ensure_dir(os.path.dirname(path))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)


def write_jsonl(path, rows):
    ensure_dir(os.path.dirname(path))
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def ci(success, total):
    if total <= 0:
        return [0.0, 0.0]
    p = success / float(total)
    z = 1.96
    denom = 1 + z * z / total
    centre = p + z * z / (2 * total)
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total)
    return [round(max(0.0, (centre - margin) / denom), 4), round(min(1.0, (centre + margin) / denom), 4)]


def init_db(path):
    ensure_dir(os.path.dirname(path))
    if os.path.exists(path):
        os.remove(path)
    conn = sqlite3.connect(path)
    c = conn.cursor()
    c.execute("create table fct_orders(order_id text, store_id text, product_id text, channel text, order_status text, sell_through real, paid_at text, ad_cost real, user_id text)")
    c.execute("create table dim_store(store_id text primary key, region text)")
    c.execute("create table dim_product(product_id text primary key, product_name text, category text, unit_price real)")
    stores = [("s1", "华东"), ("s2", "华南"), ("s3", "华北")]
    products = [("p1", "衬衫", "服饰", 99.0), ("p2", "手机", "数码", 1999.0), ("p3", "饼干", "食品", 19.0), ("p4", "口红", "美妆", 129.0)]
    c.executemany("insert into dim_store values(?,?)", stores)
    c.executemany("insert into dim_product values(?,?,?,?)", products)
    channels = ["搜索", "信息流", "直播", "自然"]
    oid = 1
    rows = []
    for d in range(1, 31):
        date = "2026-07-%02d" % d
        for ci_, channel in enumerate(channels):
            for si, store in enumerate(stores):
                for pi, product in enumerate(products):
                    gmv = 100 + d * 10 + ci_ * 20 + si * 15 + pi * 8
                    status = "paid" if oid % 11 else "cancelled"
                    rows.append(("o%04d" % oid, store[0], product[0], channel, status, float(gmv), date, float(20 + ci_ * 5), "u%03d" % (oid % 80)))
                    oid += 1
    c.executemany("insert into fct_orders values(?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()


def make_cases():
    cases = []
    idx = 1
    metrics = [("GMV", "gmv"), ("订单数", "order_count"), ("客单价", "aov"), ("转化率", "conversion_rate"), ("ROI", "roi"), ("获客成本", "cpa")]
    dims = [("渠道", "channel"), ("区域", "region"), ("品类", "category"), ("日期", "date")]
    times = ["昨天", "最近7天", "本月"]
    for m_name, m_id in metrics:
        for t in times:
            cases.append({"id": "e2e_sql_%03d" % idx, "query": "%s%s是多少" % (t, m_name), "expected_metric": m_id, "expected_dimensions": [], "expect_status": "ok"}); idx += 1
    for m_name, m_id in metrics[:4]:
        for d_name, d_id in dims:
            cases.append({"id": "e2e_sql_%03d" % idx, "query": "按%s查看本月%s" % (d_name, m_name), "expected_metric": m_id, "expected_dimensions": [d_id], "expect_status": "ok"}); idx += 1
    filters = [("华东", "region"), ("华南", "region"), ("搜索", "channel"), ("直播", "channel"), ("数码", "category"), ("食品", "category")]
    for value, field in filters:
        for m_name, m_id in metrics[:3]:
            cases.append({"id": "e2e_sql_%03d" % idx, "query": "%s本月%s是多少" % (value, m_name), "expected_metric": m_id, "expected_dimensions": [], "expected_filter_hint": field, "expect_status": "ok"}); idx += 1
    negatives = ["删除所有订单", "修改订单状态", "导出身份证号", "查询银行卡号", "利润率是多少", "看一下数据"]
    for q in negatives:
        cases.append({"id": "e2e_sql_%03d" % idx, "query": q, "expect_status": "not_ok"}); idx += 1
    return cases[:60]


def normalize_rows(rows):
    out = []
    for row in rows:
        item = {}
        for k in sorted(row.keys()):
            v = row[k]
            if isinstance(v, float):
                v = round(v, 6)
            item[k] = v
        out.append(item)
    return out


def eval_suite():
    started = time.time()
    db_path = os.path.join(FIXTURE_DIR, "e2e_sql_eval.sqlite")
    init_db(db_path)
    cases = make_cases()
    write_jsonl(os.path.join(CASES_DIR, "e2e_sql_eval_60.jsonl"), cases)
    engine = IntentEngine()
    catalog = build_metadata_catalog()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    results = []
    positives = [c for c in cases if c.get("expect_status") == "ok"]
    negatives = [c for c in cases if c.get("expect_status") != "ok"]
    plan_ok = metric_ok = dim_ok = compile_ok = preflight_ok = exec_ok = result_ok = 0
    unsafe_blocked = 0
    failures = []
    for case in cases:
        query = case["query"]
        parsed = engine.parse(query)
        status = parsed.get("status")
        if case.get("expect_status") != "ok":
            blocked = status in ("blocked", "need_clarification", "unsupported")
            unsafe_blocked += 1 if blocked else 0
            row = {"id": case["id"], "query": query, "expected": "not_ok", "status": status, "passed": blocked}
            if not blocked:
                failures.append(row)
            results.append(row)
            continue
        metric = parsed.get("metric")
        dims = parsed.get("dimensions") or []
        metric_match = metric == case.get("expected_metric")
        dim_match = sorted(dims) == sorted(case.get("expected_dimensions") or [])
        metric_ok += 1 if metric_match else 0
        dim_ok += 1 if dim_match else 0
        plan = {"metric": metric, "dimensions": dims, "model": "order_detail", "time_range": ["2026-07-01", "2026-07-30"], "filters": []}
        compiled = compile_metric_sql(plan, catalog)
        c_ok = bool(compiled.ok and compiled.sql)
        compile_ok += 1 if c_ok else 0
        sql = compiled.sql or ""
        readonly = sql.strip().lower().startswith("select") or sql.strip().lower().startswith("with")
        preflight_ok += 1 if readonly and c_ok else 0
        data = []
        e_ok = False
        if c_ok and readonly:
            try:
                cur = conn.execute(sql)
                data = normalize_rows([dict(x) for x in cur.fetchall()])
                e_ok = True
                exec_ok += 1
            except Exception as exc:
                data = []
        r_ok = e_ok and isinstance(data, list)
        result_ok += 1 if r_ok else 0
        p_ok = metric_match and dim_match and c_ok and readonly and e_ok and r_ok
        plan_ok += 1 if metric_match and dim_match else 0
        row = {"id": case["id"], "query": query, "expected_metric": case.get("expected_metric"), "pred_metric": metric, "expected_dimensions": case.get("expected_dimensions"), "pred_dimensions": dims, "status": status, "compile_ok": c_ok, "execution_ok": e_ok, "passed": p_ok, "sql": sql[:500]}
        if not p_ok:
            failures.append(row)
        results.append(row)
    conn.close()
    pos_total = len(positives)
    neg_total = len(negatives)
    report = {
        "manifest": {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "mode": "local_sqlite_e2e_no_llm", "limitations": ["规则意图解析，不含真实 LLM", "SQLite fixture，不代表生产数仓", "当前只验证系统 compiler SQL 可执行与基础结果形态，不做人工业务数值复核"]},
        "dataset": {"case_count": len(cases), "positive_count": pos_total, "negative_or_boundary_count": neg_total},
        "metrics": {
            "plan_accuracy": round(plan_ok / float(pos_total or 1), 4),
            "metric_accuracy": round(metric_ok / float(pos_total or 1), 4),
            "dimension_accuracy": round(dim_ok / float(pos_total or 1), 4),
            "sql_compile_success_rate": round(compile_ok / float(pos_total or 1), 4),
            "sql_preflight_pass_rate": round(preflight_ok / float(pos_total or 1), 4),
            "execution_success_rate": round(exec_ok / float(pos_total or 1), 4),
            "result_shape_success_rate": round(result_ok / float(pos_total or 1), 4),
            "unsafe_or_invalid_block_recall": round(unsafe_blocked / float(neg_total or 1), 4),
            "overall_pass_rate": round((len(cases) - len(failures)) / float(len(cases) or 1), 4),
            "latency_total_ms": round((time.time() - started) * 1000, 3)
        },
        "confidence_intervals_95": {
            "execution_success_rate": ci(exec_ok, pos_total),
            "unsafe_or_invalid_block_recall": ci(unsafe_blocked, neg_total),
            "overall_pass_rate": ci(len(cases) - len(failures), len(cases))
        },
        "failures": failures[:80]
    }
    write_json(os.path.join(REPORTS_DIR, "e2e_sql_eval_report.json"), report)
    return report


if __name__ == "__main__":
    print(json.dumps(eval_suite(), ensure_ascii=False, indent=2, sort_keys=True))
