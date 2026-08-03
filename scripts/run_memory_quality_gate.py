# -*- coding: utf-8 -*-
from __future__ import print_function, unicode_literals

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from memory_context_service import MemoryContextService
from memory_eval import MemoryQualityEvaluator


def load_cases(path):
    cases = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    return cases


def main():
    case_path = os.path.join(ROOT, "harness", "cases", "memory_core.jsonl")
    report_path = os.path.join(ROOT, "harness", "reports", "memory_quality_report.json")
    service = MemoryContextService()
    evaluator = MemoryQualityEvaluator(service)
    metrics = evaluator.evaluate(load_cases(case_path))
    failures = evaluator.pass_thresholds(metrics)
    report = {"status": "passed" if not failures else "failed", "metrics": metrics, "failures": failures, "case_path": case_path}
    parent = os.path.dirname(report_path)
    if not os.path.isdir(parent):
        os.makedirs(parent)
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, sort_keys=True)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
