# -*- coding: utf-8 -*-
"""Shared Answer Contract validation utilities.

This module centralizes the production-facing checks for final_answer_contract_v2
so release quality, release gates and API tests do not maintain parallel evidence
rules.  It intentionally returns a deterministic report instead of raising so it
can be used both by quality scoring and hard gates.
"""
from __future__ import unicode_literals


REQUIRED_ANSWER_CONTRACT_KEYS = [
    "contract", "status", "answer_type", "answer", "facts", "hypotheses",
    "citations", "limitations", "next_actions", "provenance", "trace_id",
    "task_id", "evidence_ids",
]
ANSWER_CONTRACT_STATUSES = [
    "ok", "blocked", "need_clarification", "no_answer",
    "pending_human_review", "error", "unsupported",
]
TERMINAL_STATUSES = [
    "ok", "blocked", "need_clarification", "no_answer",
    "pending_human_review", "error",
]


def _is_dict(value):
    return isinstance(value, dict)


def _is_list(value):
    return isinstance(value, list)


def _add_error(errors, code, message):
    errors.append({"code": code, "message": message})


def validate_answer_contract_envelope(envelope, require_production_fields=False):
    """Validate an envelope's final Answer Contract.

    Args:
        envelope: Release/API envelope containing status, answer_contract,
            provenance and optional raw payload.
        require_production_fields: When true, ok + verified facts require the
            full production lineage fields. S1 keeps this false for historical
            compatibility; S2 can turn it on for production profiles.

    Returns:
        dict with contract, passed, errors and warnings.
    """
    env = envelope or {}
    answer_contract = env.get("answer_contract") or {}
    status = env.get("status")
    errors = []
    warnings = []

    if not _is_dict(answer_contract):
        _add_error(errors, "answer_contract_not_object", "answer_contract must be an object")
        return {
            "contract": "answer_contract_validation_v1",
            "passed": False,
            "errors": errors,
            "warnings": warnings,
        }

    if answer_contract.get("contract") != "final_answer_contract_v2":
        _add_error(errors, "answer_contract_version", "answer_contract must be final_answer_contract_v2")
    for key in REQUIRED_ANSWER_CONTRACT_KEYS:
        if key not in answer_contract:
            _add_error(errors, "missing_answer_contract_key", "missing answer_contract key: %s" % key)
    if status not in TERMINAL_STATUSES:
        _add_error(errors, "unknown_terminal_status", "unknown envelope status: %s" % status)
    if answer_contract.get("status") not in ANSWER_CONTRACT_STATUSES:
        _add_error(errors, "unknown_answer_contract_status", "unknown answer_contract status: %s" % answer_contract.get("status"))
    if answer_contract.get("status") != status:
        _add_error(errors, "answer_contract_status_mismatch", "answer_contract status mismatch: %s != %s" % (answer_contract.get("status"), status))

    facts = answer_contract.get("facts") or []
    evidence_id_list = answer_contract.get("evidence_ids") or []
    evidence_ids = set(evidence_id_list)
    ac_provenance = answer_contract.get("provenance") or {}
    env_provenance = env.get("provenance") or {}
    raw = env.get("raw") or {}

    if not _is_list(facts):
        _add_error(errors, "facts_not_list", "answer_contract.facts must be a list")
        facts = []
    if not _is_list(evidence_id_list):
        _add_error(errors, "evidence_ids_not_list", "answer_contract.evidence_ids must be a list")
        evidence_ids = set()
    if not _is_dict(ac_provenance):
        _add_error(errors, "provenance_not_object", "answer_contract.provenance must be an object")
        ac_provenance = {}

    if status != "ok" and facts:
        _add_error(errors, "non_ok_facts", "non-ok terminal must not expose verified facts")

    for fact in facts:
        if not _is_dict(fact):
            _add_error(errors, "fact_not_object", "fact must be an object")
            continue
        ids = fact.get("evidence_ids") or []
        if not ids:
            _add_error(errors, "fact_missing_evidence_ids", "fact missing evidence_ids")
            continue
        if not _is_list(ids):
            _add_error(errors, "fact_evidence_ids_not_list", "fact.evidence_ids must be a list")
            continue
        missing = [eid for eid in ids if eid not in evidence_ids]
        if missing:
            _add_error(errors, "fact_unknown_evidence_ids", "fact references unknown evidence_ids: %s" % ",".join(missing))

    if status == "ok" and facts:
        required = ["row_count", "metric", "time_range"]
        if require_production_fields:
            required = ["row_count", "metric", "time_range", "query_id", "data_version", "dataid", "evidence_id"]
            if not answer_contract.get("trace_id"):
                _add_error(errors, "missing_trace_id", "ok verified facts require answer_contract.trace_id")
            if not answer_contract.get("task_id"):
                _add_error(errors, "missing_task_id", "ok verified facts require answer_contract.task_id")
        for key in required:
            value = ac_provenance.get(key)
            if value is None or value == "":
                _add_error(errors, "missing_fact_provenance", "ok facts require answer_contract.provenance.%s" % key)

        raw_execution = raw.get("execution_envelope") if _is_dict(raw) and _is_dict(raw.get("execution_envelope")) else {}
        raw_evidence_id = None
        if _is_dict(raw):
            raw_evidence_id = raw.get("evidence_id") or raw_execution.get("evidence_id")
        if raw_evidence_id and raw_evidence_id not in evidence_ids:
            _add_error(errors, "raw_evidence_id_missing", "raw evidence_id is not represented in answer_contract.evidence_ids")
        if raw_evidence_id and ac_provenance.get("evidence_id") and raw_evidence_id != ac_provenance.get("evidence_id"):
            _add_error(errors, "evidence_id_mismatch", "answer_contract evidence_id mismatch")

        raw_query_id = None
        if _is_dict(raw):
            raw_query_id = raw.get("query_id") or raw_execution.get("query_id")
        if raw_query_id and ac_provenance.get("query_id") and raw_query_id != ac_provenance.get("query_id"):
            _add_error(errors, "query_id_mismatch", "answer_contract query_id mismatch")

        raw_data_version = None
        if _is_dict(raw):
            raw_data_version = raw.get("data_version") or raw_execution.get("data_version") or ((raw.get("fact_ledger") or {}) if _is_dict(raw.get("fact_ledger")) else {}).get("data_version")
        if raw_data_version and ac_provenance.get("data_version") and raw_data_version != ac_provenance.get("data_version"):
            _add_error(errors, "data_version_mismatch", "answer_contract data_version mismatch")

        raw_dataid = None
        if _is_dict(raw):
            raw_dataid = raw.get("dataid") or raw_execution.get("dataid")
        if raw_dataid and ac_provenance.get("dataid") and raw_dataid != ac_provenance.get("dataid"):
            _add_error(errors, "dataid_mismatch", "answer_contract dataid mismatch")

        env_exec = env_provenance.get("execution") if _is_dict(env_provenance) else {}
        if _is_dict(env_exec) and env_exec.get("row_count") is not None:
            if ac_provenance.get("row_count") != env_exec.get("row_count"):
                _add_error(errors, "row_count_mismatch", "answer_contract row_count mismatch")

    return {
        "contract": "answer_contract_validation_v1",
        "passed": not errors,
        "errors": errors,
        "warnings": warnings,
    }


__all__ = [
    "REQUIRED_ANSWER_CONTRACT_KEYS",
    "ANSWER_CONTRACT_STATUSES",
    "TERMINAL_STATUSES",
    "validate_answer_contract_envelope",
]
