# -*- coding: utf-8 -*-
"""Contracts and validation for dynamic multi-turn conversation seed datasets.

A dataset row intentionally contains only the initial user question.  Later
turns must be produced by a response-aware policy at execution time, rather
than being pre-authored as a fixed dialogue script.
"""
from __future__ import unicode_literals

import codecs
import json
try:
    basestring
except NameError:  # pragma: no cover - Python 3 compatibility
    basestring = str

INITIAL_CASE_CONTRACT = "dynamic_conversation_initial_case_v1"
ALLOWED_SAMPLE_TYPES = set(["positive", "negative", "generic_control"])
ALLOWED_SCENARIOS = set(["ecommerce", "generic"])


def validate_initial_case(row):
    """Return stable validation errors without executing a question."""
    row = row or {}
    errors = []
    if row.get("contract") != INITIAL_CASE_CONTRACT:
        errors.append("invalid_contract")
    if not row.get("case_id"):
        errors.append("case_id_required")
    if not isinstance(row.get("initial_question"), basestring) or not row.get("initial_question", "").strip():
        errors.append("initial_question_required")
    if row.get("scenario") not in ALLOWED_SCENARIOS:
        errors.append("invalid_scenario")
    metadata = row.get("metadata") or {}
    if metadata.get("sample_type") not in ALLOWED_SAMPLE_TYPES:
        errors.append("invalid_sample_type")
    max_turns = row.get("max_turns")
    if not isinstance(max_turns, int) or max_turns < 1 or max_turns > 10:
        errors.append("max_turns_must_be_1_to_10")
    outcomes = row.get("expected_safe_outcomes")
    if not isinstance(outcomes, list) or not outcomes:
        errors.append("expected_safe_outcomes_required")
    # Seed data must never predetermine subsequent user messages.
    forbidden = set(["followups", "turns", "next_question", "dialogue"])
    if any(key in row for key in forbidden):
        errors.append("seed_dataset_must_not_contain_pre_authored_followups")
    return errors


def load_initial_cases(path):
    cases = []
    seen = set()
    with codecs.open(path, "r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            row = json.loads(line)
            errors = validate_initial_case(row)
            if row.get("case_id") in seen:
                errors.append("duplicate_case_id")
            if errors:
                raise ValueError("case line %s invalid: %s" % (line_number, ",".join(errors)))
            seen.add(row["case_id"])
            cases.append(row)
    return cases


def summarize_initial_cases(cases):
    """Return a deterministic aggregate suitable for a dataset gate."""
    cases = list(cases or [])
    summary = {"contract": "dynamic_conversation_dataset_summary_v1", "total": len(cases),
               "by_scenario": {}, "by_sample_type": {}, "by_category": {}}
    for row in cases:
        summary["by_scenario"][row["scenario"]] = summary["by_scenario"].get(row["scenario"], 0) + 1
        sample_type = (row.get("metadata") or {}).get("sample_type")
        summary["by_sample_type"][sample_type] = summary["by_sample_type"].get(sample_type, 0) + 1
        category = row.get("category") or "uncategorized"
        summary["by_category"][category] = summary["by_category"].get(category, 0) + 1
    summary["valid"] = (summary["total"] == 100 and
                        summary["by_scenario"].get("ecommerce", 0) >= 70 and
                        summary["by_sample_type"].get("negative", 0) >= 20 and
                        summary["by_sample_type"].get("generic_control", 0) >= 10)
    return summary


__all__ = ["INITIAL_CASE_CONTRACT", "validate_initial_case", "load_initial_cases", "summarize_initial_cases"]
