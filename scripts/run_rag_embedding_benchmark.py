# -*- coding: utf-8 -*-
"""Reproducible RAG embedding benchmark with explicit environment blocking.

Runs deterministic/BGE/m3e configurations against one frozen JSONL corpus of
retrieval and abstention cases.  A provider initialization failure never falls
back silently: that variant is reported as ``blocked_by_environment``.
"""
from __future__ import print_function, unicode_literals
import hashlib
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from rag_embedding import EmbeddingProviderFactory
from rag_eval import RagQualityEvaluator
from rag_retriever import RagService
from rag_reranker import RerankerFactory


def _load_jsonl(path):
    rows = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _sha256(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _report_path():
    return os.path.join(ROOT, "harness", "reports", "rag_embedding_benchmark.json")


def _write(data):
    path = _report_path()
    directory = os.path.dirname(path)
    if not os.path.isdir(directory):
        os.makedirs(directory)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2, sort_keys=True)


def _variant(name, cases, reranker):
    started = time.time()
    provider_name = name
    kwargs = {}
    if name == "bge":
        kwargs["model_name"] = os.environ.get("RAG_BGE_MODEL", "BAAI/bge-small-zh-v1.5")
    elif name == "m3e":
        kwargs["model_name"] = os.environ.get("RAG_M3E_MODEL", "moka-ai/m3e-base")
    try:
        provider = EmbeddingProviderFactory.create(provider_name, **kwargs)
        service = RagService(embedding_provider=provider, reranker=RerankerFactory.create(reranker))
        evaluator = RagQualityEvaluator(service)
        metrics = evaluator.evaluate(cases)
        failures = evaluator.pass_thresholds(metrics)
        return {"status": "passed" if not failures else "failed", "provider": provider_name,
                "model": getattr(provider, "model_name", None), "reranker": reranker,
                "metrics": metrics, "failures": failures,
                "latency_ms": round((time.time() - started) * 1000, 3)}
    except Exception as exc:
        return {"status": "blocked_by_environment", "provider": provider_name,
                "model": kwargs.get("model_name"), "reranker": reranker,
                "reason": str(exc), "latency_ms": round((time.time() - started) * 1000, 3)}


def main():
    case_path = os.environ.get("RAG_BENCHMARK_CASES", os.path.join(ROOT, "harness", "cases", "rag_core.jsonl"))
    reranker = os.environ.get("RAG_BENCHMARK_RERANKER", "lexical")
    requested = [x.strip() for x in os.environ.get("RAG_BENCHMARK_VARIANTS", "deterministic,bge,m3e").split(",") if x.strip()]
    cases = _load_jsonl(case_path)
    report = {
        "contract": "rag_embedding_benchmark_v1",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "manifest": {"case_path": os.path.relpath(case_path, ROOT), "case_sha256": _sha256(case_path),
                     "case_count": len(cases), "reranker": reranker,
                     "variants": requested, "seed": "deterministic_no_random_seed"},
        "variants": {name: _variant(name, cases, reranker) for name in requested},
    }
    statuses = [item["status"] for item in report["variants"].values()]
    report["status"] = "passed" if statuses and all(s == "passed" for s in statuses) else "partial_or_blocked"
    _write(report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["status"] == "passed" else 2


if __name__ == "__main__":
    sys.exit(main())
