# -*- coding: utf-8 -*-
"""Product-facing quality checks for Release v1 envelopes.

The goal of this module is not to judge internal implementation details. It
scores whether a response is usable for a trial user: stable envelope, readable
answer, provenance/credibility hints, safe terminal state, and acceptable
latency. The policy is data-driven enough to evolve with future releases while
remaining deterministic for CI gates.
"""
from __future__ import unicode_literals

from answer_contract_validator import (
    ANSWER_CONTRACT_STATUSES,
    REQUIRED_ANSWER_CONTRACT_KEYS,
    TERMINAL_STATUSES,
    validate_answer_contract_envelope,
)


DEFAULT_POLICY = {
    "min_score_by_status": {
        "ok": 0.78,
        "blocked": 0.70,
        "need_clarification": 0.68,
        "no_answer": 0.68,
        "pending_human_review": 0.68,
        "error": 0.55,
    },
    "latency_warn_ms": 1500,
    "latency_fail_ms": 5000,
}

REQUIRED_ENVELOPE_KEYS = [
    "contract", "status", "session_id", "audit_id", "query", "answer",
    "plan", "credibility", "provenance", "elapsed_ms", "answer_contract",
]
REQUIRED_ANSWER_KEYS = ["summary", "table", "chart", "caveats", "next_steps"]
def _has_text(value):
    return isinstance(value, str) and bool(value.strip())


def _is_dict(value):
    return isinstance(value, dict)


def _list_like(value):
    return isinstance(value, list)


def evaluate_release_envelope(envelope, policy=None):
    """Return a deterministic quality report for a release response envelope."""
    policy = policy or DEFAULT_POLICY
    env = envelope or {}
    answer = env.get("answer") or {}
    answer_contract = env.get("answer_contract") or {}
    status = env.get("status") or "error"
    elapsed_ms = env.get("elapsed_ms")
    answer_contract_validation = validate_answer_contract_envelope(env, require_production_fields=True)

    checks = []

    def add(name, passed, weight, detail=""):
        checks.append({
            "name": name,
            "passed": bool(passed),
            "weight": float(weight),
            "detail": detail,
        })

    add("stable_envelope_keys", all(k in env for k in REQUIRED_ENVELOPE_KEYS), 0.18,
        "Release envelope must expose stable product contract fields.")
    add("stable_answer_keys", _is_dict(answer) and all(k in answer for k in REQUIRED_ANSWER_KEYS), 0.10,
        "Answer must contain summary/table/chart/caveats/next_steps.")
    add("stable_answer_contract_keys", _is_dict(answer_contract) and all(k in answer_contract for k in REQUIRED_ANSWER_CONTRACT_KEYS), 0.12,
        "Final answer_contract must expose the versioned Answer Contract for every terminal state.")
    add("answer_contract_status", _is_dict(answer_contract) and answer_contract.get("contract") == "final_answer_contract_v2" and answer_contract.get("status") in ANSWER_CONTRACT_STATUSES, 0.10,
        "answer_contract must be final_answer_contract_v2 with a documented terminal status.")
    add("answer_contract_evidence", answer_contract_validation.get("passed"), 0.12,
        "Answer Contract facts must have in-scope evidence_ids and consistent provenance.")
    add("known_terminal_status", status in TERMINAL_STATUSES, 0.08,
        "Status must be one of the documented terminal states.")
    add("readable_summary", _has_text(answer.get("summary")), 0.14,
        "User-facing answer.summary must be non-empty.")
    add("audit_traceable", _has_text(env.get("audit_id")) and _has_text(env.get("session_id")), 0.10,
        "Each response must be traceable by audit_id and session_id.")
    add("answer_shapes", _list_like(answer.get("table")) and _is_dict(answer.get("chart")) and _list_like(answer.get("caveats")) and _list_like(answer.get("next_steps")), 0.08,
        "Answer payload types must be stable for UI rendering.")
    add("credibility_shape", _is_dict(env.get("credibility")), 0.07,
        "Credibility must be a dictionary even when empty.")
    add("provenance_shape", _is_dict(env.get("provenance")), 0.07,
        "Provenance must be a dictionary even when empty.")
    add("latency_present", isinstance(elapsed_ms, int) and elapsed_ms >= 0, 0.02,
        "Response must include elapsed_ms.")
    add("latency_acceptable", isinstance(elapsed_ms, int) and elapsed_ms < int(policy.get("latency_fail_ms", 5000)), 0.02,
        "Response should not exceed latency_fail_ms.")

    total_weight = sum(c["weight"] for c in checks) or 1.0
    score = sum(c["weight"] for c in checks if c["passed"]) / total_weight
    threshold = (policy.get("min_score_by_status") or {}).get(status, 0.60)
    warnings = []
    if isinstance(elapsed_ms, int) and elapsed_ms >= int(policy.get("latency_warn_ms", 1500)):
        warnings.append("latency_warn")
    if status == "ok" and not answer.get("table") and not answer.get("chart"):
        warnings.append("ok_without_table_or_chart")
    if status in ("blocked", "need_clarification", "no_answer", "pending_human_review") and not _has_text(answer.get("summary")):
        warnings.append("terminal_without_summary")
    if not _is_dict(answer_contract):
        warnings.append("missing_answer_contract")
    for err in answer_contract_validation.get("errors") or []:
        warnings.append("answer_contract:%s" % err.get("code"))

    return {
        "contract": "release_v1_quality",
        "score": round(score, 4),
        "threshold": threshold,
        "passed": score >= threshold and all(c["passed"] for c in checks if c["weight"] >= 0.10),
        "status": status,
        "warnings": warnings,
        "checks": checks,
        "answer_contract_validation": answer_contract_validation,
    }


__all__ = ["DEFAULT_POLICY", "evaluate_release_envelope"]
