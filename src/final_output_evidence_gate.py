# -*- coding: utf-8 -*-
"""Unified final-output evidence boundary.

This module is intentionally deterministic and dependency-light.  It validates
product-facing final outputs after an Answer Contract has been built, but before
release quality scoring or external presentation.  Unsupported verified facts
are demoted to hypotheses instead of being allowed to escape as high-confidence
business facts.
"""
from __future__ import unicode_literals

import copy
import time

from evidence_bus import EvidenceBus

CONTRACT = "final_output_evidence_validation_v1"
DEFAULT_TTL_SECONDS = 24 * 60 * 60


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


def _copy(value):
    try:
        return copy.deepcopy(value)
    except Exception:
        return value


def _add(finds, severity, code, message, evidence_id=None, fields=None):
    item = {
        "severity": severity,
        "code": code,
        "message": message,
    }
    if evidence_id is not None:
        item["evidence_id"] = evidence_id
    if fields:
        item["fields"] = list(fields)
    finds.append(item)
    return item


def _record_execution_envelope(bus, envelope, case_id, entrypoint, trace_id):
    envelope = _as_dict(envelope)
    if not envelope:
        return None
    return bus.record_envelope(envelope, case_id=case_id, graph_type=entrypoint, trace_id=trace_id)


def build_evidence_bus_from_envelope(envelope, case_id=None):
    """Build a local EvidenceBus from evidence embedded in a release envelope.

    Production can pass a durable EvidenceBus explicitly.  For the current MVP
    release path, verified execution envelopes are embedded in raw payloads, so
    this helper turns them into the same EvidenceBus contract used by Case.
    """
    env = _as_dict(envelope)
    raw = _as_dict(env.get("raw"))
    existing = raw.get("evidence_bus") or env.get("evidence_bus")
    if isinstance(existing, EvidenceBus):
        return existing
    bus = EvidenceBus.from_dict(existing) if isinstance(existing, dict) else EvidenceBus()
    answer_contract = _as_dict(env.get("answer_contract"))
    trace_id = answer_contract.get("trace_id") or raw.get("trace_id")
    entrypoint = env.get("final_output_entrypoint") or env.get("contract") or "final_output"
    _record_execution_envelope(bus, raw.get("execution_envelope"), case_id, entrypoint, trace_id)
    _record_execution_envelope(bus, raw.get("previous_execution_envelope"), case_id, entrypoint, trace_id)
    # Graph adapters keep worker envelopes under graph_result.results rather
    # than promoting each one to the legacy result projection.  Register every
    # server-produced envelope so comparison citations are checked against both
    # current and baseline evidence, not treated as an uncitable convenience.
    graph_result = _as_dict(raw.get("graph_result"))
    _record_execution_envelope(bus, graph_result.get("execution_envelope"), case_id, entrypoint, trace_id)
    _record_execution_envelope(bus, graph_result.get("previous_execution_envelope"), case_id, entrypoint, trace_id)
    for node in _as_dict(graph_result.get("results")).values():
        _record_execution_envelope(bus, _as_dict(node).get("output", {}).get("execution_envelope"),
                                   case_id, entrypoint, trace_id)
    diagnostics = _as_dict(raw.get("diagnostics"))
    _record_execution_envelope(bus, diagnostics.get("execution_envelope"), case_id, entrypoint, trace_id)
    return bus


def _expected_scope(envelope, access_context=None):
    env = _as_dict(envelope)
    raw = _as_dict(env.get("raw"))
    ac = _as_dict(env.get("answer_contract"))
    provenance = _as_dict(ac.get("provenance"))
    access_context = _as_dict(access_context)
    scope = {}
    for key in ("metric", "time_range", "dataid", "data_version"):
        value = provenance.get(key) if provenance.get(key) is not None else raw.get(key)
        if value not in (None, "", [], {}):
            if key == "time_range":
                allowed_ranges = [value]
                graph_request = _as_dict(_as_dict(raw.get("graph_result")).get("request"))
                comparison_range = graph_request.get("compare_time_range") or raw.get("compare_time_range")
                if comparison_range not in (None, "", [], {}) and comparison_range not in allowed_ranges:
                    allowed_ranges.append(comparison_range)
                scope["allowed_time_ranges"] = allowed_ranges
            else:
                scope[key] = value
    for key in ("tenant_id", "user_id", "permission_scope"):
        value = access_context.get(key) if access_context.get(key) is not None else raw.get(key)
        if value not in (None, "", [], {}):
            scope[key] = value
    return scope


def _fact_evidence_ids(answer_contract):
    ids = []
    for fact in _as_list(_as_dict(answer_contract).get("facts")):
        for eid in _as_list(_as_dict(fact).get("evidence_ids")):
            if eid not in ids:
                ids.append(eid)
    return ids


def _citation_evidence_ids(answer_contract):
    """Return citation ids from either the legacy string or structured form."""
    ids = []
    for citation in _as_list(_as_dict(answer_contract).get("citations")):
        if isinstance(citation, basestring):
            eid = citation
        else:
            citation = _as_dict(citation)
            eid = citation.get("evidence_id") or citation.get("citation_id") or citation.get("chunk_id")
        if eid and eid not in ids:
            ids.append(eid)
    return ids


def _all_referenced_evidence_ids(answer_contract):
    ids = []
    for eid in _as_list(_as_dict(answer_contract).get("evidence_ids")):
        if eid and eid not in ids:
            ids.append(eid)
    for eid in _fact_evidence_ids(answer_contract) + _citation_evidence_ids(answer_contract):
        if eid and eid not in ids:
            ids.append(eid)
    return ids


def validate_final_output_boundary(envelope, evidence_bus=None, case_id=None, access_context=None,
                                   require_evidence_bus=True, ttl_seconds=DEFAULT_TTL_SECONDS,
                                   now=None, entrypoint="final_output", legacy_exception=None):
    env = _as_dict(envelope)
    answer_contract = _as_dict(env.get("answer_contract"))
    case_id = case_id or env.get("case_id") or env.get("session_id")
    now = time.time() if now is None else float(now)
    findings = []
    metrics = {
        "final_output_lineage_coverage": 0.0,
        "unsupported_fact_block_rate": 0.0,
        "stale_evidence_rejection_rate": 0.0,
        "scope_leakage_attempt_rate": 0.0,
        "citation_validation_failure_rate": 0.0,
        "false_rejection_observation_count": 0,
    }

    if answer_contract.get("contract") != "final_answer_contract_v2":
        _add(findings, "high", "missing_answer_contract", "final output must carry final_answer_contract_v2")
        return _report(entrypoint, case_id, require_evidence_bus, legacy_exception, findings, metrics)

    facts = _as_list(answer_contract.get("facts"))
    fact_ids = _fact_evidence_ids(answer_contract)
    citation_ids = _citation_evidence_ids(answer_contract)
    referenced_ids = _all_referenced_evidence_ids(answer_contract)
    needs_evidence = bool(facts or citation_ids)

    if legacy_exception:
        if facts:
            _add(findings, "high", "legacy_high_confidence_fact", "legacy exception cannot expose verified facts")
        return _report(entrypoint, case_id, require_evidence_bus, legacy_exception, findings, metrics,
                       total_fact_count=len(facts), unsupported_fact_count=len(facts))

    if require_evidence_bus and needs_evidence and evidence_bus is None:
        _add(findings, "high", "missing_evidence_bus", "evidence-producing final output requires EvidenceBus")
        return _report(entrypoint, case_id, require_evidence_bus, legacy_exception, findings, metrics,
                       total_fact_count=len(facts), unsupported_fact_count=len(facts))

    bus = evidence_bus
    if bus is None:
        bus = build_evidence_bus_from_envelope(env, case_id=case_id)

    if needs_evidence and not referenced_ids:
        _add(findings, "high", "missing_evidence_refs", "facts/citations require evidence ids")

    invalid_fact_ids = set()
    citation_failures = 0
    stale = 0
    scope_failures = 0
    expected_scope = _expected_scope(env, access_context=access_context)
    valid_ids, rejected = bus.validate_scope(referenced_ids, expected_scope=expected_scope,
                                             ttl_seconds=ttl_seconds, now=now)
    valid_set = set(valid_ids)
    for item in rejected:
        item = _as_dict(item)
        code = item.get("error") or "evidence_rejected"
        if code == "evidence_ttl_expired":
            stale += 1
        if code == "evidence_scope_mismatch":
            scope_failures += 1
        _add(findings, "high", code, "evidence rejected by scope/freshness gate",
             evidence_id=item.get("evidence_id"), fields=item.get("fields"))

    if case_id:
        linked = set(_as_list(bus.case_links.get(case_id)))
        for eid in referenced_ids:
            if eid in valid_set and eid not in linked:
                valid_set.discard(eid)
                _add(findings, "high", "evidence_not_linked_to_case",
                     "evidence must be linked to current Case/session before final output", evidence_id=eid)

    for eid in fact_ids:
        if eid not in valid_set:
            invalid_fact_ids.add(eid)
    if facts and not fact_ids:
        _add(findings, "high", "fact_missing_evidence_ids", "verified fact has no evidence ids")
        invalid_fact_ids.add("__missing__")

    for eid in citation_ids:
        if eid not in valid_set:
            citation_failures += 1
            _add(findings, "high", "citation_validation_failed", "citation references invalid evidence", evidence_id=eid)

    unsupported_fact_count = 0
    for fact in facts:
        ids = _as_list(_as_dict(fact).get("evidence_ids"))
        if not ids or any(eid in invalid_fact_ids or eid not in valid_set for eid in ids):
            unsupported_fact_count += 1

    return _report(entrypoint, case_id, require_evidence_bus, legacy_exception, findings, metrics,
                   total_fact_count=len(facts), unsupported_fact_count=unsupported_fact_count,
                   stale_count=stale, scope_count=scope_failures,
                   citation_failure_count=citation_failures,
                   referenced_count=len(referenced_ids), valid_count=len(valid_set),
                   expected_scope=expected_scope)


def _report(entrypoint, case_id, require_evidence_bus, legacy_exception, findings, metrics,
            total_fact_count=0, unsupported_fact_count=0, stale_count=0, scope_count=0,
            citation_failure_count=0, referenced_count=0, valid_count=0, expected_scope=None):
    blocked = any(item.get("severity") == "high" for item in findings)
    metrics = dict(metrics or {})
    metrics["final_output_lineage_coverage"] = 1.0 if referenced_count == 0 else float(valid_count) / float(referenced_count)
    metrics["unsupported_fact_block_rate"] = 0.0 if total_fact_count == 0 else float(unsupported_fact_count) / float(total_fact_count)
    metrics["stale_evidence_rejection_rate"] = 0.0 if referenced_count == 0 else float(stale_count) / float(referenced_count)
    metrics["scope_leakage_attempt_rate"] = 0.0 if referenced_count == 0 else float(scope_count) / float(referenced_count)
    metrics["citation_validation_failure_rate"] = 0.0 if referenced_count == 0 else float(citation_failure_count) / float(referenced_count)
    return {
        "contract": CONTRACT,
        "entrypoint": entrypoint,
        "case_id": case_id,
        "require_evidence_bus": bool(require_evidence_bus),
        "legacy_exception": _as_dict(legacy_exception) if legacy_exception else None,
        "allowed": not blocked,
        "action": "allow" if not blocked else "demote_or_block",
        "findings": findings,
        "metrics": metrics,
        "expected_scope": dict(expected_scope or {}),
        "validated_at": int(time.time()),
    }


def apply_final_output_evidence_gate(envelope, evidence_bus=None, case_id=None, access_context=None,
                                     require_evidence_bus=True, ttl_seconds=DEFAULT_TTL_SECONDS,
                                     now=None, entrypoint="final_output", legacy_exception=None):
    """Return a gated copy of ``envelope`` and attach validation/audit data."""
    env = _copy(_as_dict(envelope))
    report = validate_final_output_boundary(
        env, evidence_bus=evidence_bus, case_id=case_id, access_context=access_context,
        require_evidence_bus=require_evidence_bus, ttl_seconds=ttl_seconds, now=now,
        entrypoint=entrypoint, legacy_exception=legacy_exception)
    ac = _copy(_as_dict(env.get("answer_contract")))
    if ac and not report.get("allowed"):
        invalid_codes = set([item.get("code") for item in report.get("findings") or []])
        demoted = []
        for fact in _as_list(ac.get("facts")):
            fact = _as_dict(fact)
            demoted.append({
                "text": fact.get("text"),
                "validation_needed": "current_verified_execution_evidence",
                "evidence_gate_reason": sorted(invalid_codes),
                "former_evidence_ids": list(fact.get("evidence_ids") or []),
            })
        # A rejected evidence id may not remain as a final citation or a
        # top-level evidence reference.  Keeping it would make an
        # evidence-limited response appear cited even though it is not.
        ac["facts"] = []
        ac["citations"] = []
        ac["evidence_ids"] = []
        provenance = _as_dict(ac.get("provenance"))
        for key in ("evidence_id", "previous_evidence_id"):
            provenance.pop(key, None)
        ac["provenance"] = provenance
        ac["hypotheses"] = _as_list(ac.get("hypotheses")) + demoted
        limitations = _as_list(ac.get("limitations"))
        limitations.append("final_output_evidence_gate_demoted_unsupported_facts")
        ac["limitations"] = limitations
        if ac.get("answer_type") not in ("error", "clarification"):
            ac["answer_type"] = "evidence_limited"
        env["answer_contract"] = ac
        raw = _as_dict(env.get("raw"))
        if isinstance(raw.get("final_answer"), dict):
            raw["final_answer"] = _copy(ac)
            env["raw"] = raw
    env["final_output_evidence_gate"] = report
    provenance = _as_dict(env.get("provenance"))
    provenance["final_output_evidence_gate"] = {
        "contract": CONTRACT,
        "allowed": report.get("allowed"),
        "entrypoint": entrypoint,
        "finding_codes": [item.get("code") for item in report.get("findings") or []],
    }
    env["provenance"] = provenance
    return env


try:
    basestring
except NameError:  # pragma: no cover - Python 3 runtime
    basestring = str


__all__ = [
    "CONTRACT", "DEFAULT_TTL_SECONDS", "build_evidence_bus_from_envelope",
    "validate_final_output_boundary", "apply_final_output_evidence_gate",
]
