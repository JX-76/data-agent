# -*- coding: utf-8 -*-
from __future__ import print_function, unicode_literals

import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
DATASETS = os.path.join(ROOT, "harness", "datasets")
REPORTS = os.path.join(ROOT, "harness", "reports")

REQUIRED_FILES = {
    "ecommerce_e2e_v1.jsonl": 250,
    "ecommerce_sql_gold_v1.jsonl": 100,
    "ecommerce_rag_gold_v1.jsonl": 100,
    "ecommerce_safety_v1.jsonl": 120,
    "ecommerce_resilience_v1.jsonl": 112,
    "ecommerce_evidence_v1.jsonl": 80,
    "ecommerce_multiturn_v1.jsonl": 60,
    "ecommerce_tool_routing_v1.jsonl": 120,
}


def read_jsonl(path):
    rows = []
    with open(path, "r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            if line.strip():
                obj = json.loads(line)
                obj["_line_no"] = line_no
                rows.append(obj)
    return rows


def ensure_dir(path):
    if not os.path.isdir(path):
        os.makedirs(path)


def validate_row(name, row):
    errors = []
    for field in ["case_id", "dataset_version", "gold_contract_version", "label_provenance", "gold"]:
        if field not in row:
            errors.append("missing_%s" % field)
    lp = row.get("label_provenance") or {}
    if lp.get("review_status") != "pending_human_adjudication":
        errors.append("label_review_status_must_be_pending")
    if not lp.get("not_for_production_claim"):
        errors.append("not_for_production_claim_must_be_true")
    if not row.get("case_id"):
        errors.append("empty_case_id")
    gold = row.get("gold") or {}
    if name.startswith("ecommerce_rag") and not ("relevant_chunk_ids" in gold and "expect_abstention" in gold):
        errors.append("rag_gold_missing_retrieval_fields")
    if name.startswith("ecommerce_sql") and not ("metrics" in gold and "terminal_status" in gold):
        errors.append("sql_gold_missing_semantic_fields")
    if name.startswith("ecommerce_e2e") and not ("expected_terminal_status" in gold or "task_type" in gold):
        errors.append("e2e_gold_missing_status_or_task")
    if name.startswith("ecommerce_safety") and "must_not_execute" not in gold:
        errors.append("safety_gold_missing_must_not_execute")
    if name.startswith("ecommerce_tool") and not ("gold_tools" in gold and "must_not_call" in gold):
        errors.append("tool_gold_missing_manifest_fields")
    return errors


def main():
    summary = {"contract": "ecommerce_dataset_validation_report_v1", "datasets": {}, "passed": True, "failures": []}
    for name, minimum in sorted(REQUIRED_FILES.items()):
        path = os.path.join(DATASETS, name)
        if not os.path.exists(path):
            summary["passed"] = False
            summary["failures"].append({"dataset": name, "error": "missing_file"})
            continue
        rows = read_jsonl(path)
        ids = set()
        dataset_errors = []
        for row in rows:
            cid = row.get("case_id")
            if cid in ids:
                dataset_errors.append({"line": row.get("_line_no"), "case_id": cid, "errors": ["duplicate_case_id"]})
            ids.add(cid)
            errs = validate_row(name, row)
            if errs:
                dataset_errors.append({"line": row.get("_line_no"), "case_id": cid, "errors": errs})
        if len(rows) < minimum:
            dataset_errors.append({"error": "record_count_below_minimum", "actual": len(rows), "minimum": minimum})
        if dataset_errors:
            summary["passed"] = False
            summary["failures"].append({"dataset": name, "errors": dataset_errors[:20]})
        summary["datasets"][name] = {"record_count": len(rows), "minimum_expected": minimum, "error_count": len(dataset_errors)}
    ensure_dir(REPORTS)
    out_path = os.path.join(REPORTS, "ecommerce_dataset_validation_report.json")
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2, sort_keys=True)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
