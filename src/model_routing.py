# -*- coding: utf-8 -*-
"""Governed model-tier routing and token-budget helpers.

This module is intentionally deterministic: it decides whether a stage may use
no LLM, a small model, or a large model.  It does not decide factual truth; all
answers must still pass execution/evidence governance.
"""
from __future__ import unicode_literals

import os
import time

try:
    text_type = unicode
except NameError:  # pragma: no cover
    text_type = str

TIER_NO_LLM = "no_llm"
TIER_SMALL = "small"
TIER_LARGE = "large"

POLICY_OFF = "off"
POLICY_AUTO = "auto"
POLICY_SMALL_ONLY = "small_only"
POLICY_LARGE_ONLY = "large_only"
POLICY_SHADOW = "shadow"

DEFAULT_SMALL_MODEL = os.environ.get("DEEPSEEK_SMALL_MODEL", "deepseek-v4-flash")
DEFAULT_LARGE_MODEL = os.environ.get("DEEPSEEK_LARGE_MODEL", os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"))
DEFAULT_STAGE_BUDGETS = {
    "route": int(os.environ.get("MODEL_ROUTE_TOKEN_BUDGET", "768")),
    "planning": int(os.environ.get("MODEL_PLANNING_TOKEN_BUDGET", "1024")),
    "analysis": int(os.environ.get("MODEL_ANALYSIS_TOKEN_BUDGET", "1536")),
    "reporting": int(os.environ.get("MODEL_REPORTING_TOKEN_BUDGET", "2048")),
}

COMPLEX_TASK_TYPES = set(["anomaly", "attribution", "forecast", "experiment", "retention", "funnel", "root_cause"])
COMPLEX_TERMS = [u"归因", u"根因", u"为什么", u"异常", u"预测", u"实验", u"AB", u"ab", u"漏斗", u"留存", u"贡献", u"下钻", u"诊断"]


def _safe_text(value):
    if value is None:
        return u""
    if isinstance(value, text_type):
        return value
    try:
        return value.decode("utf-8")
    except Exception:
        try:
            return text_type(value)
        except Exception:
            return u""


def normalize_llm_policy(use_llm=None, llm_policy=None):
    """Map legacy bool use_llm to a governed policy string."""
    if llm_policy:
        value = _safe_text(llm_policy).lower()
        if value in (POLICY_OFF, POLICY_AUTO, POLICY_SMALL_ONLY, POLICY_LARGE_ONLY, POLICY_SHADOW):
            return value
    if use_llm is True:
        return POLICY_AUTO
    return POLICY_OFF


class ModelRouteDecision(object):
    def __init__(self, tier=TIER_NO_LLM, model=None, stage="route", reason_codes=None,
                 complexity_score=0.0, confidence=1.0, token_budget=None,
                 llm_policy=POLICY_OFF, decision_version="model_route_v1", shadow=False):
        self.tier = tier
        self.model = model
        self.stage = stage
        self.reason_codes = list(reason_codes or [])
        self.complexity_score = float(complexity_score or 0.0)
        self.confidence = float(confidence or 0.0)
        self.token_budget = int(token_budget or DEFAULT_STAGE_BUDGETS.get(stage, 1024))
        self.llm_policy = llm_policy
        self.decision_version = decision_version
        self.shadow = bool(shadow)
        self.created_at = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())

    def to_dict(self):
        return {
            "contract": "model_route_decision_v1",
            "tier": self.tier,
            "model": self.model,
            "stage": self.stage,
            "reason_codes": list(self.reason_codes),
            "complexity_score": self.complexity_score,
            "confidence": self.confidence,
            "token_budget": self.token_budget,
            "llm_policy": self.llm_policy,
            "decision_version": self.decision_version,
            "shadow": self.shadow,
            "created_at": self.created_at,
        }


class ModelInvocationEnvelope(object):
    def __init__(self, decision, status="ok", prompt_tokens=0, completion_tokens=0,
                 duration_ms=0, error=None, retryable=False, upgrade_from=None):
        if isinstance(decision, dict):
            data = decision
        elif hasattr(decision, "to_dict"):
            data = decision.to_dict()
        else:
            data = {}
        self.data = {
            "contract": "model_invocation_v1",
            "status": status,
            "stage": data.get("stage", "route"),
            "tier": data.get("tier"),
            "model": data.get("model"),
            "prompt_tokens": int(prompt_tokens or 0),
            "completion_tokens": int(completion_tokens or 0),
            "total_tokens": int(prompt_tokens or 0) + int(completion_tokens or 0),
            "duration_ms": float(duration_ms or 0),
            "error": error,
            "retryable": bool(retryable),
            "upgrade_from": upgrade_from,
            "decision": data,
        }

    def to_dict(self):
        return dict(self.data)


def estimate_query_complexity(query, plan=None):
    q = _safe_text(query)
    data = dict(plan or {})
    reasons = []
    score = 0.0
    dims = data.get("dimensions") or []
    metrics = data.get("metrics") or ([data.get("metric")] if data.get("metric") else [])
    task_type = data.get("task_type")
    if task_type in COMPLEX_TASK_TYPES:
        score += 0.45; reasons.append("complex_task_type")
    if len([m for m in metrics if m]) > 1:
        score += 0.25; reasons.append("multi_metric")
    if len(dims) > 1:
        score += 0.20; reasons.append("multi_dimension")
    matched_complex_terms = [term for term in COMPLEX_TERMS if term in q]
    if matched_complex_terms:
        # One complex term often only means "use an LLM if allowed"; multiple
        # terms such as anomaly + drilldown + attribution indicate a high-risk
        # reasoning task that should start on the large tier rather than spend
        # extra small-model turns.
        score += min(0.60, 0.25 + 0.15 * len(matched_complex_terms))
        reasons.append("complex_query_term")
        if len(matched_complex_terms) > 1:
            reasons.append("multiple_complex_terms")
    if data.get("status") in ("need_clarification", "blocked", "error", "no_answer", "unsupported"):
        score += 0.10; reasons.append("non_ok_route")
    if not reasons:
        reasons.append("simple_metric_route")
    return min(1.0, score), reasons


def choose_model_for_stage(query, plan=None, stage="route", use_llm=None, llm_policy=None,
                           small_model=None, large_model=None):
    policy = normalize_llm_policy(use_llm=use_llm, llm_policy=llm_policy)
    score, reasons = estimate_query_complexity(query, plan)
    small_model = small_model or DEFAULT_SMALL_MODEL
    large_model = large_model or DEFAULT_LARGE_MODEL
    budget = DEFAULT_STAGE_BUDGETS.get(stage, 1024)
    if policy == POLICY_OFF:
        return ModelRouteDecision(TIER_NO_LLM, None, stage, ["policy_off"] + reasons,
                                  score, 1.0, budget, policy)
    if policy == POLICY_LARGE_ONLY:
        return ModelRouteDecision(TIER_LARGE, large_model, stage, ["policy_large_only"] + reasons,
                                  score, 0.85, budget, policy)
    if policy == POLICY_SMALL_ONLY:
        return ModelRouteDecision(TIER_SMALL, small_model, stage, ["policy_small_only"] + reasons,
                                  score, 0.80, budget, policy)
    # shadow records what auto would do but keeps runtime no-LLM unless caller opts in.
    if policy == POLICY_SHADOW:
        tier = TIER_LARGE if score >= 0.55 else TIER_SMALL
        model = large_model if tier == TIER_LARGE else small_model
        return ModelRouteDecision(tier, model, stage, ["shadow"] + reasons,
                                  score, 0.80, budget, policy, shadow=True)
    if score >= 0.55:
        return ModelRouteDecision(TIER_LARGE, large_model, stage, ["auto_large"] + reasons,
                                  score, 0.80, budget, policy)
    return ModelRouteDecision(TIER_SMALL, small_model, stage, ["auto_small"] + reasons,
                              score, 0.85, budget, policy)


def should_escalate_to_large(error_code=None, validation_errors=None, decision=None):
    """Only semantic/schema failures may escalate; execution/permission/no-data may not."""
    blocked = set(["permission_denied", "blocked", "sql_execution_error", "db_timeout",
                   "empty_result", "no_data", "evidence_missing", "evidence_ttl_expired"])
    if error_code in blocked:
        return False, "non_semantic_failure_must_not_escalate"
    errors = validation_errors or []
    if error_code in ("schema_invalid", "low_confidence", "missing_required_slots", "semantic_ambiguity"):
        return True, error_code
    if any(e in ("schema_invalid", "missing_required_slots", "semantic_ambiguity") for e in errors):
        return True, "validation_failure"
    return False, "no_escalation_trigger"


__all__ = ["TIER_NO_LLM", "TIER_SMALL", "TIER_LARGE", "POLICY_OFF", "POLICY_AUTO",
           "POLICY_SMALL_ONLY", "POLICY_LARGE_ONLY", "POLICY_SHADOW",
           "ModelRouteDecision", "ModelInvocationEnvelope", "normalize_llm_policy",
           "estimate_query_complexity", "choose_model_for_stage", "should_escalate_to_large"]
