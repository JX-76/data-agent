# -*- coding: utf-8 -*-
from __future__ import print_function, unicode_literals

import json
import os
import sys
import io

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from rag_eval import RagQualityEvaluator
from rag_retriever import RagService

try:
    text_type = unicode
except NameError:  # pragma: no cover - Python 3
    text_type = str


def _json_text(value):
    rendered = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    if not isinstance(rendered, text_type):
        rendered = rendered.decode("utf-8")
    return rendered


def load_cases(path):
    cases = []
    with io.open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    return cases


def main():
    case_path = os.path.join(ROOT, "harness", "cases", "rag_core.jsonl")
    report_path = os.path.join(ROOT, "harness", "reports", "rag_quality_report.json")
    cases = load_cases(case_path)
    service = RagService.local(reranker_provider="lexical")
    evaluator = RagQualityEvaluator(service)
    metrics = evaluator.evaluate(cases)
    failures = evaluator.pass_thresholds(metrics)
    report = {
        "status": "passed" if not failures else "failed",
        "metrics": metrics,
        "failures": failures,
        "case_count": len(cases),
        "case_path": case_path,
    }
    if not os.path.isdir(os.path.dirname(report_path)):
        os.makedirs(os.path.dirname(report_path))
    rendered = _json_text(report)
    with io.open(report_path, "w", encoding="utf-8") as f:
        f.write(rendered)
    print(rendered)
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
