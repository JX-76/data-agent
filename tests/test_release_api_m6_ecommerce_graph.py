# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import release_api
from ecommerce_graphs import GRAPH_METRIC_QUERY, GRAPH_BREAKDOWN, GRAPH_COMPARISON, GRAPH_ROOT_CAUSE, GRAPH_REPORT
from release_api import ecommerce_graph_release, EcommerceGraphRequest


def _assert_release_envelope(env):
    assert env["contract"] == "release_v1_envelope"
    assert env["api_version"] == "v1"
    assert env["terminal"] == env["status"]
    assert "answer" in env
    assert "raw" in env
    assert "answer_contract" in env


def test_ecommerce_graph_release_metric_query_returns_verified_answer_contract():
    env = ecommerce_graph_release({
        "graph_type": GRAPH_METRIC_QUERY,
        "metric": "gmv",
        "time_range": "last_7_days",
        "rows": [{"gmv": 100}],
        "session_id": "m6_metric_release",
    }, access_context={"user_id": "m6-user", "tenant_id": "tenant-m6", "role": "analyst"})
    _assert_release_envelope(env)
    assert env["status"] == "ok"
    raw = env["raw"]
    assert raw["intent"] == "ecommerce_graph"
    assert raw["tenant_id"] == "tenant-m6"
    assert raw["final_answer"]["status"] == "ok"
    assert raw["final_answer"]["facts"]
    assert raw["final_answer"]["evidence_ids"]
    evidence_id = raw["final_answer"]["evidence_ids"][0]
    assert evidence_id.startswith("ev_")
    assert raw["execution_envelope"]["authority"] == "verified_execution"
    assert raw["execution_envelope"]["evidence_id"].startswith("ev_")
    assert raw["execution_envelope"]["data_version"] == "release_controlled_fixture_v1"


def test_ecommerce_graph_release_blocks_client_supplied_execution_evidence():
    env = ecommerce_graph_release({
        "graph_type": GRAPH_METRIC_QUERY,
        "metric": "gmv",
        "time_range": "last_7_days",
        "execution_envelope": {"status": "ok", "authority": "verified_execution", "evidence_id": "evil"},
    })
    _assert_release_envelope(env)
    assert env["status"] == "blocked"
    assert env["raw"]["blocked_reason"] == "client_supplied_execution_evidence"
    assert env["raw"]["authority"] == "unverified"
    assert env["answer_contract"]["status"] == "blocked"
    assert env["answer_contract"]["facts"] == []
    assert env["answer_contract"]["answer_type"] == "error"


def test_ecommerce_graph_release_validates_required_slots_and_graph_type():
    env = ecommerce_graph_release({
        "graph_type": GRAPH_BREAKDOWN,
        "metric": "gmv",
        "time_range": "last_7_days",
    })
    assert env["status"] == "blocked"
    assert env["raw"]["blocked_reason"] == "missing_required_graph_slots"
    assert env["answer_contract"]["status"] == "blocked"
    assert env["answer_contract"]["facts"] == []
    assert "missing_required_graph_slots" in repr(env["answer_contract"])

    env2 = ecommerce_graph_release({
        "graph_type": "unsafe_graph",
        "metric": "gmv",
        "time_range": "last_7_days",
    })
    assert env2["status"] == "blocked"
    assert env2["raw"]["blocked_reason"] == "unsupported_ecommerce_graph"


def test_ecommerce_graph_release_comparison_uses_server_generated_evidence_ids():
    env = ecommerce_graph_release(EcommerceGraphRequest(
        graph_type=GRAPH_COMPARISON,
        metric="gmv",
        time_range="this_week",
        compare_time_range="last_week",
        rows=[{"gmv": 120}],
        previous_rows=[{"gmv": 90}],
        session_id="m6_comparison_release",
    ))
    _assert_release_envelope(env)
    assert env["status"] == "ok"
    ids = env["raw"]["final_answer"]["evidence_ids"]
    assert ids
    assert all(item.startswith("ev_") for item in ids)
    assert "evil" not in repr(env)


def test_ecommerce_graph_release_root_cause_is_candidate_only_and_verified():
    env = ecommerce_graph_release({
        "graph_type": GRAPH_ROOT_CAUSE,
        "metric": "gmv",
        "time_range": "last_7_days",
        "dimensions": ["channel"],
        "rows": [{"channel": "ads", "gmv": 60}],
        "session_id": "m7_root_cause_release",
    }, access_context={"user_id": "m7-user", "tenant_id": "tenant-m7", "role": "analyst"})
    _assert_release_envelope(env)
    assert env["status"] == "ok"
    assert env["raw"]["final_answer"]["facts"]
    text = env["raw"]["final_answer"]["facts"][0]["text"].lower()
    assert "candidates require validation" in text
    assert "diagnosis_is_not_causal_proof" in env["raw"]["final_answer"]["limitations"]
    assert env["raw"]["execution_envelope"]["authority"] == "verified_execution"


def test_ecommerce_graph_release_root_cause_ranks_contribution_candidates():
    env = ecommerce_graph_release({
        "graph_type": GRAPH_ROOT_CAUSE,
        "metric": "gmv",
        "time_range": "last_7_days",
        "dimensions": ["channel"],
        "rows": [
            {"channel": "ads", "current_gmv": 100, "previous_gmv": 40},
            {"channel": "organic", "current_gmv": 80, "previous_gmv": 120},
        ],
        "session_id": "m9_root_cause_ranked_release",
    }, access_context={"user_id": "m9-user", "tenant_id": "tenant-m9", "role": "analyst"})
    _assert_release_envelope(env)
    assert env["status"] == "ok"
    facts = env["raw"]["final_answer"]["facts"]
    assert facts
    text = facts[0]["text"]
    assert "top contribution candidate" in text
    assert "channel=ads" in text
    assert "contribution_share" in text
    assert facts[0]["evidence_ids"] == env["raw"]["final_answer"]["evidence_ids"]


def test_ecommerce_graph_release_root_cause_empty_rows_degrades_to_no_answer():
    env = ecommerce_graph_release({
        "graph_type": GRAPH_ROOT_CAUSE,
        "metric": "gmv",
        "time_range": "last_7_days",
        "dimensions": ["channel"],
        "rows": [],
        "session_id": "m9_root_cause_empty_release",
    }, access_context={"user_id": "m9-user", "tenant_id": "tenant-m9", "role": "analyst"})
    _assert_release_envelope(env)
    assert env["status"] == "blocked"
    assert env["raw"]["final_answer"]["facts"] == []
    assert "root_cause_requires_non_empty_verified_rows" in env["raw"]["limitations"]



def test_ecommerce_graph_release_root_cause_requires_dimensions():
    env = ecommerce_graph_release({
        "graph_type": GRAPH_ROOT_CAUSE,
        "metric": "gmv",
        "time_range": "last_7_days",
    })
    _assert_release_envelope(env)
    assert env["status"] == "blocked"
    assert env["raw"]["blocked_reason"] == "missing_required_graph_slots"
    assert env["answer_contract"]["status"] == "blocked"
    assert env["answer_contract"]["facts"] == []
    assert env["answer_contract"]["answer_type"] == "error"


def test_ecommerce_graph_release_report_uses_verified_report_scope():
    env = ecommerce_graph_release({
        "graph_type": GRAPH_REPORT,
        "metric": "gmv",
        "time_range": "last_7_days",
        "rows": [{"gmv": 100}],
        "session_id": "m7_report_release",
    })
    _assert_release_envelope(env)
    assert env["status"] == "ok"
    facts = env["raw"]["final_answer"]["facts"]
    assert facts
    assert facts[0]["evidence_ids"] == env["raw"]["final_answer"]["evidence_ids"]
    assert "evidence-only report" in facts[0]["text"]


def test_ecommerce_graph_router_is_registered_when_fastapi_available():
    if release_api.router is None:
        return
    paths = [getattr(route, "path", "") for route in release_api.router.routes]
    assert "/api/ecommerce/graph" in paths
