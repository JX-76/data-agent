# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import os
import sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path: sys.path.insert(0, SRC)

import time

from memory_store import MemoryStore
from clarification_state import ClarificationStateMachine
from followup_context import FollowupContextResolver
from contracts import build_execution_envelope
from evidence_bus import EvidenceBus


def _resolver():
    memory = MemoryStore()
    memory.remember("session", "last_result", {
        "task_id": "task-1", "status": "ok", "metric": "gmv",
        "dimensions": ["date"], "filters": {}, "time_range": "last_7_days",
        "task_type": "descriptive", "dataid": "orders", "data_version": "v1",
        "fact_ledger": {"authority": "verified_execution", "verified": True,
                        "evidence_refs": ["ev-1"], "captured_at": time.time(),
                        "dataid": "orders", "data_version": "v1"}})
    return FollowupContextResolver(memory, ClarificationStateMachine(), "r22"), memory


def test_drill_down_inherits_metric_and_time():
    resolver, unused = _resolver(); value = resolver.resolve(u"按渠道拆一下")
    assert value["followup_intent"] == "drill_down"
    assert value["resolved_context"]["metric"] == "gmv"
    assert value["resolved_context"]["dimensions"] == ["channel"]
    assert value["resolved_context"]["time_range"] == "last_7_days"
    assert value["decision"] == "inherit_and_reexecute"
    assert "scope_changed:dimensions" in value["decision_reasons"][0]


def test_filter_override_merges_channel():
    resolver, unused = _resolver(); value = resolver.resolve(u"只看淘宝")
    assert value["followup_intent"] == "filter_override"
    assert value["resolved_context"]["filters"] == {"channel": "淘宝"}


def test_region_filter_override():
    resolver, unused = _resolver(); value = resolver.resolve(u"换成华东")
    assert value["resolved_context"]["filters"]["region"] == "华东"


def test_dimension_override_category():
    resolver, unused = _resolver(); value = resolver.resolve(u"按品类拆一下")
    assert value["followup_intent"] == "dimension_override"
    assert value["resolved_context"]["dimensions"] == ["category"]


def test_time_override():
    resolver, unused = _resolver(); value = resolver.resolve(u"换成最近30天")
    assert value["followup_intent"] == "time_override"
    assert value["resolved_context"]["time_range"] == "last_30_days"


def test_comparison_request_overrides_task_type():
    resolver, unused = _resolver(); value = resolver.resolve(u"和上周比")
    assert value["followup_intent"] == "comparison_request"
    assert value["resolved_context"]["task_type"] == "comparison"
    assert value["resolved_context"]["compare_to"] == "previous_week"


def test_explain_more_inherits_context():
    resolver, unused = _resolver(); value = resolver.resolve(u"解释一下")
    assert value["followup_intent"] == "explain_more"
    assert value["resolved_context"]["metric"] == "gmv"
    assert value["decision"] == "reuse_verified_evidence"


def test_new_topic_does_not_inherit():
    resolver, unused = _resolver(); value = resolver.resolve(u"看用户留存")
    assert value["followup_intent"] == "new_topic"
    assert value["is_follow_up"] is False
    assert "context:" not in value["resolved_query"]


def test_metric_change_inherits_context_but_reexecutes():
    resolver, unused = _resolver(); value = resolver.resolve(u"换成订单数")
    assert value["is_follow_up"] is True
    assert value["resolved_context"]["metric"] == "order_count"
    assert value["resolved_context"]["time_range"] == "last_7_days"
    assert value["decision"] == "inherit_and_reexecute"
    assert "metric" in value["decision_reasons"][0]


def test_ambiguous_continue_needs_clarification():
    resolver, unused = _resolver(); value = resolver.resolve(u"继续看一下")
    assert value["decision"] == "need_clarification"
    assert value["clarification"]["reason"] == "ambiguous_followup_missing_object"


def test_failed_history_blocks_followup():
    resolver, memory = _resolver()
    memory.remember("session", "last_result", {"task_id": "task-failed", "status": "error",
                                               "metric": "gmv", "time_range": "last_7_days"})
    value = resolver.resolve(u"解释一下")
    assert value["decision"] == "blocked"
    assert value["decision_reasons"] == ["previous_task_not_successful"]


def test_verified_evidence_ttl_expired_reuses_context_only():
    resolver, memory = _resolver()
    memory.remember("session", "last_result", {
        "task_id": "task-old", "status": "ok", "metric": "gmv",
        "dimensions": ["date"], "filters": {}, "time_range": "last_7_days",
        "task_type": "descriptive", "fact_ledger": {"authority": "verified_execution",
        "verified": True, "evidence_refs": ["ev-old"], "captured_at": time.time() - 999999}})
    value = resolver.resolve(u"解释一下")
    assert value["decision"] == "reuse_context_only"



def _resolver_with_evidence_bus(record_overrides=None, ttl_seconds=300):
    memory = MemoryStore()
    now = time.time()
    memory.remember("session", "last_result", {
        "task_id": "task-bus", "status": "ok", "metric": "gmv",
        "dimensions": ["date"], "filters": {}, "time_range": "last_7_days",
        "task_type": "descriptive", "dataid": "orders", "data_version": "v1",
        "fact_ledger": {"authority": "verified_execution", "verified": True,
                        "evidence_refs": ["ev-bus"], "captured_at": now,
                        "dataid": "orders", "data_version": "v1"}})
    metadata = {"metric": "gmv", "dimensions": ["date"], "filters": {}}
    envelope_data = {
        "status": "ok", "stage": "db_execute", "query_id": "q-bus", "evidence_id": "ev-bus",
        "dataid": "orders", "data_version": "v1", "row_count": 1, "time_range": "last_7_days",
        "authority": "verified_execution", "metadata": metadata,
    }
    for key, value in (record_overrides or {}).items():
        if key == "metadata":
            envelope_data["metadata"].update(value)
        else:
            envelope_data[key] = value
    bus = EvidenceBus()
    bus.record_envelope(build_execution_envelope(**envelope_data), producer_task_id="data_analyst", trace_id="trace-bus")
    return FollowupContextResolver(memory, ClarificationStateMachine(), "r22-bus", evidence_bus=bus,
                                   evidence_ttl_seconds=ttl_seconds), memory, bus


def test_explain_more_reuses_evidence_only_when_bus_scope_matches():
    resolver, unused_memory, unused_bus = _resolver_with_evidence_bus()
    value = resolver.resolve(u"解释一下")
    assert value["decision"] == "reuse_verified_evidence"
    assert value["decision_reasons"] == ["scope_compatible_and_evidence_fresh"]


def test_explain_more_downgrades_when_bus_evidence_scope_mismatches():
    resolver, unused_memory, unused_bus = _resolver_with_evidence_bus({"metadata": {"metric": "orders"}})
    value = resolver.resolve(u"解释一下")
    assert value["decision"] == "reuse_context_only"
    assert value["decision_reasons"] == ["evidence_scope_mismatch:metric"]


def test_explain_more_downgrades_when_bus_evidence_ref_missing():
    resolver, unused_memory, bus = _resolver_with_evidence_bus()
    bus.records.pop("ev-bus")
    value = resolver.resolve(u"解释一下")
    assert value["decision"] == "reuse_context_only"
    assert value["decision_reasons"] == ["missing_evidence_ref"]


def test_explain_more_downgrades_when_bus_evidence_ttl_expired():
    resolver, unused_memory, bus = _resolver_with_evidence_bus(ttl_seconds=1)
    bus.records["ev-bus"]["recorded_at"] = time.time() - 999
    value = resolver.resolve(u"解释一下")
    assert value["decision"] == "reuse_context_only"
    assert value["decision_reasons"] == ["evidence_ttl_expired"]


def test_no_history_is_not_followup():
    value = FollowupContextResolver(MemoryStore(), ClarificationStateMachine(), "empty").resolve(u"只看淘宝")
    assert value["is_follow_up"] is False


def test_pending_clarification_blocks_normal_followup():
    memory = MemoryStore(); machine = ClarificationStateMachine()
    machine.begin("pending", u"看GMV", {"clarification": {"options": []}}, task_id="t")
    value = FollowupContextResolver(memory, machine, "pending").resolve(u"按渠道拆一下")
    assert value["blocked_by_pending_clarification"] is True


def test_facade_records_history_and_trace():
    from agent_facade import AgentFacade
    facade = AgentFacade(session_id="r22-facade")
    first = facade.ask(u"最近7天GMV")
    result = facade.follow_up(u"按渠道拆一下")
    assert result["follow_up_context"]["is_follow_up"] is True
    assert result["follow_up_context"]["decision"] == "inherit_and_reexecute"
    assert result["parent_task_id"] == first["task_id"]
    assert facade.get_task_history()[-1]["followup_intent"] == "drill_down"
    assert "followup_context_resolved" in [x["name"] for x in facade.get_trace(result["trace_id"])]


def test_pending_clarification_accepts_natural_language_option():
    from agent_facade import AgentFacade
    facade = AgentFacade(session_id="r22-natural-choice")
    facade.clarification_state.begin(facade.session_id, u"看GMV", {
        "clarification": {"question": u"请选择分析口径", "options": [
            {"id": "metric_query", "label": u"整体数值", "description": u"直接看汇总数据"},
            {"id": "breakdown", "label": u"按维度拆分", "description": u"按维度拆分查看"},
        ]}}, task_id="pending-1")
    captured = []
    facade.resume_clarification = lambda choice: captured.append(choice) or {"status": "ok"}
    assert facade.follow_up(u"我想看整体数值")["status"] == "ok"
    assert captured == ["metric_query"]


def test_pending_clarification_is_preserved_for_unrelated_text():
    from agent_facade import AgentFacade
    facade = AgentFacade(session_id="r22-preserve-pending")
    facade.clarification_state.begin(facade.session_id, u"看GMV", {
        "clarification": {"question": u"请选择分析口径", "options": [
            {"id": "metric_query", "label": u"整体数值"},
        ]}}, task_id="pending-2")
    result = facade.follow_up(u"什么都没有")
    assert result["status"] == "need_clarification"
    assert result["clarification_session"]["pending"] is True


def test_meta_question_returns_bounded_recap_without_new_query():
    from agent_facade import AgentFacade
    facade = AgentFacade(session_id="r22-recap")
    facade.memory.remember("session", "last_result", {
        "metric": "gmv", "dimensions": ["channel"], "results": [{"gmv": 100}],
        "insight": {"summary": u"GMV 为 100"}})
    result = facade.follow_up(u"什么都没有")
    assert result["intent"] == "conversation_recap"
    # Historical generated prose/raw rows are never replayed as facts. The
    # recap is rebuilt exclusively from safe typed state retained in session.
    assert u"指标=gmv" in result["insight"]["summary"]
    assert u"GMV 为 100" not in result["insight"]["summary"]
