# -*- coding: utf-8 -*-
from __future__ import print_function, unicode_literals
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
DATA = os.path.join(ROOT, "harness", "datasets")
REPORT = os.path.join(ROOT, "harness", "reports", "ecommerce_dataset_validation_report_v2.json")
MINIMUMS = {"ecommerce_e2e_v2.jsonl": 500, "ecommerce_sql_gold_v2.jsonl": 200, "ecommerce_rag_gold_v2.jsonl": 300, "ecommerce_safety_v2.jsonl": 300, "ecommerce_evidence_v2.jsonl": 200, "ecommerce_multiturn_v2.jsonl": 150, "ecommerce_resilience_v2.jsonl": 200, "ecommerce_tool_routing_v2.jsonl": 200}


def main():
    out = {"contract": "ecommerce_dataset_validation_report_v2", "passed": True, "datasets": {}, "failures": []}
    for name, minimum in sorted(MINIMUMS.items()):
        path = os.path.join(DATA, name)
        records, ids, errors = 0, set(), []
        if not os.path.exists(path):
            errors.append("missing_file")
        else:
            with open(path, "r", encoding="utf-8") as handle:
                for line_no, line in enumerate(handle, 1):
                    if not line.strip():
                        continue
                    records += 1
                    try:
                        item = json.loads(line)
                    except Exception as exc:
                        errors.append("invalid_json_line_%d" % line_no); continue
                    cid = item.get("case_id")
                    if not cid or cid in ids: errors.append("invalid_or_duplicate_case_id_line_%d" % line_no)
                    ids.add(cid)
                    lp = item.get("label_provenance") or {}
                    missing = [key for key in ("dataset_version", "gold_contract_version", "gold") if key not in item]
                    if missing: errors.append("missing_%s_line_%d" % ("_".join(missing), line_no))
                    if item.get("dataset_version") != "v2": errors.append("wrong_version_line_%d" % line_no)
                    if lp.get("review_status") != "pending_human_adjudication" or not lp.get("not_for_production_claim"):
                        errors.append("unsafe_label_provenance_line_%d" % line_no)
        if records < minimum: errors.append("below_minimum_%d_of_%d" % (records, minimum))
        out["datasets"][name] = {"record_count": records, "minimum": minimum, "error_count": len(errors)}
        if errors:
            out["passed"] = False; out["failures"].append({"dataset": name, "errors": errors[:20]})
    folder = os.path.dirname(REPORT)
    if not os.path.isdir(folder): os.makedirs(folder)
    with open(REPORT, "w", encoding="utf-8") as handle: json.dump(out, handle, ensure_ascii=False, indent=2, sort_keys=True)
    print(json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if out["passed"] else 1

if __name__ == "__main__": sys.exit(main())
