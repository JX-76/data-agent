# -*- coding: utf-8 -*-
"""Release v1 quality report.

Drives the full release_v1 case pack through ask_release, evaluates quality on
each response, and prints a product-facing quality/ops summary with per-case
scores. Useful as a post-gate report for weekly health checks.
"""
from __future__ import print_function

import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
SRC = os.path.join(ROOT, "src")
CASES = os.path.join(ROOT, "harness", "cases", "release_v1.jsonl")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

os.environ.setdefault("DATA_AGENT_DB_MODE", "sandbox")

from release_api import ask_release, release_dashboard, record_gate_result
from release_dashboard import format_dashboard_text


def _load_cases():
    cases = []
    with open(CASES, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    return cases


def run_report():
    cases = _load_cases()
    rows = []
    for case in cases:
        env = ask_release(case["query"], session_id="quality_report", use_llm=False, user_id="report_runner")
        q = env.get("quality") or {}
        rows.append({
            "id": case["id"],
            "status": env.get("status"),
            "score": q.get("score"),
            "passed": q.get("passed"),
            "warnings": q.get("warnings"),
            "summary": (env.get("answer") or {}).get("summary", "")[:60],
        })
    return rows


def main():
    rows = run_report()
    print("\nRelease v1 Quality Report")
    print("=" * 80)
    print("%-16s %-22s %-7s %-6s  %-40s" % ("id", "status", "score", "pass?", "warnings"))
    print("-" * 80)
    for r in rows:
        warnings = ",".join(r.get("warnings") or []) or "-"
        print("%-16s %-22s %-7.4f %-6s  %-40s" % (
            r["id"], r["status"] or "-", r["score"] or 0.0,
            "Y" if r["passed"] else "N", warnings))
    print("=" * 80)

    # Overall dashboard from in-process state after this run
    d = release_dashboard()
    print(format_dashboard_text(d))

    below = sum(1 for r in rows if not r.get("passed"))
    record_gate_result("release_quality_report", below == 0, total=len(rows), failed=below,
                       summary="release_v1 envelope quality report")
    print("\nCases below quality threshold: %d / %d" % (below, len(rows)))
    return 1 if below > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
