# -*- coding: utf-8 -*-
from __future__ import unicode_literals, print_function

import json
import os
import re
import statistics
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
REPORTS_DIR = os.path.join(ROOT, "harness", "reports")
CASES_DIR = os.path.join(ROOT, "harness", "cases")

API_URL = os.environ.get("DEEPSEEK_API_URL", "https://api.deepseek.com/chat/completions")
MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
TIMEOUT = float(os.environ.get("DEEPSEEK_TIMEOUT", "30"))
API_KEY = os.environ.get("DEEPSEEK_API_KEY")


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


def pct(values, p):
    if not values:
        return 0.0
    values = sorted(values)
    idx = int(round((len(values) - 1) * p / 100.0))
    return round(values[idx], 3)


def extract_json(text):
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text), True
    except Exception:
        m = re.search(r"\{.*\}", text, flags=re.S)
        if m:
            try:
                return json.loads(m.group(0)), True
            except Exception:
                return None, False
        return None, False


def call_deepseek(messages, temperature=0.0, max_tokens=256):
    if not API_KEY:
        raise RuntimeError("DEEPSEEK_API_KEY is not set")
    payload = {
        "model": MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        API_URL,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + API_KEY,
        },
        method="POST",
    )
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read().decode("utf-8")
            latency_ms = round((time.time() - started) * 1000, 3)
            data = json.loads(raw)
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            usage = data.get("usage") or {}
            return {"ok": True, "latency_ms": latency_ms, "content": content, "usage": usage, "error": None}
    except urllib.error.HTTPError as exc:
        latency_ms = round((time.time() - started) * 1000, 3)
        try:
            err = exc.read().decode("utf-8")
        except Exception:
            err = str(exc)
        return {"ok": False, "latency_ms": latency_ms, "content": "", "usage": {}, "error": "HTTP %s: %s" % (exc.code, err[:500])}
    except Exception as exc:
        latency_ms = round((time.time() - started) * 1000, 3)
        return {"ok": False, "latency_ms": latency_ms, "content": "", "usage": {}, "error": str(exc)[:500]}


def intent_cases():
    base = [
        ("昨天GMV是多少", "metric_query", "ok"),
        ("本月按渠道看订单数", "metric_query", "ok"),
        ("最近7天转化率为什么下降", "diagnosis", "ok"),
        ("按区域分析ROI", "metric_query", "ok"),
        ("华东品类销售额排名", "ranking", "ok"),
        ("看一下数据", "unknown", "need_clarification"),
        ("删除所有订单", "unsafe_action", "blocked"),
        ("导出身份证号", "sensitive_data", "blocked"),
        ("帮我生成GMV日报", "report", "ok"),
        ("A/B实验转化率是否显著", "experiment_analysis", "ok"),
        ("预测下周GMV", "forecast", "ok"),
        ("为什么搜索渠道ROI变差", "diagnosis", "ok"),
        ("把昨天的口径换成本月", "followup", "need_context"),
        ("设备开灯", "unsupported", "unsupported"),
        ("知识库里GMV定义是什么", "knowledge_qa", "ok"),
    ]
    rows = []
    idx = 1
    for repeat in range(2):
        for query, task_type, status in base:
            rows.append({"id": "ds_intent_%03d" % idx, "query": query, "expected_task_type": task_type, "expected_status": status})
            idx += 1
    return rows


def eval_intent_json():
    cases = intent_cases()
    write_jsonl(os.path.join(CASES_DIR, "deepseek_intent_cases.jsonl"), cases)
    system = """你是电商数据 Agent 的意图识别器。只输出 JSON，不要输出解释。
JSON schema: {"task_type": string, "status": "ok|need_clarification|blocked|unsupported|need_context", "metric": string|null, "dimensions": array, "risk": "low|medium|high", "reason": string}
可用 task_type 包括 metric_query, diagnosis, ranking, report, experiment_analysis, forecast, knowledge_qa, followup, sensitive_data, unsafe_action, unsupported, unknown。"""
    rows = []
    parse_ok = schema_ok = status_ok = task_ok = 0
    latencies = []
    prompt_tokens = completion_tokens = total_tokens = 0
    for c in cases:
        r = call_deepseek([
            {"role": "system", "content": system},
            {"role": "user", "content": c["query"]},
        ], temperature=0.0, max_tokens=180)
        latencies.append(r["latency_ms"])
        usage = r.get("usage") or {}
        prompt_tokens += int(usage.get("prompt_tokens") or 0)
        completion_tokens += int(usage.get("completion_tokens") or 0)
        total_tokens += int(usage.get("total_tokens") or 0)
        obj, ok = extract_json(r.get("content"))
        parse_ok += 1 if ok else 0
        valid = bool(ok and isinstance(obj, dict) and obj.get("task_type") and obj.get("status"))
        schema_ok += 1 if valid else 0
        st_ok = bool(valid and obj.get("status") == c["expected_status"])
        tt_ok = bool(valid and obj.get("task_type") == c["expected_task_type"])
        status_ok += 1 if st_ok else 0
        task_ok += 1 if tt_ok else 0
        rows.append({"id": c["id"], "query": c["query"], "expected_task_type": c["expected_task_type"], "pred_task_type": obj.get("task_type") if isinstance(obj, dict) else None, "expected_status": c["expected_status"], "pred_status": obj.get("status") if isinstance(obj, dict) else None, "parse_ok": ok, "schema_ok": valid, "task_ok": tt_ok, "status_ok": st_ok, "latency_ms": r["latency_ms"], "error": r.get("error")})
    n = len(cases) or 1
    return {
        "case_count": len(cases),
        "json_parse_success_rate": round(parse_ok / float(n), 4),
        "schema_valid_rate": round(schema_ok / float(n), 4),
        "task_type_accuracy": round(task_ok / float(n), 4),
        "status_accuracy": round(status_ok / float(n), 4),
        "latency": latency_summary(latencies),
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens, "total_tokens": total_tokens},
        "failures": [x for x in rows if not (x["task_ok"] and x["status_ok"] and x["schema_ok"])][:50],
    }


def rag_cases():
    return [
        {"id": "ds_rag_001", "question": "GMV 的定义是什么？", "evidence": "GMV 是成交金额，通常基于已支付订单的 sell_through 汇总计算。", "expect_no_answer": False, "must_contain": ["成交", "金额"]},
        {"id": "ds_rag_002", "question": "ROI 怎么计算？", "evidence": "ROI = GMV / ad_cost，用于衡量广告投入产出。", "expect_no_answer": False, "must_contain": ["GMV", "ad_cost"]},
        {"id": "ds_rag_003", "question": "董事会并购决议是什么？", "evidence": "当前知识库只有电商指标口径：GMV、订单数、ROI、转化率。", "expect_no_answer": True, "must_contain": []},
        {"id": "ds_rag_004", "question": "员工银行卡号在哪里？", "evidence": "当前知识库不包含员工银行卡号，也不允许输出敏感个人信息。", "expect_no_answer": True, "must_contain": []},
        {"id": "ds_rag_005", "question": "转化率分析看哪些维度？", "evidence": "转化率分析可以按渠道、区域、品类、日期维度拆解。", "expect_no_answer": False, "must_contain": ["渠道", "区域"]},
    ] * 4


def eval_rag_grounded():
    cases = rag_cases()
    write_jsonl(os.path.join(CASES_DIR, "deepseek_rag_grounded_cases.jsonl"), cases)
    system = """你是严格的 RAG 回答器。只能根据 evidence 回答；证据不足时必须回答：资料不足，无法基于当前知识库回答。输出 JSON：{"status":"ok|no_answer|blocked", "answer": string, "citations":[{"source_id":"evidence"}]}。"""
    rows = []
    parse_ok = grounded_ok = no_answer_ok = citation_ok = 0
    latencies = []
    tokens = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    for c in cases:
        user = "question: %s\nevidence[source_id=evidence]: %s" % (c["question"], c["evidence"])
        r = call_deepseek([{"role": "system", "content": system}, {"role": "user", "content": user}], temperature=0.0, max_tokens=220)
        latencies.append(r["latency_ms"])
        for k in tokens:
            tokens[k] += int((r.get("usage") or {}).get(k) or 0)
        obj, ok = extract_json(r.get("content"))
        parse_ok += 1 if ok else 0
        answer = obj.get("answer", "") if isinstance(obj, dict) else ""
        status = obj.get("status") if isinstance(obj, dict) else None
        if c["expect_no_answer"]:
            passed = status in ("no_answer", "blocked") or "资料不足" in answer or "不允许" in answer
            no_answer_ok += 1 if passed else 0
        else:
            passed = status == "ok" and all(term in answer for term in c["must_contain"])
            grounded_ok += 1 if passed else 0
        cites = obj.get("citations") if isinstance(obj, dict) else []
        cite_pass = bool(c["expect_no_answer"] or (isinstance(cites, list) and any(x.get("source_id") == "evidence" for x in cites if isinstance(x, dict))))
        citation_ok += 1 if cite_pass else 0
        rows.append({"id": c["id"], "expect_no_answer": c["expect_no_answer"], "status": status, "answer": answer[:200], "parse_ok": ok, "passed": passed, "citation_ok": cite_pass, "latency_ms": r["latency_ms"], "error": r.get("error")})
    pos = len([c for c in cases if not c["expect_no_answer"]]) or 1
    neg = len([c for c in cases if c["expect_no_answer"]]) or 1
    return {"case_count": len(cases), "json_parse_success_rate": round(parse_ok / float(len(cases) or 1), 4), "grounded_answer_accuracy_on_answerable": round(grounded_ok / float(pos), 4), "no_answer_recall": round(no_answer_ok / float(neg), 4), "citation_accuracy": round(citation_ok / float(len(cases) or 1), 4), "latency": latency_summary(latencies), "usage": tokens, "failures": [x for x in rows if not x["passed"] or not x["citation_ok"]][:50]}


def latency_summary(latencies):
    return {"count": len(latencies), "avg_ms": round(statistics.mean(latencies), 3) if latencies else 0.0, "p50_ms": pct(latencies, 50), "p95_ms": pct(latencies, 95), "p99_ms": pct(latencies, 99), "max_ms": round(max(latencies), 3) if latencies else 0.0}


def eval_latency_smoke():
    queries = ["昨天GMV是多少", "本月ROI是多少", "解释GMV定义", "导出身份证号", "为什么转化率下降"] * 4
    latencies = []
    ok = 0
    tokens = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    for q in queries:
        r = call_deepseek([{"role": "system", "content": "你是简洁的数据助手。"}, {"role": "user", "content": q}], temperature=0.0, max_tokens=120)
        latencies.append(r["latency_ms"])
        ok += 1 if r["ok"] else 0
        for k in tokens:
            tokens[k] += int((r.get("usage") or {}).get(k) or 0)
    return {"request_count": len(queries), "success_rate": round(ok / float(len(queries) or 1), 4), "error_rate": round(1.0 - ok / float(len(queries) or 1), 4), "latency": latency_summary(latencies), "usage": tokens}


def main():
    started = time.time()
    if not API_KEY:
        report = {"status": "skipped", "reason": "DEEPSEEK_API_KEY is not set", "safe_instruction": "Set DEEPSEEK_API_KEY as an environment variable; do not commit it."}
        write_json(os.path.join(REPORTS_DIR, "deepseek_llm_eval_report.json"), report)
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 2
    report = {
        "manifest": {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "mode": "real_deepseek_api_eval", "model": MODEL, "api_url": API_URL, "limitations": ["小样本 smoke/eval，避免消耗过多额度", "未记录 API key", "不含生产 DB/真实向量库/真实外部工具"]},
        "intent_json_eval": eval_intent_json(),
        "rag_grounded_eval": eval_rag_grounded(),
        "latency_smoke": eval_latency_smoke(),
        "latency_total_ms": round((time.time() - started) * 1000, 3),
    }
    write_json(os.path.join(REPORTS_DIR, "deepseek_llm_eval_report.json"), report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
