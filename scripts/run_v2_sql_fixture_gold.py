# -*- coding: utf-8 -*-
"""Measure executable SQL v2 candidates against the deterministic SQLite fixture."""
from __future__ import print_function, unicode_literals
import hashlib, json, os, sqlite3, sys, time
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.join(ROOT, "src"))
from intent_engine import IntentEngine
from metadata_catalog import build_metadata_catalog
from metric_sql_compiler import compile_metric_sql
# Reuse the fixture initializer, but this runner has its own v2 measurement report.
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from run_e2e_sql_eval import init_db

DATA = os.path.join(ROOT, "harness", "datasets", "ecommerce_sql_gold_v2.jsonl")
DB = os.path.join(ROOT, "fixtures", "expanded_eval", "ecommerce_sql_v2_fixture.sqlite")
OUT = os.path.join(ROOT, "harness", "reports", "ecommerce_sql_fixture_gold_v2_report.json")


def checksum(rows):
    raw = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def main():
    init_db(DB)
    rows = [json.loads(line) for line in open(DATA, "r", encoding="utf-8") if line.strip()]
    conn = sqlite3.connect(DB); conn.row_factory = sqlite3.Row
    engine, catalog = IntentEngine(), build_metadata_catalog()
    measured, executable, correct_negative, failures = [], 0, 0, []
    for item in rows:
        gold = item.get("gold", {}); expected = gold.get("terminal_status")
        parsed = engine.parse(item["query"])
        status = parsed.get("status")
        rec = {"case_id": item["case_id"], "expected_status": expected, "actual_intent_status": status}
        if expected != "ok":
            rec["passed"] = status in ("blocked", "need_clarification", "unsupported")
            correct_negative += 1 if rec["passed"] else 0
        else:
            plan = {"metric": parsed.get("metric"), "dimensions": parsed.get("dimensions") or [], "model": "order_detail", "time_range": ["2026-07-01", "2026-07-30"], "filters": []}
            compiled = compile_metric_sql(plan, catalog)
            if not compiled.ok or not compiled.sql:
                rec.update({"passed": False, "reason": "compile_failed"})
            else:
                try:
                    output = [dict(r) for r in conn.execute(compiled.sql).fetchall()]
                    rec.update({"passed": True, "sql_hash": hashlib.sha256(compiled.sql.encode("utf-8")).hexdigest()[:24], "result_checksum": checksum(output), "row_count": len(output), "columns": sorted(output[0].keys()) if output else []})
                    executable += 1
                except Exception as exc:
                    rec.update({"passed": False, "reason": "execution_failed", "safe_error": str(exc)[:160]})
        if not rec.get("passed"): failures.append(rec)
        measured.append(rec)
    conn.close()
    positives = len([x for x in rows if x.get("gold", {}).get("terminal_status") == "ok"])
    negatives = len(rows) - positives
    report = {"contract": "sql_fixture_result_gold_measurement_v2", "dataset_count": len(rows), "fixture_version": "expanded_eval_sqlite_v1", "metrics": {"fixture_executable_positive_rate": round(executable / float(positives or 1), 4), "negative_policy_intent_rate": round(correct_negative / float(negatives or 1), 4), "overall_case_pass_rate": round((len(rows)-len(failures))/float(len(rows) or 1), 4)}, "counts": {"positives": positives, "negatives": negatives, "fixture_verified_results": executable, "failed": len(failures)}, "failures": failures[:50], "samples": [x for x in measured if x.get("result_checksum")][:50], "limitations": ["This executes the current rule-based parser/compiler on a local SQLite fixture.", "Only successful executable rows obtain a result checksum; non-executable v2 candidates remain review candidates, not result-gold.", "No production warehouse or manual business-result adjudication is represented."], "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    with open(OUT, "w", encoding="utf-8") as handle: json.dump(report, handle, ensure_ascii=False, indent=2, sort_keys=True)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0
if __name__ == "__main__": sys.exit(main())
