# -*- coding: utf-8 -*-
"""Canonical data structures for the Data Agent pipeline.

This module is intentionally Python 2.7 friendly: it avoids dataclasses,
annotations, and other newer syntax so legacy entrypoints can import it.
"""


class _DictLike(object):
    def get(self, key, default=None):
        return self.to_dict().get(key, default)

    def __getitem__(self, key):
        return self.to_dict()[key]

    def __contains__(self, key):
        return key in self.to_dict()

    def keys(self):
        return self.to_dict().keys()

    def items(self):
        return self.to_dict().items()

    def values(self):
        return self.to_dict().values()


class ClarificationRequest(_DictLike):
    def __init__(self, metric=None, question="", options=None, reason=None, expected_next_step=None):
        self.metric = metric
        self.question = question
        self.options = options or []
        self.reason = reason
        self.expected_next_step = expected_next_step

    def to_dict(self):
        return {
            "metric": self.metric,
            "question": self.question,
            "options": list(self.options),
            "reason": self.reason,
            "expected_next_step": self.expected_next_step,
        }


class AnalysisPlan(_DictLike):
    def __init__(self, query, status="ok", intent=None, source=None, confidence=None,
                 model=None, metric=None, metrics=None, dimensions=None, filters=None,
                 time_range=None, clarification=None, blocked_reason=None, task_steps=None,
                 fallback_policy=None, verification_policy=None, schema_version="v1",
                  plan_version="v1", memory_refs=None, diagnostics=None, task_id=None,
                  parent_task_id=None, resume_payload=None, task_type="descriptive",
                  analysis_config=None, current_time_range=None, previous_time_range=None,
                  time_dimension=None, attribution_dimension=None, cohort_definition=None,
                  funnel_steps=None,
                   sub_plans=None, decompose_strategy=None, decompose_reason=None,
                   execution_mode=None, join_strategy=None):
        self.query = query
        self.status = status
        self.intent = intent
        self.source = source
        self.confidence = confidence
        self.model = model
        self.metric = metric
        self.metrics = metrics or []
        self.dimensions = dimensions or []
        self.filters = filters or []
        self.time_range = time_range
        self.clarification = clarification
        self.blocked_reason = blocked_reason
        self.task_steps = task_steps or []
        self.fallback_policy = fallback_policy or {}
        self.verification_policy = verification_policy or {}
        self.schema_version = schema_version
        self.plan_version = plan_version
        self.memory_refs = memory_refs or []
        self.diagnostics = diagnostics or {}
        self.task_id = task_id
        self.parent_task_id = parent_task_id
        self.resume_payload = resume_payload or {}
        self.task_type = task_type or "descriptive"
        self.analysis_config = analysis_config or {}
        self.current_time_range = current_time_range
        self.previous_time_range = previous_time_range
        self.time_dimension = time_dimension
        self.attribution_dimension = attribution_dimension
        self.cohort_definition = cohort_definition or {}
        self.funnel_steps = funnel_steps or []
        self.sub_plans = sub_plans or []
        self.decompose_strategy = decompose_strategy
        self.decompose_reason = decompose_reason
        # execution_mode: "plan_act" (default) | "react" (iterative exploration)
        self.execution_mode = execution_mode or "plan_act"
        # v2 additive planning metadata. Join strategy is declarative so the
        # later SQL builder can choose paths without re-interpreting the query.
        self.join_strategy = join_strategy or {}


    def to_dict(self):
        return {
            "query": self.query,
            "status": self.status,
            "intent": self.intent,
            "source": self.source,
            "confidence": self.confidence,
            "model": self.model,
            "metric": self.metric,
            "metrics": list(self.metrics),
            "dimensions": list(self.dimensions),
            "filters": list(self.filters),
            "time_range": self.time_range,
            "clarification": self.clarification,
            "blocked_reason": self.blocked_reason,
            "task_steps": list(self.task_steps),
            "fallback_policy": dict(self.fallback_policy),
            "verification_policy": dict(self.verification_policy),
            "schema_version": self.schema_version,
            "plan_version": self.plan_version,
            "memory_refs": list(self.memory_refs),
            "diagnostics": dict(self.diagnostics),
            "task_id": self.task_id,
            "parent_task_id": self.parent_task_id,
            "resume_payload": dict(self.resume_payload),
            "task_type": self.task_type,
            "analysis_config": dict(self.analysis_config),
            "current_time_range": self.current_time_range,
            "previous_time_range": self.previous_time_range,
            "time_dimension": self.time_dimension,
            "attribution_dimension": self.attribution_dimension,
            "cohort_definition": dict(self.cohort_definition),
            "funnel_steps": list(self.funnel_steps),
            "sub_plans": list(self.sub_plans),
            "decompose_strategy": self.decompose_strategy,
            "decompose_reason": self.decompose_reason,
            "execution_mode": self.execution_mode,
            "join_strategy": dict(self.join_strategy),
        }


class ExecutionResult(_DictLike):
    def __init__(self, query, status="ok", intent=None, model=None, metric=None,
                 dimensions=None, time_range=None, steps=None, insight=None, chart=None,
                 sql=None, results=None, results_summary=None, errors=None, trace=None,
                 termination_reason=None, loop_stats=None, execution=None, diagnostics=None,
                 session_id=None, trace_id=None, clarification=None, task_id=None,
                 parent_task_id=None, resume_payload=None, analysis=None, interrupt=None,
                 state=None, executor=None, plan=None, report=None, elapsed_ms=None,
                  blocked_reason=None, fallback_reason=None, confidence=None, requires_human_review=None,
                  approval_status=None, risk_level=None, review_checklist=None,
                 prompt_chain=None, prompt_specs=None, sandbox=None, human_gate=None,
                 execution_mode=None):
        self.query = query
        self.status = status
        self.intent = intent
        self.model = model
        self.metric = metric
        self.dimensions = dimensions or []
        self.time_range = time_range
        self.steps = steps or []
        self.insight = insight
        self.chart = chart or {}
        self.sql = sql
        self.results = results
        self.results_summary = results_summary
        self.errors = errors or []
        self.trace = trace
        self.termination_reason = termination_reason
        self.loop_stats = loop_stats or {}
        self.execution = execution or {}
        self.diagnostics = diagnostics or {}
        self.session_id = session_id
        self.trace_id = trace_id
        self.clarification = clarification
        self.task_id = task_id
        self.parent_task_id = parent_task_id
        self.resume_payload = resume_payload or {}
        self.analysis = analysis
        self.interrupt = interrupt
        self.state = state
        self.executor = executor
        self.plan = plan or {}
        self.report = report
        self.elapsed_ms = elapsed_ms
        self.blocked_reason = blocked_reason
        self.fallback_reason = fallback_reason
        self.confidence = confidence
        self.requires_human_review = requires_human_review
        self.approval_status = approval_status
        self.risk_level = risk_level
        self.review_checklist = review_checklist or []
        self.prompt_chain = prompt_chain or []
        self.prompt_specs = prompt_specs or []
        self.sandbox = sandbox or {}
        self.human_gate = human_gate or {}
        self.execution_mode = execution_mode or "plan_act"

    def to_dict(self):
        return {
            "query": self.query,
            "status": self.status,
            "intent": self.intent,
            "model": self.model,
            "metric": self.metric,
            "dimensions": list(self.dimensions),
            "time_range": self.time_range,
            "steps": list(self.steps),
            "insight": self.insight,
            "chart": dict(self.chart),
            "sql": self.sql,
            "results": self.results,
            "results_summary": self.results_summary,
            "errors": list(self.errors),
            "trace": self.trace,
            "termination_reason": self.termination_reason,
            "loop_stats": dict(self.loop_stats),
            "execution": dict(self.execution),
            "diagnostics": dict(self.diagnostics),
            "session_id": self.session_id,
            "trace_id": self.trace_id,
            "clarification": self.clarification,
            "task_id": self.task_id,
            "parent_task_id": self.parent_task_id,
            "resume_payload": dict(self.resume_payload),
            "analysis": self.analysis if self.analysis is not None else self.insight,
            "interrupt": self.interrupt,
            "state": self.state,
            "executor": self.executor,
            "plan": dict(self.plan),
            "report": self.report,
            "elapsed_ms": self.elapsed_ms,
            "blocked_reason": self.blocked_reason,
            "fallback_reason": self.fallback_reason,
            "confidence": self.confidence,
            "requires_human_review": self.requires_human_review,
            "approval_status": self.approval_status,
            "risk_level": self.risk_level,
            "review_checklist": list(self.review_checklist),
            "prompt_chain": list(self.prompt_chain),
            "prompt_specs": list(self.prompt_specs),
            "sandbox": dict(self.sandbox),
            "human_gate": dict(self.human_gate),
            "execution_mode": self.execution_mode,
        }


class InsightBundle(_DictLike):
    def __init__(self, summary="", chart=None, caveats=None, next_steps=None, raw=None):
        self.summary = summary
        self.chart = chart or {}
        self.caveats = caveats or []
        self.next_steps = next_steps or []
        self.raw = raw or {}

    def to_dict(self):
        return {
            "summary": self.summary,
            "headline": self.summary,
            "chart": dict(self.chart),
            "caveats": list(self.caveats),
            "next_steps": list(self.next_steps),
            "raw": dict(self.raw),
        }


def ensure_dict(obj):
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    if hasattr(obj, "__dict__"):
        return dict(obj.__dict__)
    return {"value": obj}
