# -*- coding: utf-8 -*-
"""Versioned, safe-to-evaluate answer contract.

The canonical agent result remains backward compatible.  This module creates a
small, serialisable projection for evaluation and product clients: generated
prose is separated from claims, and references to tool/RAG/memory evidence are
typed rather than inferred from free text.
"""
from __future__ import unicode_literals

import hashlib
import json
import re

try:
    unicode
except NameError:  # pragma: no cover
    unicode = str


NUMERIC_RE = re.compile(r"(?:\d[\d,]*(?:\.\d+)?\s*(?:%|％|元|件|单|个|次|人|万元|亿))")
SENSITIVE_KEYWORDS = ("password", "secret", "token", "api_key", "authorization", "cookie")
INTERNAL_ERROR_TOKENS = ("traceback", "exception", "sqlite", "postgres", "select ", "from ", "dim_", "fct_")


def _as_dict(value):
    if isinstance(value, dict):
        return value
    if hasattr(value, "to_dict"):
        try:
            return value.to_dict()
        except Exception:
            return {}
    return {}


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _text(value):
    if value is None:
        return u""
    if isinstance(value, unicode):
        return value
    try:
        return value.decode("utf-8", "ignore")
    except Exception:
        return unicode(value)


def _hash(value):
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        raw = _text(value)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _safe_value(value, key=""):
    """Return a report-safe primitive projection, never raw internal errors."""
    lowered = _text(key).lower()
    if any(token in lowered for token in SENSITIVE_KEYWORDS):
        return "[REDACTED]"
    if isinstance(value, dict):
        return dict((k, _safe_value(v, k)) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return [_safe_value(item, key) for item in value]
    text = _text(value)
    if any(token in text.lower() for token in INTERNAL_ERROR_TOKENS):
        return u"[INTERNAL_DETAIL_REDACTED]"
    return text if isinstance(value, (str, unicode)) else value


def _make_evidence(result):
    diagnostics = _as_dict(result.get("diagnostics"))
    cards = _as_list(diagnostics.get("evidence_cards"))
    evidence = []
    execution_envelope = _as_dict(result.get("execution_envelope") or diagnostics.get("execution_envelope"))
    if execution_envelope.get("evidence_id") and execution_envelope.get("authority") == "verified_execution":
        metadata = _as_dict(execution_envelope.get("metadata"))
        evidence.append({
            "evidence_id": execution_envelope.get("evidence_id"),
            "kind": "tool",
            "source": execution_envelope.get("stage") or "execution",
            "result_hash": _hash(execution_envelope),
            "parameters_summary": {"query_id": execution_envelope.get("query_id")},
            "row_count": execution_envelope.get("row_count"),
            "metric": metadata.get("metric") or result.get("metric"),
            "scope": metadata.get("filters") or result.get("filters") or {},
            "time_range": execution_envelope.get("time_range") or result.get("time_range"),
        })
    for index, card in enumerate(cards):
        card = _as_dict(card)
        evidence.append({
            "evidence_id": card.get("evidence_id") or card.get("id") or "tool:%s" % (index + 1),
            "kind": card.get("kind") or "tool",
            "source": card.get("tool") or card.get("source") or "execution",
            "result_hash": card.get("result_hash") or _hash(card.get("result") or card),
            "parameters_summary": _safe_value(card.get("parameters") or card.get("params") or {}),
            "row_count": card.get("row_count"),
            "metric": card.get("metric") or result.get("metric"),
            "scope": card.get("scope") or card.get("filters") or {},
            "time_range": card.get("time_range") or result.get("time_range"),
        })
    # Existing executions predate EvidenceCard. Supply an explicitly synthetic
    # reference so the evaluator can see execution happened, but do not claim it
    # supports a numeric fact.
    execution = _as_dict(result.get("execution"))
    if not evidence and (execution.get("used_db") or result.get("results") is not None):
        evidence.append({
            "evidence_id": "tool:execution:%s" % _hash(result.get("results") or []),
            "kind": "tool",
            "source": "execution",
            "result_hash": _hash(result.get("results") or []),
            "parameters_summary": {"metric": result.get("metric"), "dimensions": result.get("dimensions") or []},
            "row_count": _as_dict(result.get("results_summary")).get("row_count"),
            "metric": result.get("metric"), "scope": result.get("filters") or {},
            "time_range": result.get("time_range"),
        })
    return evidence


def _make_claims(result, user_answer, evidence):
    """Create conservative structural claims; no semantic fact is fabricated."""
    analysis = _as_dict(result.get("analysis"))
    findings = _as_list(analysis.get("key_findings"))
    if not findings:
        findings = _as_list(result.get("key_findings"))
    claims = []
    for index, finding in enumerate(findings):
        text = _text(finding)
        numeric = bool(NUMERIC_RE.search(text))
        claims.append({
            "claim_id": "claim:%s" % (index + 1),
            "text": text,
            "kind": "fact" if numeric else "observation",
            "numeric": numeric,
            # Numeric claims are intentionally left unsupported unless the
            # producing path supplied a per-claim evidence mapping.
            "evidence_ids": [],
            "scope": result.get("filters") or {},
            "time_range": result.get("time_range"),
            "confidence": None,
        })
    audit = _as_dict(result.get("claim_audit"))
    if audit.get("status") == "blocked":
        claims.append({"claim_id": "claim:audit", "text": _text(audit.get("safe_answer")),
                       "kind": "limitation", "numeric": False, "evidence_ids": [],
                       "scope": {}, "time_range": None, "confidence": None})
    return claims


def _answer_type(status, result):
    if status == "need_clarification":
        return "clarification"
    if status in ("blocked", "pending_human_review", "unsupported"):
        return "error" if result.get("error") else "evidence_limited"
    if status == "error":
        return "error"
    if status in ("no_answer", "degraded", "unsupported"):
        return "evidence_limited"
    if result.get("chart") and _as_dict(result.get("chart")).get("type") not in (None, "none"):
        return "chart"
    if result.get("report"):
        return "report"
    return "analysis"


def _canonical_status(status):
    if status in ("ok", "need_clarification", "blocked", "error", "no_answer"):
        return status
    if status in ("fallback", "degraded"):
        return "no_answer"
    if status in ("unsupported", "pending_human_review"):
        return status
    return "error" if status else "error"


def build_final_answer_contract(result, query=None):
    """Build the product-facing terminal answer contract.

    This v2 projection is additive and conservative: data facts are emitted only
    when they carry explicit evidence ids. Unsupported generated findings become
    hypotheses/limitations instead of verified facts.
    """
    result = _as_dict(result)
    legacy_status = result.get("status") or "error"
    status = _canonical_status(legacy_status)
    envelope = build_answer_envelope(result, query=query)
    answer = result.get("answer") or envelope.get("user_answer") or u""
    evidence_refs = envelope.get("evidence_refs") or []
    valid_evidence_ids = set([x.get("evidence_id") for x in evidence_refs if isinstance(x, dict) and x.get("evidence_id")])
    facts = []
    hypotheses = []
    for claim in envelope.get("claims") or []:
        if not isinstance(claim, dict):
            continue
        ids = [x for x in (claim.get("evidence_ids") or []) if x in valid_evidence_ids]
        text = _safe_value(claim.get("text") or "")
        if claim.get("kind") == "fact" and ids:
            facts.append({"text": text, "evidence_ids": ids})
        elif text:
            hypotheses.append({"text": text, "validation_needed": "current_verified_execution_evidence"})
    for fact in _as_list(result.get("facts")):
        fact = _as_dict(fact)
        text = _safe_value(fact.get("text") or "")
        ids = [x for x in (fact.get("evidence_ids") or []) if x in valid_evidence_ids]
        if text and ids:
            facts.append({"text": text, "evidence_ids": ids})
        elif text:
            hypotheses.append({"text": text, "validation_needed": "current_verified_execution_evidence"})
    limitations = list(envelope.get("limitations") or [])
    if legacy_status != status:
        limitations.append("legacy_status:%s mapped_to:%s" % (legacy_status, status))
    if not facts and status == "ok" and (envelope.get("claims") or []):
        limitations.append("claims_without_explicit_evidence_were_not_promoted_to_facts")
    provenance = _as_dict(result.get("provenance"))
    execution = _as_dict(provenance.get("execution"))
    semantic = _as_dict(provenance.get("semantic"))
    return {
        "contract": "final_answer_contract_v2",
        "status": status,
        "legacy_status": legacy_status if legacy_status != status else None,
        "answer_type": _answer_type(status, result),
        "answer": _safe_value(answer),
        "facts": facts,
        "hypotheses": hypotheses,
        "citations": list(envelope.get("rag_refs") or result.get("citations") or []),
        "limitations": _safe_value(limitations),
        "next_actions": _safe_value(result.get("next_actions") or []),
        "provenance": {
            "query_id": execution.get("query_id") or result.get("query_id"),
            "tool_call_id": execution.get("tool_call_id") or result.get("tool_call_id"),
            "evidence_id": execution.get("evidence_id") or result.get("evidence_id"),
            "dataid": execution.get("dataid") or result.get("dataid"),
            "data_version": execution.get("data_version") or result.get("data_version") or _as_dict(result.get("fact_ledger")).get("data_version"),
            "row_count": execution.get("row_count") or _as_dict(result.get("results_summary")).get("row_count"),
            "time_range": execution.get("time_range") or result.get("time_range"),
            "metric": semantic.get("metric") or execution.get("metric") or result.get("metric"),
        },
        "trace_id": result.get("trace_id"),
        "task_id": result.get("task_id"),
        "evidence_ids": sorted(valid_evidence_ids),
    }


def build_answer_envelope(result, query=None):
    """Build the additive ``answer_envelope_v1`` contract from a final result."""
    result = _as_dict(result)
    analysis = _as_dict(result.get("analysis"))
    report = _as_dict(result.get("report"))
    user_answer = result.get("answer") or report.get("answer") or report.get("summary") or analysis.get("summary")
    if not user_answer:
        user_answer = result.get("message") or result.get("blocked_reason") or u""
    user_answer = _safe_value(user_answer)
    evidence = _make_evidence(result)
    claims = _make_claims(result, user_answer, evidence)
    rag_refs = []
    for item in _as_list(result.get("rag_evidence")):
        item = _as_dict(item)
        rag_refs.append({"evidence_id": item.get("citation_id") or item.get("chunk_id"),
                         "kind": "rag", "source": item.get("source_uri") or item.get("title"),
                         "result_hash": _hash(item.get("supporting_extract") or item)})
    memory_refs = _as_list(_as_dict(result.get("conversation_context")).get("memory_refs"))
    diagnostics = _as_dict(result.get("diagnostics"))
    claim_audit = _as_dict(result.get("claim_audit"))
    findings = []
    for code in _as_list(claim_audit.get("unsupported_claims")):
        findings.append({"code": code, "severity": "high", "claim_id": None,
                         "message": u"回答中的陈述缺少足够执行证据。", "evidence_ids": []})
    return {
        "contract": "answer_envelope_v1",
        "status": result.get("status") or "error",
        "user_answer": user_answer,
        "structured_answer": {"summary": _safe_value(analysis.get("summary") or report.get("summary") or user_answer),
                              "key_findings": _safe_value(analysis.get("key_findings") or result.get("key_findings") or []),
                              "limitations": _safe_value(analysis.get("caveats") or report.get("caveats") or [])},
        "claims": claims,
        "evidence_refs": evidence,
        "tool_summary": {"used_db": bool(_as_dict(result.get("execution")).get("used_db")),
                         "tool_calls": _as_dict(result.get("execution")).get("tool_calls", 0),
                         "row_count": _as_dict(result.get("results_summary")).get("row_count")},
        "memory_refs": _safe_value(memory_refs),
        "rag_refs": rag_refs,
        "governance": _safe_value(result.get("human_gate") or result.get("permission_decision") or {}),
        "quality": _safe_value(result.get("quality") or {}),
        "hallucination_findings": findings,
        "claim_audit": _safe_value(claim_audit),
        "limitations": _safe_value(analysis.get("caveats") or report.get("caveats") or []),
        "trace_id": result.get("trace_id"), "task_id": result.get("task_id"),
        "session_id": result.get("session_id"),
        "query": _safe_value(query if query is not None else result.get("query")),
    }


__all__ = ["build_answer_envelope", "build_final_answer_contract"]
