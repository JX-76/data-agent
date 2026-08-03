# -*- coding: utf-8 -*-
"""Agent facade for the Data Agent mainline.

This module provides the primary entry point for user queries.
It orchestrates governance, routing, planning, execution, analysis,
charting, insight generation, and reporting.

Phase 12: Stage-split ask() into 5 independent stages with
timeout, circuit breaker, and result cache support.
"""

import time
import uuid

try:
    text_type = unicode
except NameError:  # pragma: no cover - Python 3
    text_type = str


def _safe_text(value):
    """Normalize external/session values without Python 2 ASCII coercion."""
    if value is None:
        return u""
    if isinstance(value, text_type):
        return value
    try:
        return value.decode("utf-8")
    except Exception:
        try:
            return value.decode("mbcs")
        except Exception:
            return text_type(value)


from governance import Governance
from risk_policy import RiskPolicy
from human_gate import HumanGatePolicy
from prompt_registry import PromptRegistry
from memory_store import MemoryStore
from observability import Observer
from contracts import NON_OK_TERMINAL_STATUSES, normalize_analysis_plan, normalize_result
from report_generator import generate_product_report
from execution_engine import ExecutionEngine
from analysis_strategies import AnalysisStrategyRegistry
from analysis_output import standardize_analysis_output
from answer_contracts import build_answer_envelope, build_final_answer_contract
from quality_scoring import score_answer_quality

from strategy_service import StrategyService
from subagent_runtime import get_subagent_runtime
from timeout_guard import TimeoutGuard
from result_cache import ResultCache, CacheScope
from circuit_breaker import CircuitBreaker, CircuitBreakerOpenError
from task_decomposer import TaskDecomposer
from result_merger import ResultMerger
from task_anchor import TaskAnchor
from memory_contracts import EvidenceCard, AUTHORITY_VERIFIED
from memory_policy import MemoryPolicy
from react_observation import ReActObservationGovernor
from react_loop_runtime import ControlledReactLoop
from clarification_state import ClarificationStateMachine
from credibility import build_credibility
from provenance import build_provenance
from agent_capability import capability_catalog, business_coverage
from agent_monitoring import AgentMonitoring
from human_review_state import HumanReviewStateMachine
from permission_policy import AccessContext
from masking_policy import sanitize_agent_payload, sanitize_text
from audit_contract import build_governance_audit_event
from phase3a_runtime import annotate_plan_with_phase3a, build_dag_trace_event
from rag_governance import (ClaimEvidenceAuditor, ClaimScopeBuilder,
                            HistoricalEvidencePolicy, IdempotencyKeyBuilder,
                            PromptContextCompiler, TaskStateLedger)
from rag_retriever import RagService
from context import SessionContextCompressor
from evidence_bus import EvidenceBus
from claim_graduation import audit_final_answer_claims




def _new_id():
    return str(uuid.uuid4())


def _new_correlation_id():
    """Return an opaque correlation id that cannot resemble a phone number.

    UUIDs are otherwise safe under the recursive masker, but their leading
    digits may accidentally satisfy a consumer's broad PII scan.  Prefixing
    preserves trace/replay uniqueness while making the identifier unambiguous.
    """
    return "id_" + str(uuid.uuid4())


# Default stage timeouts in seconds
_STAGE_TIMEOUTS = {
    "governance": 10,
    "planning": 30,
    "execution": 60,
    "analysis": 30,
    "reporting": 10,
}

# Max steps to prevent infinite loops
_MAX_STEPS = 20

# ReAct uses a separate bounded exploration budget.
_REACT_MAX_STEPS = 2


class AgentFacade(object):
    """Primary entry point for user queries.

    Usage:
        facade = AgentFacade()
        result = facade.ask("最近7天GMV")
        trace = facade.get_trace()
    """

    def __init__(self, executor=None, governance=None, risk_policy=None,
                 human_gate=None, prompt_registry=None, memory=None,
                 observer=None, session_id=None, strategies=None,
                  result_cache=None, circuit_breaker=None,
                   decomposer=None, merger=None, memory_policy=None,
                    clarification_state=None, human_review_state=None,
                    monitoring=None, access_context=None, rag_service=None,
                    evidence_store=None):
        # A runnable local sandbox is the safe product default.  Tests and
        # deployments may still inject a different readonly executor.
        if executor is None:
            from db_factory import build_query_executor
            executor = build_query_executor()
        self.executor = executor
        self.governance = governance or Governance()
        self.risk_policy = risk_policy or RiskPolicy()
        self.prompt_registry = prompt_registry or PromptRegistry()
        self.memory = memory or MemoryStore()
        self.observer = observer or Observer()
        self.session_id = session_id or _new_id()
        self.analysis_strategies = AnalysisStrategyRegistry()

        self.strategies = strategies or StrategyService()
        self.human_gate = HumanGatePolicy(self.risk_policy)
        self.subagent_runtime = get_subagent_runtime()
        self._trace_id = None
        self._task_id = None
        self.result_cache = result_cache or ResultCache(max_size=100, ttl_seconds=300)
        self.circuit_breaker = circuit_breaker or CircuitBreaker(
            name="agent_facade",
            failure_threshold=5,
            recovery_timeout=30.0,
        )
        self.decomposer = decomposer or TaskDecomposer(max_metrics=3, max_dimensions=2)
        self.merger = merger or ResultMerger(strategy="auto")
        self.memory_policy = memory_policy or MemoryPolicy()
        self.task_anchor = None
        self.react_governor = ReActObservationGovernor(
            memory_policy=self.memory_policy, observer=self.observer)
        self.clarification_state = clarification_state or ClarificationStateMachine()
        self.human_review_state = human_review_state or HumanReviewStateMachine()
        self.monitoring = monitoring or AgentMonitoring(self.observer)
        self.access_context = AccessContext.from_value(access_context, fallback=self.session_id).to_dict()
        self.rag_prompt_compiler = PromptContextCompiler()
        self.historical_evidence_policy = HistoricalEvidencePolicy()
        self.task_state_ledger = TaskStateLedger()
        self.claim_scope_builder = ClaimScopeBuilder()
        self.idempotency_key_builder = IdempotencyKeyBuilder()
        self.claim_auditor = ClaimEvidenceAuditor()
        # RAG is a governed reference subsystem. If production embedding is not
        # available, it degrades explicitly rather than fabricating evidence.
        self.rag_service = rag_service or RagService(allow_degraded=True)
        # Bounded multi-turn context: never retain generated prose as session facts.
        self.session_context = SessionContextCompressor(max_recent_turns=4, max_tokens=900)
        # Session-local verified execution registry. Follow-up resolution may use
        # session typed state as context, but reusable evidence must be validated
        # against this bus for existence, TTL and scope compatibility.  A
        # persistent store may hydrate it after process/session restoration.
        self.evidence_store = evidence_store
        self.evidence_bus = EvidenceBus(self._load_persisted_evidence())



    def _prompt_chain_ids(self, chain_name='default'):
        return [item.get('prompt_id') for item in self.prompt_registry.chain_spec(chain_name)]

    def _attach_chain_metadata(self, plan, chain_name='safety'):
        diagnostics = dict(plan.get('diagnostics') or {}) if isinstance(plan, dict) else {}
        diagnostics['prompt_chain'] = self._prompt_chain_ids(chain_name)
        diagnostics['prompt_specs'] = self.prompt_registry.chain_spec(chain_name)
        if isinstance(plan, dict):
            plan['diagnostics'] = diagnostics
            plan.setdefault('prompt_chain', diagnostics['prompt_chain'])
            plan.setdefault('prompt_specs', diagnostics['prompt_specs'])
            plan.setdefault('sandbox', self.subagent_runtime.describe('planner', task_id=self._task_id, parent_task_id=None))
        return diagnostics

    def ask(self, query, use_llm=False, access_context=None, analysis_method=None):
        """Stage-split ask() with timeout, circuit breaker, and result cache.

        Stages:
            1. governance  - check query safety
            2. planning    - route + plan + risk + human_gate
            3. execution   - execute SQL
            4. analysis    - analyze + chart + insight
            5. reporting   - generate report
        """
        self._trace_id = _new_correlation_id()
        self._task_id = _new_correlation_id()
        self._record_evidence_store_load_snapshot()
        t0 = time.time()
        step_count = 0

        effective_access_context = AccessContext.from_value(
            access_context if access_context is not None else self.access_context,
            fallback=self.session_id).to_dict()
        # Include caller authorization scope in cache isolation.
        conversation_context = self.session_context.build(query)
        provisional_resolved_query = self._resolve_multiturn_query(query, conversation_context)
        # Cache identity is the explicit user turn plus permission scope.  The
        # multi-turn resolver may append typed context after the first request;
        # using that enriched text as the key would make identical explicit
        # turns miss their own cache entry while still not adding evidence.
        cache_query = query
        cache_scope = CacheScope.from_context(
            query=cache_query,
            plan_hash="pre_plan",
            access_context=effective_access_context,
        )
        # Check result cache first. The key is scope-hashed; raw user text,
        # tenant permissions and masking policy are never logged as cache keys.
        cached, cache_decision = self.result_cache.get(
            cache_query, plan_hash="pre_plan", scope=cache_scope, return_decision=True)
        # Do not record cache misses before governance: trace consumers and
        # replay gates expect the first mainline event to be the policy decision.
        # Cache hits are terminal fast-paths and are recorded below.
        if cached is not None:
            self._record_cache_decision(cache_decision)
            cached["from_cache"] = True
            cached["trace_id"] = self._trace_id
            cached.setdefault("diagnostics", {})["cache_decision"] = cache_decision
            return self._finalize(query, cached, t0)

        self.memory.remember("session", "last_query", query, session_id=self.session_id, task_id=self._task_id)

        # Stage 1: Governance
        ctx = {"query": query, "t0": t0, "use_llm": use_llm,
               "analysis_method": analysis_method,
               "access_context": effective_access_context,
               "conversation_context": conversation_context,
               "resolved_query": provisional_resolved_query}
        stages = [
            ("governance", self._stage_governance),
            ("rag", self._stage_rag_context),
            ("planning", self._stage_planning),
            ("decompose", self._stage_decompose),
            ("execution", self._stage_execution),
            ("analysis", self._stage_analysis),
            ("reporting", self._stage_reporting),
        ]


        for stage_name, stage_fn in stages:
            step_count += 1
            if step_count > _MAX_STEPS:
                ctx["status"] = "error"
                ctx.setdefault("errors", []).append({"stage": stage_name, "error": "max_steps_exceeded"})
                break

            timeout = _STAGE_TIMEOUTS.get(stage_name, 30)
            try:
                with TimeoutGuard(timeout, stage_name) as guard:
                    ctx = self.circuit_breaker.call(stage_fn, ctx)
                if guard.timed_out:
                    ctx["status"] = "error"
                    ctx.setdefault("errors", []).append({"stage": stage_name, "error": "timeout"})
                    self._record_stage_error(stage_name, "timeout", ctx)
                    break
            except CircuitBreakerOpenError as e:
                ctx["status"] = "error"
                ctx.setdefault("errors", []).append({"stage": stage_name, "error": str(e)})
                self._record_stage_error(stage_name, "circuit_breaker_open", ctx)
                break
            except Exception as e:
                ctx["status"] = "error"
                ctx.setdefault("errors", []).append({"stage": stage_name, "error": str(e)})
                self._record_stage_error(stage_name, str(e), ctx)
                break

            # Early exit for terminal statuses
            if ctx.get("status") in NON_OK_TERMINAL_STATUSES:
                break

        # Cache only verified successful results inside the same pre-execution
        # permission scope used for lookup. ResultCache.cacheable() still
        # rejects evidence-limited/unverified results; deployments with an
        # externally known data_version may include it in access_context.
        if ctx.get("status") == "ok":
            cache_store_scope = CacheScope.from_context(
                query=cache_query,
                plan_hash="pre_plan",
                access_context=effective_access_context,
            )
            store_decision = self.result_cache.set(
                cache_query, ctx, plan_hash="pre_plan", scope=cache_store_scope)
            ctx.setdefault("diagnostics", {})["cache_decision"] = store_decision
            self._record_cache_decision(store_decision)

        return self._finalize(query, ctx, t0)

    def _record_dag_node(self, node, status="ok", reason=None, metadata=None):
        """Record a Phase 3C DAG node event without changing mainline behavior."""
        event = build_dag_trace_event(node, status=status, reason=reason, metadata=metadata or {})
        self.observer.record(
            "dag_node",
            trace_id=self._trace_id,
            status=event.get("status", status),
            stage=node,
            task_id=self._task_id,
            session_id=self.session_id,
            metadata=event,
        )
        return event

    def _record_cache_decision(self, decision):
        """Record cache decisions without exposing raw query or permission values."""
        if not self.observer or not decision:
            return
        self.observer.record(
            "result_cache",
            trace_id=self._trace_id,
            status=decision.get("action") or "unknown",
            stage="cache",
            failure_type=None if decision.get("action") in ("hit", "miss", "store") else decision.get("reason"),
            task_id=self._task_id,
            session_id=self.session_id,
            metadata=decision,
        )

    def _record_stage_error(self, stage_name, error, ctx):
        """Record a stage error to the observer."""
        if self.observer:
            self.observer.record(
                stage_name,
                trace_id=self._trace_id,
                status="error",
                stage=stage_name,
                failure_type=error,
                task_id=self._task_id,
                session_id=self.session_id,
            )

    def _stage_governance(self, ctx):
        """Stage 1: Governance check."""
        query = ctx["query"]
        access_context = ctx.get("access_context") or self.access_context
        gov = self.governance.check_query(query, identity=access_context.get("quota_key") or self.session_id,
                                          trace_id=self._trace_id)
        gov_data = gov.to_dict() if hasattr(gov, "to_dict") else {
            "allowed": getattr(gov, "allowed", True),
            "reason": getattr(gov, "reason", ""),
            "action": getattr(gov, "action", "allow"),
            "metadata": getattr(gov, "metadata", {}),
        }
        self.observer.record(
            "governance",
            trace_id=self._trace_id,
            status="ok" if gov_data.get("allowed") else "blocked",
            stage="governance",
            failure_type=None if gov_data.get("allowed") else gov_data.get("decision_type") or gov_data.get("action"),
            task_id=self._task_id,
            session_id=self.session_id,
            metadata=gov_data,
        )
        self._record_dag_node("precheck", "ok" if gov_data.get("allowed") else "blocked",
                              None if gov_data.get("allowed") else gov_data.get("decision_type") or gov_data.get("action"),
                              {"policy": gov_data.get("policy_id")})
        if not gov_data.get("allowed", True):
            ctx["status"] = "blocked"
            ctx["intent"] = "blocked"
            ctx["blocked_reason"] = gov_data.get("reason")
            ctx["errors"] = []
            ctx["diagnostics"] = {"governance": gov_data}
            ctx["task_id"] = self._task_id
            return ctx
        ctx["gov_data"] = gov_data
        return ctx

    def _stage_rag_context(self, ctx):
        """Stage 1.5: retrieve governed RAG evidence for planning/reporting."""
        raw_query = ctx.get("query", "")
        previous = ctx.get("conversation_context") or {}
        resolved_query = self._resolve_multiturn_query(raw_query, previous)
        ctx["resolved_query"] = resolved_query
        access_context = dict(ctx.get("access_context") or self.access_context)
        access_context.setdefault("session_id", self.session_id)
        rag_idempotency = self.idempotency_key_builder.build(
            tenant_id=access_context.get("tenant_id"), session_id=self.session_id,
            stage="rag_retrieval", input_value={"query": resolved_query,
            "raw_query": raw_query, "access_scope": access_context.get("role")},
            policy_version="rag_v2")
        ctx["rag_idempotency"] = rag_idempotency
        try:
            rag_context = self.rag_service.retrieve_analysis_context(
                resolved_query, access_context=access_context, previous_context=previous)
        except Exception as exc:
            rag_context = {"status": "unavailable", "query": resolved_query,
                           "evidence": [], "citations": [], "confidence": 0.0,
                           "decision": "no_answer", "notes": [str(exc)[:300]]}
        rag_context["raw_query"] = raw_query
        rag_context["resolved_query"] = resolved_query
        ctx["rag_context"] = rag_context
        ctx["rag_evidence"] = list(rag_context.get("evidence") or [])
        notes = list(rag_context.get("notes") or [])
        risk_notes = set(["query_out_of_governed_corpus_scope",
                          "query_requests_sensitive_or_private_data",
                          "query_requests_destructive_or_internal_operation"])
        hard_no_answer = bool(risk_notes.intersection(set(notes)))
        # RAG has authority over governed knowledge scope, not over whether the
        # agent may run a supported SQL/tool analysis.  Missing RAG evidence must
        # degrade to planning without reference context; only explicit scope,
        # sensitive-data, or destructive-operation risks become terminal.
        if rag_context.get("decision") == "no_answer" and hard_no_answer:
            ctx["status"] = "unsupported"
            ctx["intent"] = "unsupported"
            ctx["blocked_reason"] = "rag_answerability_gate"
            ctx["diagnostics"] = {"rag_context": {
                "status": rag_context.get("status"),
                "decision": rag_context.get("decision"),
                "confidence": rag_context.get("confidence"),
                "notes": notes,
                "raw_query": raw_query,
                "resolved_query": resolved_query,
                "no_evidence": not bool(ctx["rag_evidence"]),
            }}
            ctx["task_id"] = self._task_id
        self.observer.record(
            "rag_retrieval",
            trace_id=self._trace_id,
            status=rag_context.get("status", "no_answer"),
            task_id=self._task_id,
            session_id=self.session_id,
            metadata={"evidence_count": len(ctx["rag_evidence"]),
                      "confidence": rag_context.get("confidence"),
                      "decision": rag_context.get("decision"),
                      "resolved_query": resolved_query,
                      "idempotency_key": rag_idempotency.get("idempotency_key"),
                      "notes": rag_context.get("notes") or []},
        )
        return ctx

    def _stage_planning(self, ctx):
        """Stage 2: Route + Plan + Risk + Human Gate."""
        query = ctx["query"]
        use_llm = ctx.get("use_llm", False)
        resolved_query = ctx.get("resolved_query") or query

        plan = self._route(resolved_query, use_llm=use_llm)
        plan = normalize_analysis_plan(plan, query=resolved_query, task_id=self._task_id)
        # Analysis methods are controlled product presets, not free-form prompts.
        # Keep the selection in plan diagnostics so it is traceable and can be
        # consumed by reporting/LLM prompt compilation without bypassing evidence.
        method = ctx.get("analysis_method")
        if method:
            diagnostics = dict(plan.get("diagnostics") or {})
            diagnostics["analysis_method"] = method
            # normalize_analysis_plan returns an AnalysisPlan object in the
            # runtime path, so mutate through its typed attributes instead of
            # dict item assignment. Keep dict compatibility for test doubles.
            if isinstance(plan, dict):
                plan["diagnostics"] = diagnostics
                plan["analysis_method"] = method
            else:
                plan.diagnostics = diagnostics
                try:
                    plan.analysis_method = method
                except Exception:
                    pass
        rag_context = ctx.get("rag_context") or {}
        if isinstance(plan, dict):
            diagnostics = dict(plan.get("diagnostics") or {})
            diagnostics["rag_context"] = {
                "status": rag_context.get("status"),
                "decision": rag_context.get("decision"),
                "confidence": rag_context.get("confidence"),
                "evidence_count": len(rag_context.get("evidence") or []),
                "citations": rag_context.get("citations") or [],
                "usage_policy": ((rag_context.get("context_pack") or {}).get("usage_policy") or {}),
            }
            plan["diagnostics"] = diagnostics
            plan["rag_evidence"] = list(rag_context.get("evidence") or [])
        self._record_dag_node("route", plan.get("status", "ok"), None,
                              {"intent": plan.get("intent"), "task_type": plan.get("task_type")})
        access = self.governance.check_access(ctx.get("access_context"), plan=plan, query=query)
        access_data = access.to_dict() if hasattr(access, "to_dict") else {}
        self._audit_governance(ctx, access_data)
        self.observer.record("permission_policy", trace_id=self._trace_id,
                             status=access_data.get("action", "allowed"), task_id=self._task_id,
                             session_id=self.session_id, metadata=access_data)
        # Approval must never be a route to bypass output masking.  A request
        # explicitly demanding unmasked PII is denied before it can enter a
        # review queue; a reviewer can approve a legitimate sensitive export,
        # but the response boundary stays masked in every case.
        query_text = query or ""
        refuses_masking = any(token in query_text for token in
                              (u"不要脱敏", u"不脱敏", u"取消脱敏", u"完整手机号", u"原始手机号"))
        # PermissionDecision serializes masking scope below metadata; support
        # both forms so adapters with a flattened contract remain compatible.
        masked_scope = (access_data.get("masked_fields") or
                        (access_data.get("metadata") or {}).get("masked_fields") or [])
        if masked_scope and refuses_masking:
            ctx.update(plan.to_dict() if hasattr(plan, "to_dict") else dict(plan))
            ctx["status"] = "blocked"
            ctx["intent"] = "blocked"
            ctx["blocked_reason"] = "raw_sensitive_data_not_allowed"
            ctx["diagnostics"] = {"governance": access_data}
            ctx["task_id"] = self._task_id
            return ctx
        if access_data.get("action") == "pending_human_review":
            ctx.update(plan.to_dict() if hasattr(plan, "to_dict") else dict(plan))
            ctx["status"] = "pending_human_review"
            ctx["requires_human_review"] = True
            ctx["approval_status"] = "pending"
            ctx["reason"] = access_data.get("reason")
            ctx["diagnostics"] = {"governance": access_data}
            ctx["task_id"] = self._task_id
            return ctx
        if not access_data.get("allowed", True):
            ctx.update(plan.to_dict() if hasattr(plan, "to_dict") else dict(plan))
            ctx["status"] = "blocked"
            ctx["blocked_reason"] = access_data.get("reason")
            ctx["diagnostics"] = {"governance": access_data}
            ctx["task_id"] = self._task_id
            return ctx
        plan = self.governance.inject_tenant_filter(plan.to_dict() if hasattr(plan, "to_dict") else plan,
                                                     ctx.get("access_context"))
        plan = normalize_analysis_plan(plan, query=query, task_id=self._task_id)
        # Phase 3A is additive metadata: retain the canonical plan while
        # exposing controlled route/DAG intent to downstream observability.
        phase3a_data = annotate_plan_with_phase3a(plan.to_dict(), query=query)
        plan = normalize_analysis_plan(phase3a_data, query=query, task_id=self._task_id)
        # AnalysisPlan is typed in the normal runtime, so attach RAG metadata
        # after all normalization/replanning steps instead of losing it in an
        # intermediate dict conversion.
        rag_diagnostics = {
            "status": rag_context.get("status"),
            "decision": rag_context.get("decision"),
            "confidence": rag_context.get("confidence"),
            "evidence_count": len(rag_context.get("evidence") or []),
            "citations": rag_context.get("citations") or [],
            "usage_policy": ((rag_context.get("context_pack") or {}).get("usage_policy") or {}),
        }
        if isinstance(plan, dict):
            plan.setdefault("diagnostics", {})["rag_context"] = rag_diagnostics
            plan["rag_evidence"] = list(rag_context.get("evidence") or [])
        else:
            plan.diagnostics = dict(plan.get("diagnostics") or {})
            plan.diagnostics["rag_context"] = rag_diagnostics
            try:
                plan.rag_evidence = list(rag_context.get("evidence") or [])
            except Exception:
                pass
        ctx["permission_decision"] = access_data
        self.memory.remember(
            "session",
            "last_plan",
            plan.to_dict() if hasattr(plan, "to_dict") else dict(plan),
            session_id=self.session_id,
            task_id=self._task_id,
        )
        if plan.get("status") in ("blocked", "unsupported"):
            ctx.update(plan)
            return ctx
        if plan.get("execution_mode") == "react" and plan.get("status") != "ok":
            if isinstance(plan, dict):
                diagnostics = plan.setdefault("diagnostics", {})
            else:
                diagnostics = dict(plan.get("diagnostics") or {})
                try:
                    plan.diagnostics = diagnostics
                except Exception:
                    pass
            diagnostics["react_selected"] = True
            diagnostics["react_policy"] = "controlled_exploration_candidate"
            diagnostics["react_runtime"] = "terminal_status_no_execution"
            self.observer.record(
                "react_selected",
                trace_id=self._trace_id,
                status="terminal",
                task_id=self._task_id,
                session_id=self.session_id,
                metadata={"reason": diagnostics.get("execution_mode_reason"), "policy": diagnostics.get("react_policy")},
            )

        self.observer.record(
            "plan",
            trace_id=self._trace_id,
            status=plan.get("status", "ok"),
            intent=plan.get("intent"),
            metric=plan.get("metric"),
            source=plan.get("source"),
            task_id=self._task_id,
        )
        self._attach_chain_metadata(plan)
        risk = self.risk_policy.assess(query, plan)
        self.observer.record(
            "risk_assessed",
            trace_id=self._trace_id,
            status="ok",
            task_id=self._task_id,
            session_id=self.session_id,
            metadata={
                "risk_level": risk.level,
                "requires_human_review": risk.requires_human_review,
                "execution_mode": plan.get("execution_mode") if isinstance(plan, dict) else getattr(plan, "execution_mode", None),
            },
        )
        gate = self.human_gate.evaluate(query, plan, risk)
        self.observer.record(
            "human_gate",
            trace_id=self._trace_id,
            status=gate.approval_status,
            task_id=self._task_id,
            session_id=self.session_id,
            metadata={
                "approval_status": gate.approval_status,
                "requires_human_review": gate.requires_human_review,
                "risk_level": gate.risk_level,
            },
        )
        if gate.requires_human_review:

            pending = dict(plan.to_dict() if hasattr(plan, "to_dict") else plan)
            diagnostics = dict(pending.get("diagnostics") or {})
            diagnostics["risk"] = risk.to_dict() if hasattr(risk, "to_dict") else risk
            diagnostics["human_gate"] = gate.to_dict() if hasattr(gate, "to_dict") else gate
            diagnostics["prompt_chain"] = self._prompt_chain_ids("safety")
            diagnostics["prompt_specs"] = self.prompt_registry.chain_spec("safety")
            diagnostics["sandbox"] = self.subagent_runtime.describe("planner", task_id=self._task_id, parent_task_id=None)
            pending["diagnostics"] = diagnostics
            pending["status"] = "pending_human_review"
            pending["intent"] = pending.get("intent") or "clarification"
            pending["approval_status"] = gate.approval_status
            pending["requires_human_review"] = True
            pending["risk_level"] = risk.level
            pending["review_checklist"] = list(gate.review_checklist)
            pending["reason"] = gate.reason
            pending["prompt_chain"] = diagnostics["prompt_chain"]
            pending["prompt_specs"] = diagnostics["prompt_specs"]
            pending["sandbox"] = diagnostics["sandbox"]
            pending["human_gate"] = gate.to_dict() if hasattr(gate, "to_dict") else gate
            pending["human_review_session"] = self.human_review_state.begin(
                self.session_id, query, pending, risk_level=risk.level,
                task_id=self._task_id, checklist=gate.review_checklist)
            ctx.update(pending)
            ctx["task_id"] = self._task_id
            return ctx
        if plan.get("clarification"):
            pending = self.clarification_state.begin(
                self.session_id, query, plan, task_id=self._task_id)
            self.memory.remember(
                "session",
                "last_clarification",
                plan.get("clarification"),
                session_id=self.session_id,
                task_id=self._task_id,
            )
            ctx.update(plan)
            # The routed plan may originate from an earlier task id (for
            # example, a replay). The facade's current task owns this pending
            # clarification and must remain traceable as such.
            ctx["task_id"] = self._task_id
            ctx["clarification_session"] = pending
            return ctx
        ctx["plan"] = plan
        self.task_anchor = TaskAnchor.from_plan(plan, task_id=self._task_id)
        ctx["task_anchor"] = self.task_anchor.to_dict()
        previous_results = self.memory.recall(scope="session", key="last_result")
        if previous_results:
            previous = previous_results[-1].value
            decision = self.historical_evidence_policy.assess(
                previous, self.task_state_ledger.task_state(plan))
            ctx["historical_evidence_decision"] = decision
            # History can guide interpretation only.  Any incompatible scope is
            # deliberately sent through execution again; never copy old rows or
            # narrative into the current task.
            ctx["historical_evidence_mode"] = decision.get("action")
            self.observer.record(
                "historical_evidence_gate", trace_id=self._trace_id,
                status=decision.get("action", "force_reexecute"),
                task_id=self._task_id, session_id=self.session_id,
                metadata={"reasons": decision.get("reasons") or [],
                          "changed_fields": (decision.get("task_state_diff") or {}).get("changed_fields") or []},
            )
        self.memory.remember("session", "task_anchor", ctx["task_anchor"],
                             session_id=self.session_id, task_id=self._task_id)
        self.observer.record(
            "task_anchor",
            trace_id=self._trace_id,
            status="ok",
            task_id=self._task_id,
            session_id=self.session_id,
            metadata=ctx["task_anchor"],
        )
        ctx["gov_data"] = ctx.get("gov_data", {})
        # Promote plan fields to ctx top-level for _finalize / normalize_result
        if isinstance(plan, dict):
            ctx.setdefault("intent", plan.get("intent"))
            ctx.setdefault("task_type", plan.get("task_type"))
            ctx.setdefault("metric", plan.get("metric"))
            ctx.setdefault("dimensions", plan.get("dimensions"))
            ctx.setdefault("status", plan.get("status"))
            ctx.setdefault("execution_mode", plan.get("execution_mode") or "plan_act")
        elif hasattr(plan, "to_dict"):
            pd = plan.to_dict()
            ctx.setdefault("intent", pd.get("intent"))
            ctx.setdefault("task_type", pd.get("task_type"))
            ctx.setdefault("metric", pd.get("metric"))
            ctx.setdefault("dimensions", pd.get("dimensions"))
            ctx.setdefault("status", pd.get("status"))
            ctx.setdefault("execution_mode", pd.get("execution_mode") or "plan_act")
        return ctx

    def _stage_decompose(self, ctx):
        """Stage 2.5: Decompose plan into sub-plans if needed."""
        plan = ctx.get("plan")
        if plan is None:
            return ctx

        # Skip decomposition for terminal statuses
        if ctx.get("status") in NON_OK_TERMINAL_STATUSES:
            return ctx

        query = ctx.get("query", "")
        result = self.decomposer.decompose(plan, query=query,
                                            trace_id=self._trace_id)

        ctx["sub_plans"] = result.sub_plans
        ctx["decompose_strategy"] = result.strategy
        ctx["decompose_reason"] = result.reason

        self.observer.record(
            "decompose",
            trace_id=self._trace_id,
            strategy=result.strategy,
            sub_plan_count=len(result.sub_plans),
            reason=result.reason,
            task_id=self._task_id,
        )

        # If decomposed, update plan with decompose metadata
        if result.strategy != "no_split" and len(result.sub_plans) > 1:
            pd = plan.to_dict() if hasattr(plan, "to_dict") else dict(plan)
            pd["sub_plans"] = [s.to_dict() if hasattr(s, "to_dict") else dict(s)
                               for s in result.sub_plans]
            pd["decompose_strategy"] = result.strategy
            pd["decompose_reason"] = result.reason
            ctx["plan"] = pd

        return ctx

    def _stage_execution(self, ctx):
        """Stage 3: Execute SQL (supports multi-plan execution)."""
        plan = ctx.get("plan")
        if plan is None:
            ctx["status"] = "error"
            ctx.setdefault("errors", []).append({"stage": "execution", "error": "no_plan"})
            return ctx

        sub_plans = ctx.get("sub_plans") or [plan]
        execution_mode = plan.get("execution_mode") if isinstance(plan, dict) else getattr(plan, "execution_mode", None)
        ctx["execution_idempotency"] = self.idempotency_key_builder.build(
            tenant_id=(ctx.get("access_context") or {}).get("tenant_id"),
            session_id=self.session_id, task_id=self._task_id, stage="execution",
            input_value=plan.to_dict() if hasattr(plan, "to_dict") else plan,
            policy_version="execution_v1")
        is_react = execution_mode == "react"
        if is_react:
            if isinstance(plan, dict):
                diagnostics = plan.setdefault("diagnostics", {})
            else:
                diagnostics = dict(getattr(plan, "diagnostics", None) or {})
                try:
                    plan.diagnostics = diagnostics
                except Exception:
                    pass
            diagnostics["react_selected"] = True
            diagnostics["react_policy"] = "controlled_exploration_candidate"
            # Phase 21-C: react observations are now governed through the
            # ReActObservationGovernor rather than blindly deferred.
            diagnostics["react_runtime"] = "governed_plan_act"

            self.observer.record(
                "react_selected",
                trace_id=self._trace_id,
                status="governed",
                task_id=self._task_id,
                session_id=self.session_id,
                metadata={"reason": diagnostics.get("execution_mode_reason"), "policy": diagnostics.get("react_policy")},
            )

        if len(sub_plans) == 1:
            if is_react:
                loop = ControlledReactLoop(self._execute, self.react_governor,
                                           max_steps=_REACT_MAX_STEPS,
                                           observer=self.observer)
                loop_result = loop.run(
                    self.task_anchor, sub_plans[0], trace_id=self._trace_id,
                    task_id=self._task_id, session_id=self.session_id,
                    replan=self._replan_react_action)
                exec_result = loop_result.result
                ctx["exec_result"] = exec_result
                ctx["react_observations"] = list(loop_result.observations)
                ctx["react_step_count"] = loop_result.steps
                ctx["react_terminal_action"] = loop_result.terminal_action
                ctx["react_replans"] = list(loop_result.replans)
                self._capture_execution_evidence(ctx, exec_result, sub_plans[0])
            else:
                exec_result = self._execute(sub_plans[0])
                ctx["exec_result"] = exec_result
                self._capture_execution_evidence(ctx, exec_result, sub_plans[0])

            self._record_dag_node("execute", exec_result.get("status", "ok") if isinstance(exec_result, dict) else "ok",
                                  None, {"execution_mode": execution_mode})
            self.observer.record(
                "explain", trace_id=self._trace_id,
                status=exec_result.get("status", "ok") if isinstance(exec_result, dict) else "ok",
                has_errors=bool(exec_result.get("errors")) if isinstance(exec_result, dict) else False,
                task_id=self._task_id)
            return ctx


        # Multi-plan: execute each sub-plan sequentially
        sub_results = []
        all_ok = True
        for i, sub_plan in enumerate(sub_plans):
            sub_result = self._execute(sub_plan)
            sub_results.append(sub_result)
            if isinstance(sub_result, dict) and sub_result.get("status") != "ok":
                all_ok = False

        # Merge results
        original_plan = plan.to_dict() if hasattr(plan, "to_dict") else dict(plan)
        merge_result = self.merger.merge(sub_results, original_plan=original_plan)
        merged = merge_result.merged

        # Record execution trace
        self.observer.record(
            "explain",
            trace_id=self._trace_id,
            status="ok" if all_ok else "partial",
            has_errors=not all_ok,
            sub_result_count=len(sub_results),
            merge_strategy=merge_result.strategy,
            task_id=self._task_id,
        )

        ctx["exec_result"] = merged
        ctx["sub_results"] = sub_results
        ctx["merge_strategy"] = merge_result.strategy
        for index, sub_result in enumerate(sub_results):
            self._capture_execution_evidence(ctx, sub_result, sub_plans[index])
        return ctx


    def _replan_react_action(self, plan, outcome, step_index):
        """Create a conservative second action after a governed pivot."""
        data = plan.to_dict() if hasattr(plan, 'to_dict') else dict(plan or {})
        data = dict(data)
        diagnostics = dict(data.get('diagnostics') or {})
        diagnostics['react_pivot_from_step'] = step_index
        diagnostics['react_pivot_reason'] = (outcome.get('decision') or {}).get('reason')
        diagnostics['react_observation_ref'] = None
        data['diagnostics'] = diagnostics
        return data

    def _capture_execution_evidence(self, ctx, exec_result, plan):
        """Store only a compact evidence card; quarantine anchor conflicts."""
        if not isinstance(exec_result, dict) or self.task_anchor is None:
            return
        pd = plan.to_dict() if hasattr(plan, "to_dict") else dict(plan or {})
        rows = exec_result.get("results") or exec_result.get("rows") or []
        summary = "execution status=%s rows=%s" % (exec_result.get("status", "ok"), len(rows))
        if rows and isinstance(rows[0], dict):
            summary += " columns=%s" % ",".join(sorted(rows[0].keys())[:8])
        card = EvidenceCard(
            task_id=self._task_id,
            source="execution",
            summary=summary,
            metric=pd.get("metric"),
            dimensions=pd.get("dimensions") or [],
            time_range=pd.get("time_range") or pd.get("time_range_label"),
            dataid=exec_result.get("dataid") or exec_result.get("current_dataid"),
            authority=AUTHORITY_VERIFIED if exec_result.get("status") == "ok" else "unverified",
            confidence=1.0 if exec_result.get("status") == "ok" else 0.0,
            metadata={"row_count": len(rows), "task_type": pd.get("task_type")},
        )
        card, decision = self.memory_policy.apply(self.task_anchor, card)
        data = card.to_dict()
        self.memory.remember("session", "evidence_card", data,
                             session_id=self.session_id, task_id=self._task_id)
        ctx.setdefault("evidence_cards", []).append(data)
        event = "memory_retrieved" if decision.action == "allow" else "memory_quarantined"
        self.observer.record(event, trace_id=self._trace_id,
                             status="ok" if decision.action == "allow" else "quarantined",
                             task_id=self._task_id, session_id=self.session_id,
                             metadata={"decision": decision.to_dict(), "evidence_id": card.evidence_id})
        ctx["injectable_evidence"] = self.memory_policy.compact_context(ctx.get("evidence_cards") or [])

    def _govern_react_observation(self, ctx, exec_result, plan, step_index=0):
        """Phase 21-C: run a react observation through the governor.

        The governor decides allow/quarantine/pivot and produces a compact,
        row-free OBSERVATION_REF that is safe to inject into the next react
        step. Never raises; observability failures must not break the loop.
        """
        if not isinstance(exec_result, dict):
            return
        try:
            outcome = self.react_governor.govern(
                self.task_anchor, step_index, "sql_query", exec_result,
                trace_id=self._trace_id, task_id=self._task_id,
                session_id=self.session_id)
        except Exception:
            return
        ctx.setdefault("react_observations", []).append(outcome)
        pd = plan if isinstance(plan, dict) else (plan.to_dict() if hasattr(plan, "to_dict") else {})
        diagnostics = pd.get("diagnostics") if isinstance(pd, dict) else None
        if isinstance(diagnostics, dict):
            diagnostics["react_last_action"] = outcome.get("action")
        self.observer.record(
            "react_observation",
            trace_id=self._trace_id,
            status=outcome.get("action", "allow"),
            task_id=self._task_id,
            session_id=self.session_id,
            metadata={"step": step_index, "action": outcome.get("action"),
                      "injectable": bool(outcome.get("injectable"))},
        )

    def _stage_analysis(self, ctx):

        """Stage 4: Analyze + Chart + Insight + Contribution + GMV Driver."""
        plan = ctx.get("plan")
        exec_result = ctx.get("exec_result")
        if exec_result is None:
            ctx["status"] = "error"
            ctx.setdefault("errors", []).append({"stage": "analysis", "error": "no_exec_result"})
            return ctx
        if isinstance(exec_result, dict) and exec_result.get("status") != "ok":
            ctx["status"] = exec_result.get("status") or "error"
            ctx.setdefault("errors", []).extend(exec_result.get("errors") or [])
            ctx["analysis"] = {"summary": u"执行未成功，不能生成数值、趋势、排名或归因结论。",
                               "caveats": ["execution_status:%s" % ctx.get("status")],
                               "key_findings": []}
            ctx["insight"] = {"summary": ctx["analysis"]["summary"]}
            return ctx
        analysis = self.analysis_strategies.analyze(plan, exec_result)
        exec_result["analysis"] = analysis
        chart = self.strategies.run("chart", plan if isinstance(plan, dict) else {}, exec_result)
        exec_result["chart"] = chart
        insight = self.strategies.run("insight", plan, exec_result)

        self._record_dag_node("analyze", "ok", None,
                              {"task_type": plan.get("task_type") if isinstance(plan, dict) else getattr(plan, "task_type", None)})
        ctx["analysis"] = analysis
        ctx["chart"] = chart
        ctx["insight"] = insight

        return ctx


    def _stage_reporting(self, ctx):
        """Stage 5: Generate report."""
        plan = ctx.get("plan")
        exec_result = ctx.get("exec_result")
        analysis = ctx.get("analysis")
        insight = ctx.get("insight")
        gov_data = ctx.get("gov_data", {})

        merged = dict(plan.to_dict() if hasattr(plan, "to_dict") else plan)
        diagnostics = dict(merged.get("diagnostics") or {})
        diagnostics["governance"] = gov_data
        merged.update(exec_result.to_dict() if hasattr(exec_result, "to_dict") else exec_result)
        merged.setdefault("task_id", self._task_id)
        merged.setdefault("parent_task_id", plan.get("parent_task_id") if isinstance(plan, dict) else None)
        merged.setdefault("resume_payload", plan.get("resume_payload") if isinstance(plan, dict) else {})
        merged["insight"] = insight.to_dict() if hasattr(insight, "to_dict") else insight
        merged["analysis"] = analysis
        merged["prompt_chain"] = merged.get("prompt_chain") or self._prompt_chain_ids("safety")
        merged["prompt_specs"] = merged.get("prompt_specs") or self.prompt_registry.chain_spec("safety")
        merged["sandbox"] = merged.get("sandbox") or self.subagent_runtime.describe("planner", task_id=self._task_id, parent_task_id=None)
        merged["human_gate"] = merged.get("human_gate") or self.human_gate.evaluate(
            ctx["query"], plan, self.risk_policy.assess(ctx["query"], plan)
        ).to_dict()
        merged["requires_human_review"] = merged.get("requires_human_review") or False
        merged["approval_status"] = merged.get("approval_status") or "approved"
        merged["risk_level"] = merged.get("risk_level") or self.risk_policy.assess(ctx["query"], plan).level
        merged["review_checklist"] = merged.get("review_checklist") or []
        exec_diagnostics = merged.get("diagnostics") or {}
        if isinstance(exec_diagnostics, dict):
            diagnostics.update(exec_diagnostics)
        diagnostics["prompt_chain"] = merged["prompt_chain"]
        diagnostics["prompt_specs"] = merged["prompt_specs"]
        diagnostics["sandbox"] = merged["sandbox"]
        diagnostics["human_gate"] = merged["human_gate"]
        diagnostics["task_anchor"] = ctx.get("task_anchor")
        diagnostics["evidence_cards"] = ctx.get("injectable_evidence") or []
        diagnostics["historical_evidence_decision"] = ctx.get("historical_evidence_decision")
        diagnostics["rag_context"] = {
            "status": (ctx.get("rag_context") or {}).get("status"),
            "decision": (ctx.get("rag_context") or {}).get("decision"),
            "confidence": (ctx.get("rag_context") or {}).get("confidence"),
            "evidence_count": len(ctx.get("rag_evidence") or []),
            "citations": (ctx.get("rag_context") or {}).get("citations") or [],
            "usage_policy": (((ctx.get("rag_context") or {}).get("context_pack") or {}).get("usage_policy") or {}),
        }
        merged["rag_evidence"] = list(ctx.get("rag_evidence") or [])
        claim_scope = self.claim_scope_builder.build(
            task_state=ctx.get("task_anchor"),
            tool_evidence=ctx.get("injectable_evidence") or [],
            rag_evidence=ctx.get("rag_evidence") or [],
            historical_decision=ctx.get("historical_evidence_decision"))
        ctx["claim_scope"] = claim_scope
        merged["claim_scope"] = claim_scope
        diagnostics["claim_scope"] = claim_scope
        diagnostics["prompt_governance_contract"] = self.rag_prompt_compiler.compile(
            "report", ctx.get("query"), task_state=ctx.get("task_anchor"),
            tool_evidence=ctx.get("injectable_evidence") or [],
            conversation_context=ctx.get("conversation_context"),
            claim_scope=claim_scope)[:1600]
        # Export only loop control metadata. Governed evidence remains compact
        # and is kept separately in the existing evidence-card fields.
        if ctx.get("react_step_count") is not None:
            diagnostics["react_loop"] = {
                "steps": ctx.get("react_step_count"),
                "terminal_action": ctx.get("react_terminal_action"),
                "replans": list(ctx.get("react_replans") or []),
                "replan_count": len(ctx.get("react_replans") or []),
            }
        merged["diagnostics"] = diagnostics
        merged["analysis"] = standardize_analysis_output(plan, merged, analysis=merged.get("analysis"), insight=merged.get("insight"))
        if isinstance(merged.get("insight"), dict):
            merged["insight"].setdefault("raw", {})
            merged["insight"]["raw"]["analysis"] = merged["analysis"]
        report_payload = {
            "query": merged.get("query", ctx["query"]),
            "analysis": merged.get("analysis") or {},
            "status": merged.get("status"),
            "task_type": merged.get("task_type"),
            "diagnostics": merged.get("diagnostics") or {},
            "results_summary": merged.get("results_summary") or {},
        }
        merged["report"] = generate_product_report(report_payload).to_dict()
        self._record_dag_node("report", merged.get("status", "ok"), None,
                              {"has_report": bool(merged.get("report"))})
        ctx.update(merged)
        return ctx


    def resume_clarification(self, choice_id):
        """Resume the pending plan for this facade session using an option id.

        Routing is intentionally skipped: the approved pending plan remains the
        source of truth, preventing a clarification answer from being parsed as
        an unrelated new analysis query.
        """
        t0 = time.time()
        self._trace_id = _new_id()
        self._task_id = _new_id()
        resolved = self.clarification_state.resolve(self.session_id, choice_id)
        if resolved.get("status") != "ok":
            raw = dict(resolved)
            raw["clarification_session"] = self.clarification_state.describe(self.session_id)
            return self._finalize("", raw, t0)

        plan = normalize_analysis_plan(
            resolved["plan"], query=resolved.get("original_query"), task_id=self._task_id)
        plan.parent_task_id = resolved.get("parent_task_id")
        self.task_anchor = TaskAnchor.from_plan(plan, task_id=self._task_id)
        ctx = {
            "query": resolved.get("original_query") or "",
            "plan": plan,
            "status": "ok",
            "task_id": self._task_id,
            "parent_task_id": resolved.get("parent_task_id"),
            "task_anchor": self.task_anchor.to_dict(),
            "gov_data": {},
            "intent": plan.get("intent"),
            "task_type": plan.get("task_type"),
            "metric": plan.get("metric"),
            "dimensions": plan.get("dimensions"),
        }
        for stage_name, stage_fn in [
                ("decompose", self._stage_decompose),
                ("execution", self._stage_execution),
                ("analysis", self._stage_analysis),
                ("reporting", self._stage_reporting)]:
            try:
                ctx = self.circuit_breaker.call(stage_fn, ctx)
            except Exception as exc:
                ctx["status"] = "error"
                ctx.setdefault("errors", []).append({"stage": stage_name, "error": str(exc)})
                break
        ctx["resume_payload"] = plan.get("resume_payload")
        return self._finalize(ctx.get("query", ""), ctx, t0)

    def get_pending_clarification(self):
        """Return safe pending clarification metadata for the current session."""
        return self.clarification_state.describe(self.session_id)

    def get_pending_human_review(self):
        """Return the safe approval checklist for the current session."""
        return self.human_review_state.describe(self.session_id)

    @staticmethod
    def _is_conversation_meta_query(query):
        """Whether a short turn asks about the previous answer rather than data."""
        text = (query or "").strip().lower()
        if text in ("?", "？", "什么", "什么意思", "再说一遍"):
            return True
        return any(phrase in text for phrase in ("什么都没有", "没看到", "看不懂", "没有啊", "没结果"))

    def _pending_choice_from_text(self, query):
        """Resolve a visible clarification option from natural-language input."""
        pending = self.get_pending_clarification() or {}
        text = (query or "").strip().lower()
        for item in pending.get("options") or []:
            option_id = str(item.get("id") or "")
            candidates = [option_id, item.get("label"), item.get("description")]
            if any(value and (text == str(value).lower() or str(value).lower() in text)
                   for value in candidates):
                return option_id
        aliases = {"整体": "metric_query", "汇总": "metric_query", "总数": "metric_query",
                   "拆分": "breakdown", "按维度": "breakdown", "明细": "breakdown"}
        for phrase, option_id in aliases.items():
            if phrase in text and option_id in [str(x.get("id")) for x in pending.get("options") or []]:
                return option_id
        return None

    def _safe_session_result(self, result):
        """Persist only typed state and evidence references for multi-turn use."""
        result = dict(result or {})
        ledger = result.get("fact_ledger") or {}
        diagnostics = result.get("diagnostics") or {}
        return {
            "status": result.get("status"),
            "task_id": result.get("task_id"),
            "parent_task_id": result.get("parent_task_id"),
            "intent": result.get("intent"),
            "task_type": result.get("task_type"),
            "metric": result.get("metric"),
            "dimensions": result.get("dimensions") or [],
            "filters": result.get("filters") or {},
            "time_range": result.get("time_range"),
            "dataid": result.get("dataid") or result.get("current_dataid") or ledger.get("dataid"),
            "fact_ledger": ledger,
            "provenance": result.get("provenance") or {},
            "diagnostics": {
                "rag_context": diagnostics.get("rag_context"),
                "historical_evidence_decision": diagnostics.get("historical_evidence_decision"),
                "evidence_cards": diagnostics.get("evidence_cards") or [],
            },
            "memory_contract": {
                "generated_text_persisted": False,
                "raw_rows_persisted": False,
                "safe_for_followup": True,
            },
        }

    def _safe_last_context(self, query, result):
        return {"query": query, "result": self._safe_session_result(result),
                "task_id": result.get("task_id"), "memory_contract": "safe_context_v1"}

    def _explain_last_result(self, query):
        """Return a bounded recap using only persisted typed state/evidence refs."""
        items = self.memory.recall(scope="session", key="last_result")
        previous = items[-1].value if items else {}
        ledger = previous.get("fact_ledger") or {}
        evidence_refs = ledger.get("evidence_refs") or []
        if not previous:
            summary = "上一轮没有可复用的安全执行状态。请重新发起分析问题。"
        else:
            parts = []
            if previous.get("metric"):
                parts.append("指标=%s" % previous.get("metric"))
            if previous.get("time_range"):
                parts.append("时间=%s" % previous.get("time_range"))
            if previous.get("dimensions"):
                parts.append("维度=%s" % ",".join(previous.get("dimensions") or []))
            if evidence_refs:
                parts.append("证据引用=%s" % ",".join([str(x) for x in evidence_refs[-4:]]))
            summary = "上一轮安全状态：%s。" % ("；".join(parts) or "仅保留了任务状态，未保留生成结论")
        raw = {"status": "ok", "intent": "conversation_recap",
               "metric": previous.get("metric"), "dimensions": previous.get("dimensions") or [],
               "fact_ledger": ledger,
               "insight": {"summary": summary + " 如需继续，请指定新的拆分、时间或筛选；范围变化会重新执行。",
                           "caveats": ["这是会话状态说明，不复用上一轮模型文案、原始行或未核验结论。"]},
               "provenance": previous.get("provenance") or {}}
        return self._finalize(query, raw, time.time())

    def decide_human_review(self, decision, reviewer_id=None, note=None):
        """Apply an explicit approve/reject decision to a high-risk plan.

        Approval resumes the already-reviewed plan without rerouting. Rejection
        returns a terminal blocked result and retains the reviewer audit data.
        """
        t0 = time.time()
        self._trace_id = _new_id()
        self._task_id = _new_id()
        resolved = self.human_review_state.decide(
            self.session_id, decision, reviewer_id=reviewer_id, note=note)
        if resolved.get("status") != "ok":
            return self._finalize("", dict(resolved), t0)
        plan = normalize_analysis_plan(resolved["plan"], query=resolved.get("original_query"), task_id=self._task_id)
        plan.parent_task_id = resolved.get("parent_task_id")
        self.task_anchor = TaskAnchor.from_plan(plan, task_id=self._task_id)
        ctx = {"query": resolved.get("original_query") or "", "plan": plan,
               "status": "ok", "task_id": self._task_id,
               "parent_task_id": resolved.get("parent_task_id"),
               "task_anchor": self.task_anchor.to_dict(), "gov_data": {},
               "intent": plan.get("intent"), "task_type": plan.get("task_type"),
               "metric": plan.get("metric"), "dimensions": plan.get("dimensions"),
               "human_review": resolved.get("human_review")}
        for stage_name, stage_fn in [("decompose", self._stage_decompose), ("execution", self._stage_execution),
                                     ("analysis", self._stage_analysis), ("reporting", self._stage_reporting)]:
            try:
                ctx = self.circuit_breaker.call(stage_fn, ctx)
            except Exception as exc:
                ctx["status"] = "error"
                ctx.setdefault("errors", []).append({"stage": stage_name, "error": str(exc)})
                break
        return self._finalize(ctx.get("query", ""), ctx, t0)

    def get_monitoring_dashboard(self):
        """Return rolling, safe monitoring data ready for an API/dashboard."""
        return self.monitoring.dashboard()

    def get_capability_catalog(self):
        return capability_catalog()

    def follow_up(self, query, use_llm=False, analysis_method=None):
        """Resolve and execute a traceable contextual follow-up.

        Context resolution is delegated to followup_context so ask() remains the
        stable single-turn entrypoint. Pending clarification is never rerouted.
        """
        self._trace_id = _new_id()
        self._task_id = _new_id()
        self._record_evidence_store_load_snapshot()
        from followup_context import FollowupContextResolver
        # Do not let a pending clarification be overwritten by an arbitrary
        # follow-up.  A user may either use a button or type its visible label.
        if self.clarification_state.has_pending(self.session_id):
            choice_id = self._pending_choice_from_text(query)
            if choice_id:
                return self.resume_clarification(choice_id)
            pending = self.get_pending_clarification() or {}
            # The clarification record owns the parent id.  Looking it up from
            # last_result is unstable because each reminder itself is finalized
            # and becomes the new last_result.
            parent_task_id = pending.get("task_id")
            if not parent_task_id:
                previous_items = self.memory.recall(scope="session", key="last_result")
                previous = previous_items[-1].value if previous_items else {}
                parent_task_id = previous.get("task_id")
            raw = {"status": "need_clarification", "intent": "clarification",
                   "clarification_session": pending,
                   "clarification": pending,
                   "parent_task_id": parent_task_id,
                   "summary": ("请先选择：%s。" % (pending.get("question") or "分析口径")),
                   "follow_up_context": {"blocked_by_pending_clarification": True,
                                         "reason": "pending_clarification_preserved",
                                         "parent_task_id": parent_task_id}}
            return self._finalize(query, raw, time.time())
        if self._is_conversation_meta_query(query):
            return self._explain_last_result(query)
        resolver = FollowupContextResolver(self.memory, self.clarification_state, self.session_id,
                                           evidence_bus=self.evidence_bus)
        followup = resolver.resolve(query)
        if followup.get("blocked_by_pending_clarification"):
            raw = {"status": "need_clarification", "intent": "clarification",
                   "clarification_session": self.get_pending_clarification(),
                   "follow_up_context": followup}
            return self._finalize(query, raw, time.time())
        decision = followup.get("decision")
        if decision == "need_clarification":
            raw = {"status": "need_clarification", "intent": "clarification",
                   "clarification": followup.get("clarification") or {},
                   "follow_up_context": followup,
                   "parent_task_id": followup.get("parent_task_id"),
                   "summary": (followup.get("clarification") or {}).get("question")}
            return self._finalize(query, raw, time.time())
        if decision == "blocked":
            raw = {"status": "blocked", "intent": "blocked",
                   "blocked_reason": (followup.get("decision_reasons") or ["followup_blocked"])[0],
                   "follow_up_context": followup,
                   "parent_task_id": followup.get("parent_task_id")}
            return self._finalize(query, raw, time.time())
        if decision == "reuse_verified_evidence":
            ctx = followup.get("resolved_context") or {}
            answer = u"可复用上一轮已验证证据进行解释；未重新执行查询。如需改变指标、时间、筛选或维度，将重新执行。"
            raw = {"status": "ok", "intent": "context_explanation", "metric": ctx.get("metric"),
                   "dimensions": ctx.get("dimensions") or [], "filters": ctx.get("filters") or {},
                   "time_range": ctx.get("time_range"), "task_type": ctx.get("task_type"),
                   "answer": answer, "insight": {"summary": answer},
                   "fact_ledger": {"authority": "verified_execution", "verified": True,
                                    "evidence_refs": ctx.get("evidence_refs") or [],
                                    "dataid": ctx.get("dataid"), "data_version": ctx.get("data_version"),
                                    "captured_at": ctx.get("captured_at")},
                   "follow_up_context": followup, "parent_task_id": followup.get("parent_task_id")}
            return self._finalize(query, raw, time.time())
        resolved_query = followup.get("resolved_query") or query
        result = self.ask(resolved_query, use_llm=use_llm, analysis_method=analysis_method)
        result["follow_up_context"] = followup
        if followup.get("parent_task_id") and followup.get("is_follow_up"):
            result["parent_task_id"] = followup.get("parent_task_id")
        self.observer.record("followup_context_resolved", trace_id=result.get("trace_id"),
                             status="ok", task_id=result.get("task_id"), session_id=self.session_id,
                             metadata={"followup_intent": followup.get("followup_intent"),
                                       "context_sources": followup.get("context_sources") or [],
                                       "overrides": followup.get("overrides") or {},
                                       "parent_task_id": followup.get("parent_task_id")})
        history = {"task_id": result.get("task_id"), "parent_task_id": result.get("parent_task_id"),
                   "query": query, "resolved_query": resolved_query, "status": result.get("status"),
                   "metric": result.get("metric"), "dimensions": result.get("dimensions") or [],
                   "filters": result.get("filters") or {}, "time_range": result.get("time_range"),
                   "task_type": result.get("task_type"), "followup_intent": followup.get("followup_intent"),
                   "context_sources": followup.get("context_sources") or []}
        self.memory.remember("session", "task_history", history, session_id=self.session_id,
                             task_id=result.get("task_id"))
        return result

    def get_task_history(self):
        return [item.value for item in self.memory.recall(scope="session", key="task_history")]

    def get_history(self):
        return [
            {"key": item.key, "value": item.value, "metadata": item.metadata}
            for item in self.memory.recall(scope="session")
        ]

    def get_trace(self, trace_id=None):
        tid = trace_id or self._trace_id
        return self.observer.events_as_dicts(trace_id=tid)

    def _resolve_multiturn_query(self, query, previous_context):
        """Resolve an underspecified turn from typed state only.

        Generated prose and old natural-language queries are deliberately excluded:
        they are untrusted, can contain prompt-injection-like text, and otherwise
        pollute retrieval/routing with obsolete scope.  This also avoids ``str``
        coercion of Chinese unicode values on the Python 2 compatible runtime.
        """
        base = _safe_text(query).strip()
        prev = previous_context if isinstance(previous_context, dict) else {}
        task_state = prev.get("task_state") or {}
        if not isinstance(task_state, dict):
            task_state = {}
        # Preserve prior typed task scope for longer drill-down turns too.
        follow_up_markers = (u"\u7ee7\u7eed", u"\u6362\u6210", u"\u6539\u6210", u"\u90a3\u4e0a\u5468",
                             u"\u4e0a\u5468", u"\u4e4b\u524d", u"\u518d\u770b", u"\u8fd9\u4e2a",
                             u"\u90a3\u4e2a", u"\u540c\u6bd4", u"\u73af\u6bd4", u"\u6309",
                             u"\u62c6\u89e3", u"\u62c6\u5206", u"\u7ef4\u5ea6", u"\u8d21\u732e",
                             u"\u6e20\u9053", u"\u5730\u533a", u"\u5546\u54c1")
        is_follow_up = (len(base) <= 8 or any(token in base for token in follow_up_markers))
        if not is_follow_up:
            return base
        # These are task interpretation hints, never factual evidence.  Do not
        # carry filters: a follow-up must state a changed filter explicitly and
        # the planner/execution policy will then re-run the data query.
        parts = [base]
        for key in ("metric", "task_type", "intent", "time_range"):
            value = task_state.get(key)
            text = _safe_text(value).strip()
            if text and text not in parts:
                parts.append(text)
        return u" ".join(parts).strip()

    def _route(self, query, use_llm=False):
        from dag_routing import route_and_plan
        plan = route_and_plan(query, use_llm=use_llm)
        self.observer.record("route", trace_id=self._trace_id, status=plan.get("status", "ok"), intent=plan.get("intent"), metric=plan.get("metric"), source=plan.get("source"), task_id=self._task_id)
        # P2 trace/replay contract: route_and_plan is a combined legacy call,
        # but replay needs an explicit planning checkpoint to distinguish route
        # selection from the executable plan snapshot.
        self.observer.record("plan", trace_id=self._trace_id, status=plan.get("status", "ok"),
                             stage="plan", intent=plan.get("intent"), metric=plan.get("metric"),
                             task_type=plan.get("task_type"), task_id=self._task_id,
                             session_id=self.session_id)
        return plan

    def _execute(self, plan):
        engine = ExecutionEngine(executor=self.executor, max_retries=1, observer=self.observer)
        # Chart selection occurs after analysis strategy execution in ask().
        # It must consume the shared analysis payload rather than raw SQL rows.
        return engine.execute(plan, trace_id=self._trace_id, task_id=self._task_id)

    @staticmethod
    def _terminal_user_answer(result):
        """Create fact-free terminal text with the correct decision semantics."""
        status = result.get("status")
        if status == "blocked":
            reason = result.get("blocked_reason") or u"该请求涉及受限操作或数据"
            return u"风险提示：%s。为保护数据与业务安全，本次请求已拦截；请改为只读、非敏感的分析请求。" % reason
        if status == "unsupported":
            diagnostics = result.get("diagnostics") or {}
            reason = result.get("blocked_reason") or diagnostics.get("reason") or u"当前能力范围不支持该分析"
            return u"结论：%s，因此不会生成未经验证的结果。请接入对应数据模型或改为当前已支持的指标分析。" % reason
        if status == "need_clarification":
            clarification = result.get("clarification") or {}
            question = clarification.get("question") if isinstance(clarification, dict) else None
            return u"结论：当前信息不足，不能确认数值、趋势或归因。%s" % (question or u"请补充指标、时间范围或筛选条件后继续。")
        if status == "pending_human_review":
            return u"本次请求涉及敏感数据或高风险操作，已进入人工审核；即使审核通过，敏感字段仍会按脱敏策略返回。"
        if status == "degraded":
            diagnostics = result.get("diagnostics") or {}
            failure_type = diagnostics.get("failure_type") or u"数据源不可用"
            # A data failure forbids verified numeric conclusions, not useful
            # business assistance.  Return explicitly bounded hypotheses and
            # next actions, separating them from any user-supplied facts.
            from evidence_limited_answer import build_evidence_limited_answer
            return build_evidence_limited_answer(result.get("query") or "", failure_type=failure_type)
        return None

    def _audit_governance(self, ctx, decision):
        metadata = (decision or {}).get("metadata") or {}
        event = build_governance_audit_event(ctx.get("access_context"), ctx.get("query"), self._task_id,
                                             self._trace_id, metadata.get("tables"), metadata.get("fields"),
                                             (decision or {}).get("action") or "allowed",
                                             (decision or {}).get("reason"))
        audit = getattr(self.governance, "audit", None)
        if audit is not None and hasattr(audit, "log_event"):
            audit.log_event("governance_decision", event)

    def _evidence_tenant_id(self):
        return (self.access_context or {}).get("tenant_id") or "default"

    def _load_persisted_evidence(self):
        """Load tenant/session-scoped verified evidence records if a store exists."""
        if not self.evidence_store:
            return []
        try:
            records = []
            if hasattr(self.evidence_store, "list_records"):
                records = self.evidence_store.list_records(self._evidence_tenant_id(), self.session_id, limit=200)
            elif hasattr(self.evidence_store, "list_evidence"):
                from repository_contracts import RepositoryAccessContext
                access = RepositoryAccessContext(tenant_id=self._evidence_tenant_id(), user_id=(self.access_context or {}).get("user_id"), verified=True, source="agent_facade")
                records = self.evidence_store.list_evidence(access, self.session_id, limit=200)
            else:
                records = []
            return records or []
        except Exception as exc:
            self.observer.record(
                "evidence_store_load", trace_id=self._trace_id,
                status="degraded", task_id=self._task_id,
                session_id=self.session_id, stage="evidence_store",
                failure_type="evidence_store_load_error",
                metadata={"reason": str(exc)})
        return []

    def _record_evidence_store_load_snapshot(self):
        """Emit a replayable hydration event for the current request trace."""
        if not self.evidence_store:
            return None
        try:
            count = len(getattr(self.evidence_bus, "records", {}) or {})
            self.observer.record(
                "evidence_store_load", trace_id=self._trace_id,
                status="ok", task_id=self._task_id, session_id=self.session_id,
                stage="evidence_store",
                metadata={"loaded_count": count, "tenant_id": self._evidence_tenant_id()})
            return count
        except Exception:
            return None

    def _persist_evidence_record(self, record):
        """Persist verified evidence without making persistence authoritative."""
        if not self.evidence_store or not record:
            return None
        try:
            saved = None
            if hasattr(self.evidence_store, "save_record"):
                saved = self.evidence_store.save_record(self._evidence_tenant_id(), self.session_id, record)
            elif hasattr(self.evidence_store, "save_evidence"):
                from repository_contracts import RepositoryAccessContext
                access = RepositoryAccessContext(tenant_id=self._evidence_tenant_id(), user_id=(self.access_context or {}).get("user_id"), verified=True, source="agent_facade")
                saved = self.evidence_store.save_evidence(access, self.session_id, record.get("evidence_id"), record)
            if saved is not None:
                self.observer.record(
                    "evidence_store_persist", trace_id=self._trace_id,
                    status="ok", task_id=self._task_id, session_id=self.session_id,
                    stage="evidence_store",
                    evidence_id=record.get("evidence_id"),
                    metadata={"evidence_id": record.get("evidence_id"),
                              "tenant_id": self._evidence_tenant_id()})
            return saved
        except Exception as exc:
            self.observer.record(
                "evidence_store_persist", trace_id=self._trace_id,
                status="degraded", task_id=self._task_id,
                session_id=self.session_id, stage="evidence_store",
                failure_type="evidence_store_persist_error",
                metadata={"reason": str(exc), "evidence_id": record.get("evidence_id")})
        return None

    def _record_result_evidence(self, result):
        """Record verified execution evidence into the session EvidenceBus."""
        if not isinstance(result, dict):
            return None
        envelope = result.get("execution_envelope")
        if not envelope:
            diagnostics = result.get("diagnostics") or {}
            if isinstance(diagnostics, dict):
                envelope = diagnostics.get("execution_envelope")
        if not envelope:
            return None
        try:
            record = self.evidence_bus.record_envelope(
                envelope,
                producer_task_id=result.get("task_id") or self._task_id,
                trace_id=result.get("trace_id") or self._trace_id,
                graph_type=result.get("graph_type"),
            )
            self._persist_evidence_record(record)
            self.observer.record(
                "evidence_bus_recorded", trace_id=self._trace_id,
                status="ok" if record else "skipped", task_id=self._task_id,
                session_id=self.session_id,
                metadata={"evidence_id": envelope.get("evidence_id") if isinstance(envelope, dict) else None,
                          "persisted": bool(record and self.evidence_store)})
            return record
        except Exception as exc:
            self.observer.record(
                "evidence_bus_recorded", trace_id=self._trace_id,
                status="degraded", task_id=self._task_id,
                session_id=self.session_id, metadata={"reason": str(exc)})
            return None

    def _finalize(self, query, raw, t0):
        result_obj = normalize_result(raw, query=query, session_id=self.session_id, trace_id=self._trace_id)
        result = result_obj.to_dict() if hasattr(result_obj, "to_dict") else dict(result_obj)
        # normalize_result deliberately emits the canonical execution contract.
        # Preserve the small, typed state-machine payloads required by the
        # follow-up API; otherwise an unrelated reply during clarification
        # loses the active options and the user cannot resume the task.
        if isinstance(raw, dict):
            for key in ("clarification_session", "follow_up_context", "from_cache"):
                if key in raw:
                    result[key] = raw[key]
        if result.get("status") == "error":
            diagnostics = result.get("diagnostics") or {}
            errors_text = u"%s" % (result.get("errors") or diagnostics)
            failure_type = diagnostics.get("failure_type")
            execution_envelope = result.get("execution_envelope") or diagnostics.get("execution_envelope")
            execution_status = execution_envelope.get("status") if isinstance(execution_envelope, dict) else None
            evidence_limited_execution = execution_status == "error" and bool(result.get("analysis") or result.get("insight"))
            if (failure_type in ("schema_error", "db_error", "sql_validation_error", "retry_exhausted") or
                    evidence_limited_execution or "no such column" in errors_text or "no such table" in errors_text):
                diagnostics["failure_type"] = failure_type or "execution_error"
                diagnostics["evidence_limited"] = True
                result["diagnostics"] = diagnostics
                result["status"] = "degraded"
                result["fallback_reason"] = diagnostics.get("failure_type") or "execution_error"
        result["elapsed_ms"] = int((time.time() - t0) * 1000)
        masked_fields = (((raw or {}).get("permission_decision") or {}).get("metadata") or {}).get("masked_fields") if isinstance(raw, dict) else []
        result = sanitize_agent_payload(result, masked_fields=masked_fields)
        # Normalizers may expose these keys with a None value.  Treat that as
        # absent: otherwise consecutive clarification reminders lose their
        # stable task lineage and multi-turn recovery becomes non-deterministic.
        if not result.get("task_id"):
            result["task_id"] = self._task_id
        if not result.get("parent_task_id") and isinstance(raw, dict):
            result["parent_task_id"] = raw.get("parent_task_id")
        result.setdefault("resume_payload", raw.get("resume_payload") if isinstance(raw, dict) else {})
        plan_for_credibility = result.get("plan") or raw.get("plan") if isinstance(raw, dict) else result.get("plan")
        result["credibility"] = build_credibility(plan_for_credibility or {}, result)
        result["provenance"] = build_provenance(plan_for_credibility or {}, result)
        result["business_coverage"] = business_coverage(plan_for_credibility or result)
        result["capability_contract"] = capability_catalog()["contract"]
        result["monitoring"] = self.monitoring.record_completed(self._trace_id, result)
        claim_scope = result.get("claim_scope") or ((result.get("diagnostics") or {}).get("claim_scope") or {})
        result["claim_audit"] = self.claim_auditor.audit(
            result.get("report") or result.get("analysis") or result,
            tool_evidence=((result.get("diagnostics") or {}).get("evidence_cards") or []),
            rag_evidence=result.get("rag_evidence") or [], claim_scope=claim_scope)
        # The audit is an enforcement point, not just observability: an
        # unsupported generated conclusion is replaced by a bounded abstention.
        if result["claim_audit"].get("status") == "blocked":
            result["answer"] = result["claim_audit"].get("safe_answer")
            report = result.get("report")
            if isinstance(report, dict):
                report["answer"] = result["answer"]
                report["summary"] = result["answer"]
                report["unsupported_claims"] = result["claim_audit"].get("unsupported_claims") or []
        self.observer.record("answer_audit", trace_id=self._trace_id,
                             status=result["claim_audit"].get("status") or "ok",
                             stage="answer_audit",
                             failure_type=None if result["claim_audit"].get("status") != "blocked" else "unsupported_claim",
                             task_id=self._task_id, session_id=self.session_id,
                             metadata={"unsupported_claims": result["claim_audit"].get("unsupported_claims") or [],
                                       "evidence_ids": result.get("evidence_ids") or result.get("citations") or []})
        terminal_answer = self._terminal_user_answer(result)
        if terminal_answer:
            result["answer"] = terminal_answer
            report = result.get("report")
            if isinstance(report, dict):
                report["answer"] = terminal_answer
                report["summary"] = terminal_answer
        result["fact_ledger"] = self.task_state_ledger.capture(result)
        raw_ledger = (raw.get("fact_ledger") if isinstance(raw, dict) else {}) or {}
        if (isinstance(raw_ledger, dict) and raw_ledger.get("authority") == "verified_execution" and
                raw_ledger.get("evidence_refs")):
            result["fact_ledger"]["evidence_refs"] = list(raw_ledger.get("evidence_refs") or [])
            result["fact_ledger"]["authority"] = "verified_execution"
            result["fact_ledger"]["verified"] = True
            result["fact_ledger"]["dataid"] = raw_ledger.get("dataid")
            result["fact_ledger"]["data_version"] = raw_ledger.get("data_version")
            result["fact_ledger"]["captured_at"] = raw_ledger.get("captured_at")
        evidence_record = self._record_result_evidence(raw if isinstance(raw, dict) else result)
        if evidence_record:
            result["evidence_id"] = evidence_record.get("evidence_id")
            result.setdefault("evidence_refs", [])
            if evidence_record.get("evidence_id") not in result["evidence_refs"]:
                result["evidence_refs"].append(evidence_record.get("evidence_id"))
            result["fact_ledger"]["evidence_refs"] = [evidence_record.get("evidence_id")]
            result["fact_ledger"]["authority"] = evidence_record.get("authority")
            result["fact_ledger"]["verified"] = True
            result["fact_ledger"]["dataid"] = evidence_record.get("dataid")
            result["fact_ledger"]["data_version"] = evidence_record.get("data_version")
            result["fact_ledger"]["captured_at"] = evidence_record.get("recorded_at")
        self._record_result_evidence(result)
        # Add a stable, report-safe evaluation projection without altering the
        # legacy top-level response contract consumed by existing clients.
        result["answer_envelope"] = build_answer_envelope(result, query=query)
        result["final_answer"] = build_final_answer_contract(result, query=query)
        # The facade owns the live session EvidenceBus.  A fact can be emitted by
        # the generic answer adapter only after it is checked against the current
        # task scope and the verified execution records retained by that bus.
        execution_envelope = result.get("execution_envelope") or ((result.get("diagnostics") or {}).get("execution_envelope") or {})
        execution_metadata = execution_envelope.get("metadata") if isinstance(execution_envelope, dict) else {}
        execution_metadata = execution_metadata if isinstance(execution_metadata, dict) else {}
        claim_scope = {
            "metric": result.get("metric") or execution_metadata.get("metric"),
            "allowed_time_ranges": [result.get("time_range")] if result.get("time_range") else [],
            "dimensions": list(result.get("dimensions") or execution_metadata.get("dimensions") or []),
            "filters": dict(result.get("filters") or execution_metadata.get("filters") or {}),
            "dataid": result.get("dataid") or (execution_envelope.get("dataid") if isinstance(execution_envelope, dict) else None),
            "data_version": result.get("data_version") or (execution_envelope.get("data_version") if isinstance(execution_envelope, dict) else None),
            "tenant_id": (result.get("access_context") or self.access_context or {}).get("tenant_id"),
            "user_id": (result.get("access_context") or self.access_context or {}).get("user_id"),
            "permission_scope": (result.get("access_context") or self.access_context or {}).get("permission_scope"),
        }
        audited_answer, graduation_findings = audit_final_answer_claims(
            result["final_answer"], evidence_bus=self.evidence_bus,
            expected_scope=claim_scope)
        result["final_answer"] = audited_answer
        result.setdefault("diagnostics", {})["claim_graduation"] = {
            "findings": graduation_findings, "expected_scope": claim_scope,
            "evidence_bus_record_count": len(getattr(self.evidence_bus, "records", {}) or {})}
        if audited_answer.get("status") != "ok" and result.get("status") == "ok":
            result["status"] = audited_answer.get("status")
            result["answer"] = (u"当前没有可用于确认该结论的、与本次范围匹配的执行证据。"
                                u"请重新执行查询或补充所需范围后继续。")
        result["answer_type"] = result["final_answer"].get("answer_type")
        result["facts"] = result["final_answer"].get("facts") or []
        result["hypotheses"] = result["final_answer"].get("hypotheses") or []
        result["citations"] = result["final_answer"].get("citations") or []
        result["limitations"] = result["final_answer"].get("limitations") or []
        result["next_actions"] = result["final_answer"].get("next_actions") or []
        result["quality"] = score_answer_quality(result, result["answer_envelope"])
        result["answer_envelope"]["quality"] = result["quality"]
        # Store only a bounded typed checkpoint plus a short raw turn window.
        # Generated answer/report text and raw rows intentionally never enter it.
        try:
            self.session_context.add_turn(query, result)
            result["conversation_context"] = self.session_context.build()
            self.memory.remember("session", "conversation_context", result["conversation_context"],
                                 session_id=self.session_id, task_id=self._task_id)
            self.observer.record("conversation_context_compacted", trace_id=self._trace_id,
                                 status="ok", task_id=self._task_id, session_id=self.session_id,
                                 metadata={"turn_count": result["conversation_context"].get("turn_count"),
                                           "token_estimate": result["conversation_context"].get("token_estimate"),
                                           "within_budget": result["conversation_context"].get("within_budget")})
        except Exception as exc:
            # Never fail a user task because a non-authoritative context checkpoint failed.
            result["conversation_context"] = {"degraded": True, "current_query": query}
            self.observer.record("conversation_context_compacted", trace_id=self._trace_id,
                                 status="degraded", task_id=self._task_id, session_id=self.session_id,
                                 metadata={"reason": str(exc)})
        self.memory.remember("session", "fact_ledger", result["fact_ledger"], session_id=self.session_id, task_id=self._task_id)
        safe_result = self._safe_session_result(result)
        self.memory.remember("session", "last_result", safe_result, session_id=self.session_id, task_id=self._task_id)
        self.memory.remember("session", "last_context", self._safe_last_context(query, result), session_id=self.session_id, task_id=self._task_id)
        self.observer.record(
            "complete",
            trace_id=self._trace_id,
            status=result.get("status", "ok"),
            elapsed_ms=result["elapsed_ms"],
            intent=result.get("intent"),
            has_report=bool(result.get("report")),
            has_chart=bool(result.get("chart")),
            task_id=self._task_id,
            session_id=self.session_id,
        )
        return result


__all__ = ["AgentFacade"]
