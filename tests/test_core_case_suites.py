# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from agent_harness import AgentHarness

sys.path.insert(0, os.path.join(ROOT, "scripts"))
import run_agent_harness


CASE_FILES = {
    "regression_core.jsonl": 12,
    "multiturn_core.jsonl": 8,
    "tool_calling_core.jsonl": 6,
    "security_governance_core.jsonl": 8,
}


def _cases_path(name):
    return os.path.join(ROOT, "harness", "cases", name)


def test_core_case_suites_have_stable_ids_categories_and_expected_contracts():
    harness = AgentHarness()
    total = 0
    ids = set()
    for name, expected_count in CASE_FILES.items():
        cases = harness.load_cases(_cases_path(name))
        assert len(cases) == expected_count
        total += len(cases)
        for case in cases:
            assert case["id"] not in ids
            ids.add(case["id"])
            assert case["category"]
            assert case["scenario"]
            assert case["expected"]["status"]
    assert total == 34


def test_core_case_suites_cover_required_product_paths():
    harness = AgentHarness()
    cases = []
    for name in CASE_FILES:
        cases.extend(harness.load_cases(_cases_path(name)))

    statuses = set(case["expected"]["status"] for case in cases)
    task_types = set(
        case["expected"].get("task_type") for case in cases
        if case["expected"].get("task_type")
    )
    categories = set(case["category"] for case in cases)
    assert {"ok", "blocked", "pending_human_review", "need_clarification", "unsupported", "error"}.issubset(statuses)
    assert {"descriptive", "comparison", "attribution", "anomaly"}.issubset(task_types)
    assert {"tool_calling", "security_governance", "follow_up", "funnel"}.issubset(categories)


def test_harness_reports_stage_level_failure_breakdown():
    harness = AgentHarness()
    metrics = harness.summarize([
        {"passed": False, "failure_type": "routing_error", "expected": {}, "result": {}, "trace": []},
        {"passed": False, "failure_type": "planning_error", "expected": {}, "result": {}, "trace": []},
        {"passed": False, "failure_type": "execution_error", "expected": {}, "result": {}, "trace": []},
        {"passed": False, "failure_type": "governance_error", "expected": {}, "result": {}, "trace": []},
    ])
    assert metrics["failure_stage_breakdown"] == {
        "routing": 1, "planning": 1, "execution": 1, "governance": 1,
    }


def test_core_suites_are_available_by_runner_alias():
    harness = AgentHarness()
    assert len(run_agent_harness._load_suite_cases(harness, "regression_core")) == 12
    assert len(run_agent_harness._load_suite_cases(harness, "multiturn_core")) == 8
    assert len(run_agent_harness._load_suite_cases(harness, "tool_calling_core")) == 6
    assert len(run_agent_harness._load_suite_cases(harness, "security_governance_core")) == 8
