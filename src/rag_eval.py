# -*- coding: utf-8 -*-
"""RAG quality evaluation helpers."""
from __future__ import unicode_literals


class RagQualityEvaluator(object):
    def __init__(self, rag_service):
        self.rag_service = rag_service

    def evaluate(self, cases, k_values=(1, 3, 5, 10)):
        cases = list(cases or [])
        rows = []
        no_answer_total = 0
        no_answer_correct = 0
        citation_ok = 0
        for case in cases:
            result = self.rag_service.retrieve(case["query"], top_k=max(k_values), access_context=case.get("access_context"))
            got = [e.get("chunk_id") for e in result.get("evidence") or []]
            expected = set(case.get("expected_chunk_ids") or [])
            rows.append((got, expected))
            if case.get("expect_no_answer"):
                no_answer_total += 1
                if result.get("status") == "no_answer":
                    no_answer_correct += 1
            citations = result.get("citations") or []
            evidence_ids = set(got)
            if citations and all(c.get("chunk_id") in evidence_ids for c in citations):
                citation_ok += 1
            elif not citations and not got:
                citation_ok += 1

        metrics = {"case_count": len(cases)}
        for k in k_values:
            hit = 0
            for got, expected in rows:
                if not expected:
                    continue
                if expected.intersection(set(got[:k])):
                    hit += 1
            denom = len([1 for _, expected in rows if expected]) or 1
            metrics["recall@%s" % k] = float(hit) / float(denom)
        reciprocal = []
        ndcg = []
        for got, expected in rows:
            if not expected:
                continue
            rank = 0
            for idx, cid in enumerate(got, start=1):
                if cid in expected:
                    rank = idx
                    break
            reciprocal.append(0.0 if rank == 0 else 1.0 / float(rank))
            ndcg.append(0.0 if rank == 0 else 1.0 / self._log2(rank + 1))
        metrics["mrr@10"] = sum(reciprocal) / float(len(reciprocal) or 1)
        metrics["ndcg@10"] = sum(ndcg) / float(len(ndcg) or 1)
        metrics["citation_accuracy"] = float(citation_ok) / float(len(cases) or 1)
        metrics["no_answer_precision"] = 1.0 if no_answer_total == 0 else float(no_answer_correct) / float(no_answer_total)
        return metrics

    def pass_thresholds(self, metrics, thresholds=None):
        thresholds = thresholds or {
            "recall@5": 0.80,
            "mrr@10": 0.70,
            "ndcg@10": 0.75,
            "citation_accuracy": 0.90,
            "no_answer_precision": 0.85,
        }
        failures = []
        for key, value in thresholds.items():
            if float(metrics.get(key, 0.0)) < float(value):
                failures.append({"metric": key, "actual": metrics.get(key, 0.0), "threshold": value})
        return failures

    def _log2(self, value):
        import math
        return math.log(value, 2)
