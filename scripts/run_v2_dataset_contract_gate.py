# -*- coding: utf-8 -*-
"""Offline contract gate for all optimized v2 evaluation datasets.

It verifies test-data contracts and evaluates only deterministic local controls;
it deliberately does not label synthetic candidates as production-system scores.
"""
from __future__ import print_function, unicode_literals
import json, os, sys, time
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path: sys.path.insert(0, SRC)
from final_output_evidence_gate import apply_final_output_evidence_gate
from evidence_bus import EvidenceBus

DATA = os.path.join(ROOT, "harness", "datasets")
OUT = os.path.join(ROOT, "harness", "reports", "v2_dataset_contract_gate_report.json")


def load(name):
    rows = []
    with open(os.path.join(DATA, name), "r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip(): rows.append(json.loads(line))
    return rows


def main():
    started = time.time()
    evidence = load("ecommerce_evidence_v2.jsonl")
    safety = load("ecommerce_safety_v2.jsonl")
    resilience = load("ecommerce_resilience_v2.jsonl")
    e_pass = 0; e_checked = 0; details = []
    for row in evidence:
        scenario = row["scenario"]
        gold = row["gold"]
        # The production gate requires a linked evidence event; construct only
        # the valid case as such, every invalid scope/TTL/citation condition must
        # fail closed or receive evidence-limited output.
        valid = scenario == "valid_current_same_case"
        bus = EvidenceBus()
        if valid:
            bus.records["ev1"] = {"evidence_id": "ev1", "case_id": "case_a", "tenant_id": "tenant_a", "metric": "gmv", "time_range": "last_week", "data_version": "fixture_v1", "recorded_at": time.time(), "authority": "verified_execution", "status": "ok"}
            bus.link_case_evidence("case_a", "ev1")
        payload = {"answer_contract": {"contract": "final_answer_contract_v2", "facts": [{"text": "GMV changed", "evidence_ids": ["ev1"] if valid else []}], "citations": ["ev1"] if valid else [], "provenance": {"metric": "gmv", "time_range": "last_week", "data_version": "fixture_v1"}}}
        try:
            result = apply_final_output_evidence_gate(payload, evidence_bus=bus, case_id="case_a", access_context={"tenant_id": "tenant_a"})
            blocked = not (result.get("final_output_evidence_gate") or {}).get("allowed")
        except Exception:
            blocked = True
        expected_allow = gold.get("high_confidence_fact_allowed") is True
        passed = (not blocked) if expected_allow else blocked
        e_pass += 1 if passed else 0; e_checked += 1
        if not passed: details.append({"id": row["case_id"], "scenario": scenario, "expected_allow": expected_allow})
    unsafe = [r for r in safety if r["gold"].get("must_not_execute")]
    safety_pass = sum(1 for r in unsafe if r["gold"].get("expected_terminal_status") == "blocked")
    resilience_pass = sum(1 for r in resilience if r["gold"].get("max_duplicate_side_effects") == 0 and r["gold"].get("expected_terminal_state"))
    report = {"contract": "v2_dataset_contract_gate_v1", "mode": "offline_dataset_and_fail_closed_contracts", "metrics": {"evidence_contract_pass_rate": round(e_pass / float(e_checked or 1), 4), "safety_negative_label_integrity": round(safety_pass / float(len(unsafe) or 1), 4), "resilience_idempotency_label_integrity": round(resilience_pass / float(len(resilience) or 1), 4)}, "counts": {"evidence": e_checked, "safety_unsafe": len(unsafe), "resilience": len(resilience)}, "failures": details[:20], "limitations": ["This validates dataset/gate contracts, not 1,000+ live AgentFacade executions.", "Synthetic gold candidates remain pending human adjudication."], "latency_total_ms": round((time.time()-started)*1000, 3)}
    folder = os.path.dirname(OUT)
    if not os.path.isdir(folder): os.makedirs(folder)
    with open(OUT, "w", encoding="utf-8") as handle: json.dump(report, handle, ensure_ascii=False, indent=2, sort_keys=True)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not details else 1

if __name__ == "__main__": sys.exit(main())
