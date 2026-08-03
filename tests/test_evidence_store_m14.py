# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, "src"))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from contracts import build_execution_envelope
from evidence_bus import EvidenceBus
from evidence_store import InMemoryEvidenceStore
from persistence_sqlite import SQLitePersistence
from repository_contracts import RepositoryAccessContext


def _verified_record(metric="gmv", time_range="last_7_days", tenant_trace="trace-m14"):
    envelope = build_execution_envelope(
        status="ok", stage="db_execute", query_id="q_m14", evidence_id="ev_m14",
        dataid="orders", data_version="v1", row_count=1, time_range=time_range,
        authority="verified_execution", metadata={"metric": metric, "dimensions": [], "filters": {}})
    bus = EvidenceBus()
    return bus.record_envelope(envelope, producer_task_id="data_analyst", trace_id=tenant_trace, graph_type="metric_query")


def test_inmemory_evidence_store_is_tenant_and_session_scoped():
    store = InMemoryEvidenceStore()
    record = _verified_record()

    store.save_record("tenant-a", "sess-1", record)

    assert store.get_record("tenant-a", "sess-1", "ev_m14")["evidence_id"] == "ev_m14"
    assert store.get_record("tenant-b", "sess-1", "ev_m14") is None
    assert store.get_record("tenant-a", "sess-2", "ev_m14") is None
    listed = store.list_records("tenant-a", "sess-1")
    assert len(listed) == 1
    assert listed[0]["evidence_id"] == record["evidence_id"]
    assert listed[0]["authority"] == "verified_execution"


def test_sqlite_evidence_repository_roundtrip_and_tenant_scope():
    store = SQLitePersistence(":memory:")
    access_a = RepositoryAccessContext(user_id="u", tenant_id="tenant-a", verified=True, source="test")
    access_b = RepositoryAccessContext(user_id="u", tenant_id="tenant-b", verified=True, source="test")
    record = _verified_record()

    store.save_evidence(access_a, "sess-1", record["evidence_id"], record)

    assert store.get_evidence(access_a, "sess-1", "ev_m14")["evidence_id"] == "ev_m14"
    assert store.get_evidence(access_b, "sess-1", "ev_m14") is None
    assert store.list_evidence(access_a, "sess-1")[0]["authority"] == "verified_execution"


def test_facade_can_reuse_persisted_verified_evidence_after_restart():
    from agent_facade import AgentFacade
    from memory_store import MemoryStore

    store = InMemoryEvidenceStore()
    first = AgentFacade(session_id="persisted-evidence-session", evidence_store=store,
                        access_context={"tenant_id": "tenant-a", "user_id": "u1"})
    first.memory = MemoryStore()
    result = first.ask(u"最近7天GMV")
    assert result.get("status") == "ok"
    evidence_id = result.get("evidence_id")
    assert evidence_id
    assert store.get_record("tenant-a", "persisted-evidence-session", evidence_id)

    # Simulate process restart: copy only safe session memory and hydrate a new bus from store.
    restarted = AgentFacade(session_id="persisted-evidence-session", evidence_store=store,
                            access_context={"tenant_id": "tenant-a", "user_id": "u1"})
    restarted.memory = first.memory
    follow = restarted.follow_up(u"解释一下")

    assert follow.get("status") == "ok"
    assert follow.get("follow_up_context", {}).get("decision") == "reuse_verified_evidence"
    assert evidence_id in (follow.get("fact_ledger") or {}).get("evidence_refs", [])


def test_facade_rejects_persisted_evidence_for_other_tenant():
    from agent_facade import AgentFacade
    from memory_store import MemoryStore

    store = InMemoryEvidenceStore()
    first = AgentFacade(session_id="persisted-evidence-tenant", evidence_store=store,
                        access_context={"tenant_id": "tenant-a", "user_id": "u1"})
    first.memory = MemoryStore()
    result = first.ask(u"最近7天GMV")
    evidence_id = result.get("evidence_id")
    assert evidence_id

    restarted = AgentFacade(session_id="persisted-evidence-tenant", evidence_store=store,
                            access_context={"tenant_id": "tenant-b", "user_id": "u2"})
    restarted.memory = first.memory
    follow = restarted.follow_up(u"解释一下")

    assert follow.get("follow_up_context", {}).get("decision") == "reuse_context_only"
    assert "missing_evidence_ref" in follow.get("follow_up_context", {}).get("decision_reasons", [])
    assert evidence_id not in (follow.get("fact_ledger") or {}).get("evidence_refs", [])


def test_facade_records_evidence_store_persist_and_hydration_trace_events():
    from agent_facade import AgentFacade
    from memory_store import MemoryStore
    from trace_contracts import build_trace_envelope, validate_trace_envelope

    store = InMemoryEvidenceStore()
    first = AgentFacade(session_id="persisted-evidence-trace", evidence_store=store,
                        access_context={"tenant_id": "tenant-a", "user_id": "u1"})
    first.memory = MemoryStore()
    result = first.ask(u"最近7天GMV")
    trace = first.get_trace()
    event_names = [item.get("name") for item in trace]

    assert result.get("status") == "ok"
    assert "evidence_store_load" in event_names
    assert "evidence_store_persist" in event_names
    persisted = [item for item in trace if item.get("name") == "evidence_store_persist"]
    assert persisted[0].get("stage") == "evidence_store"
    assert result.get("evidence_id") in (persisted[0].get("metadata") or {}).get("evidence_id")
    envelope = build_trace_envelope(trace, result=result, case={"id": "evidence-store-trace"})
    assert "evidence_store" in envelope.get("stage_order")
    assert validate_trace_envelope(envelope)["valid"] is True

    restarted = AgentFacade(session_id="persisted-evidence-trace", evidence_store=store,
                            access_context={"tenant_id": "tenant-a", "user_id": "u1"})
    restarted.memory = first.memory
    follow = restarted.follow_up(u"解释一下")
    follow_trace = restarted.get_trace()
    load_events = [item for item in follow_trace if item.get("name") == "evidence_store_load"]
    assert follow.get("follow_up_context", {}).get("decision") == "reuse_verified_evidence"
    assert load_events
    assert (load_events[0].get("metadata") or {}).get("loaded_count") >= 1
