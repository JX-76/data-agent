# -*- coding: utf-8 -*-
"""Phase 3A contracts for controlled routing, tool plans and DAG traces.

This module is intentionally small and additive. It does not execute business
logic; it only standardizes route/tool/DAG metadata so AgentFacade and governed
external tools can share a stable contract.
"""
from __future__ import unicode_literals

TERMINAL_STATUSES = ("ok", "blocked", "need_clarification", "fallback", "pending_human_review", "error", "unsupported", "no_answer")
DAG_NODES = ("route", "precheck", "tool_call", "execute", "analyze", "report")


def _safe_status(status):
    return status if status in TERMINAL_STATUSES else "error"


def build_route_decision(plan, query=None):
    plan = plan or {}
    diagnostics = plan.get("diagnostics") or {}
    execution_mode = plan.get("execution_mode") or "plan_act"
    task_type = plan.get("task_type") or "descriptive"
    status = _safe_status(plan.get("status") or "ok")
    tooling_required = execution_mode == "react"
    dag_required = execution_mode == "react" or bool(plan.get("sub_plans"))
    route_path = diagnostics.get("route_path") or plan.get("source") or "unknown"
    reason = diagnostics.get("execution_mode_reason") or diagnostics.get("reason") or "business_intent_policy"
    return {
        "contract": "route_decision_v1",
        "query": query or plan.get("query"),
        "route_path": route_path,
        "execution_mode": execution_mode,
        "tooling_required": bool(tooling_required),
        "dag_required": bool(dag_required),
        "routing_reason": reason,
        "confidence": plan.get("confidence"),
        "task_type": task_type,
        "status": status,
    }


def build_tool_invocation_plan(tool_id, arguments=None, spec=None, context=None, policy_decision=None):
    spec = spec or {}
    context = context or {}
    policy_decision = policy_decision or {}
    return {
        "contract": "tool_invocation_plan_v1",
        "tool_id": tool_id,
        "arguments": dict(arguments or {}),
        "risk_level": spec.get("risk_level") or "low",
        "requires_human_review": bool(spec.get("requires_human_review")),
        "allowed_intents": list(spec.get("allowed_intents") or []),
        "policy_decision": dict(policy_decision),
        "side_effect": spec.get("side_effect") or "unknown",
        "trace_id": context.get("trace_id"),
    }


def build_controlled_dag_plan(route_decision=None, include_tool_call=None):
    route_decision = route_decision or {}
    nodes = list(DAG_NODES)
    if include_tool_call is False and "tool_call" in nodes:
        nodes.remove("tool_call")
    return {
        "contract": "controlled_dag_plan_v1",
        "mode": "controlled",
        "nodes": nodes,
        "entry_node": "route",
        "terminal_statuses": list(TERMINAL_STATUSES),
        "route_decision": route_decision,
    }


def build_dag_trace_event(node, status="ok", reason=None, metadata=None):
    return {
        "contract": "controlled_dag_trace_event_v1",
        "node": node,
        "status": _safe_status(status),
        "reason": reason,
        "metadata": dict(metadata or {}),
    }


def annotate_plan_with_phase3a(plan, query=None):
    data = dict(plan or {})
    diagnostics = dict(data.get("diagnostics") or {})
    route_decision = build_route_decision(data, query=query)
    dag_plan = build_controlled_dag_plan(route_decision, include_tool_call=route_decision.get("tooling_required"))
    diagnostics["route_decision"] = route_decision
    diagnostics["controlled_dag"] = dag_plan
    data["diagnostics"] = diagnostics
    data.setdefault("route_decision", route_decision)
    data.setdefault("controlled_dag", dag_plan)
    return data


__all__ = [
    "TERMINAL_STATUSES", "DAG_NODES", "build_route_decision",
    "build_tool_invocation_plan", "build_controlled_dag_plan",
    "build_dag_trace_event", "annotate_plan_with_phase3a",
]
