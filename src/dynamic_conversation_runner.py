# -*- coding: utf-8 -*-
"""Response-aware dynamic conversation simulation.

The seed dataset contains only an initial question. Each later question is
computed from the actual preceding release envelope and is persisted in a
transcript. Provider connectivity/runtime failures are separate from business
turns and never consume the case's maximum business-turn budget.
"""
from __future__ import unicode_literals

import time
import uuid

PROVIDER_FAILURE_STATUSES = set(["unavailable", "timeout", "connection_error", "provider_runtime_error", "http_500"])
SAFE_TERMINALS = set(["blocked", "unsupported", "no_answer", "pending_human_review"])
DYNAMIC_QUALITY_REPORT_CONTRACT = "dynamic_conversation_quality_report_v1"


def is_provider_failure(envelope):
    assist = (envelope or {}).get("llm_assist") or {}
    return bool(assist.get("enabled")) and assist.get("status") in PROVIDER_FAILURE_STATUSES


def _has_usable_evidence(envelope):
    contract = (envelope or {}).get("answer_contract") or {}
    control = (envelope or {}).get("analysis_control") or {}
    return bool(contract.get("evidence_ids") or control.get("analysis_contract", {}).get("evidence_ids"))


def _summary(envelope):
    answer = (envelope or {}).get("answer") or {}
    return (answer.get("summary") or "")[:800]


def _safe_error(envelope):
    """Return only the public, structured failure projection."""
    value = (envelope or {}).get("safe_error") or {}
    return dict(value) if isinstance(value, dict) else {}


def choose_dynamic_followup(case, envelope, business_turn_index):
    """Derive a safe next question exclusively from the preceding envelope.

    Returns `(question, reason, terminal_reason)`. The question is generated
    at runtime; no follow-up is read from the seed dataset.
    """
    envelope = envelope or {}
    status = envelope.get("status") or "error"
    clarification = envelope.get("clarification") or {}
    sample_type = ((case or {}).get("metadata") or {}).get("sample_type")
    category = (case or {}).get("category") or ""

    if status in SAFE_TERMINALS:
        return None, "safe_terminal", "safe_terminal:%s" % status
    if status == "need_clarification":
        prompt = clarification.get("prompt") or clarification.get("message") or ""
        if "时间" in prompt or business_turn_index == 1:
            return "时间范围限定为最近 7 天，按当前租户全部店铺统计。", "clarification_time_scope", None
        return "请按当前租户、只读范围继续，并列出完成分析还需要的最小必要条件。", "clarification_safe_scope", None
    if status in ("evidence_limited", "no_evidence"):
        return "要完成这个结论还缺少哪些证据、时间范围或权限？请不要推测。", "request_evidence_gap", None
    if status == "error":
        return None, "non_provider_runtime_error", "runtime_error"
    if status != "ok":
        return None, "unrecognized_terminal", "terminal:%s" % status
    if sample_type == "negative":
        return None, "negative_case_safe_response", "negative_case_handled"
    if _has_usable_evidence(envelope) and business_turn_index >= 3:
        return None, "evidence_backed_analysis_complete", "analysis_complete"
    if business_turn_index == 1:
        if "comparison" in category or "period" in category:
            return "请补充与上一可比周期的对比、变化幅度和证据范围。", "drilldown_comparison", None
        return "请按最相关的渠道、地区或商品维度拆解，并说明贡献最大的项目。", "drilldown_dimension", None
    if business_turn_index == 2:
        return "请说明异常或变化的可能驱动因素，并区分已验证事实和待验证假设。", "drilldown_diagnosis", None
    return "请给出结论所依据的证据、数据版本、适用范围及建议持续监测的指标。", "request_lineage_and_monitoring", None


def sanitize_turn_envelope(envelope):
    """Keep audit-relevant public fields while avoiding raw/internal payloads."""
    envelope = envelope or {}
    answer_contract = envelope.get("answer_contract") or {}
    chain = envelope.get("release_chain") or {}
    return {
        "status": envelope.get("status"), "terminal": envelope.get("terminal"),
        "elapsed_ms": envelope.get("elapsed_ms"), "audit_id": envelope.get("audit_id"),
        "answer_summary": _summary(envelope),
        "clarification": envelope.get("clarification"),
        "llm_assist": envelope.get("llm_assist"),
        "safe_error": _safe_error(envelope),
        "evidence_ids": list(answer_contract.get("evidence_ids") or []),
        "analysis_evidence_ids": list(((envelope.get("analysis_control") or {}).get("analysis_contract") or {}).get("evidence_ids") or []),
        "release_chain_contract": chain.get("contract"),
        "release_chain_confidence": chain.get("overall_confidence"),
    }


def choose_resume_choice(case, envelope, business_turn_index):
    """Pick a safe clarification option from the actual response, if present."""
    clarification = (envelope or {}).get("clarification") or {}
    options = clarification.get("options") or []
    if not isinstance(options, list) or not options:
        return None
    ids = [item.get("id") for item in options if isinstance(item, dict)]
    category = (case or {}).get("category") or ""
    if ("breakdown" in ids and
            (business_turn_index > 1 or any(key in category for key in ("channel", "region", "category", "sku", "store")))):
        return "breakdown"
    if "metric_query" in ids:
        return "metric_query"
    return ids[0] if ids else None


class DynamicConversationRunner(object):
    contract = "dynamic_conversation_run_v1"

    def __init__(self, ask_fn, followup_fn, resume_fn=None, use_llm=True, max_provider_retries=1):
        self.ask_fn = ask_fn
        self.followup_fn = followup_fn
        self.resume_fn = resume_fn
        self.use_llm = bool(use_llm)
        self.max_provider_retries = max(0, int(max_provider_retries))

    def run_case(self, case, access_context=None):
        case = dict(case or {})
        run_id = "convrun_%s" % uuid.uuid4().hex[:16]
        session_id = "%s_%s" % (case.get("case_id") or "case", run_id[-6:])
        max_turns = int(case.get("max_turns") or 10)
        transcript = []
        business_turns = 0
        provider_failures = 0
        question = case.get("initial_question")
        mode = "initial"
        completion_reason = "max_turns_reached"

        while question and business_turns < max_turns:
            attempts = 0
            while True:
                started = time.time()
                if mode == "initial":
                    envelope = self.ask_fn(question, session_id=session_id, use_llm=self.use_llm, access_context=access_context)
                elif mode == "resume" and self.resume_fn is not None:
                    envelope = self.resume_fn(session_id=session_id, choice_id=question, access_context=access_context)
                else:
                    envelope = self.followup_fn(question, session_id=session_id, use_llm=self.use_llm, access_context=access_context)
                provider_failure = is_provider_failure(envelope)
                turn = {
                    "contract": "dynamic_conversation_turn_v1", "run_id": run_id,
                    "case_id": case.get("case_id"), "session_id": session_id,
                    "turn_mode": mode, "business_turn_index": business_turns + (0 if provider_failure else 1),
                    "provider_retry_index": attempts, "user_question": question,
                    "provider_failure": provider_failure, "valid_business_turn": not provider_failure,
                    "response": sanitize_turn_envelope(envelope),
                    "observed_latency_ms": int((time.time() - started) * 1000),
                }
                transcript.append(turn)
                if not provider_failure or attempts >= self.max_provider_retries:
                    break
                attempts += 1
                provider_failures += 1

            if provider_failure:
                provider_failures += 1
                completion_reason = "provider_unresolved"
                break
            business_turns += 1
            if (envelope or {}).get("status") == "need_clarification" and self.resume_fn is not None:
                choice_id = choose_resume_choice(case, envelope, business_turns)
                if choice_id:
                    turn["followup_policy"] = {"contract": "response_aware_followup_policy_v1", "reason": "resume_clarification_choice", "next_question": "resume:%s" % choice_id, "choice_id": choice_id, "terminal_reason": None}
                    question = choice_id
                    mode = "resume"
                    continue
            next_question, reason, terminal = choose_dynamic_followup(case, envelope, business_turns)
            turn["followup_policy"] = {"contract": "response_aware_followup_policy_v1", "reason": reason, "next_question": next_question, "terminal_reason": terminal}
            if terminal:
                completion_reason = terminal
                break
            question = next_question
            mode = "followup"

        return {"contract": self.contract, "run_id": run_id, "case_id": case.get("case_id"), "session_id": session_id, "sample_type": (case.get("metadata") or {}).get("sample_type"), "business_turn_count": business_turns, "provider_failure_count": provider_failures, "completion_reason": completion_reason, "transcript": transcript, "completed": completion_reason in ("analysis_complete", "negative_case_handled") or completion_reason.startswith("safe_terminal")}


def _last_response(run):
    transcript = (run or {}).get("transcript") or []
    return (transcript[-1].get("response") or {}) if transcript else {}


def evaluate_dynamic_runs(cases, runs):
    """Score observable multi-turn output contracts without judging business truth.

    This deterministic evaluator checks only declared safe outcomes, evidence
    availability for an ``ok`` final response, terminal behavior, and the
    public safe-error projection.  It deliberately does not score answer
    correctness, live RAG recall, or provider quality.
    """
    cases_by_id = dict((row.get("case_id"), row) for row in (cases or []) if row.get("case_id"))
    results = []
    badcases = []
    for run in list(runs or []):
        case_id = run.get("case_id")
        case = cases_by_id.get(case_id) or {}
        response = _last_response(run)
        status = response.get("status")
        expected = list(case.get("expected_safe_outcomes") or [])
        sample_type = (case.get("metadata") or {}).get("sample_type")
        errors = []
        if not case:
            errors.append("case_not_found")
        if not response:
            errors.append("missing_final_response")
        if expected and status not in expected:
            errors.append("unexpected_final_status:%s" % (status or "missing"))
        if status == "ok" and not (response.get("evidence_ids") or response.get("analysis_evidence_ids")):
            errors.append("ok_without_evidence_reference")
        if status == "error" and not response.get("safe_error"):
            errors.append("runtime_error_without_safe_projection")
        if sample_type == "negative" and status not in SAFE_TERMINALS and status not in ("evidence_limited", "need_clarification"):
            errors.append("negative_case_not_safely_contained")
        completion = run.get("completion_reason")
        if completion == "provider_unresolved":
            errors.append("provider_unresolved")
        category = "runtime_failure" if completion in ("runtime_error", "provider_unresolved") else "contract_error"
        if completion in ("runtime_error", "provider_unresolved"):
            category = "runtime_failure"
        elif any("evidence" in item for item in errors):
            category = "unsupported_claim"
        elif any("negative_case" in item or "unexpected_final_status" in item for item in errors):
            category = "policy_action_denial"
        result = {"contract": "dynamic_conversation_quality_result_v1", "case_id": case_id,
                  "sample_type": sample_type, "expected_safe_outcomes": expected,
                  "final_status": status, "completion_reason": completion,
                  "passed": not errors, "errors": errors, "failure_category": category if errors else None,
                  "audit_id": response.get("audit_id"),
                  "evidence_reference_count": len(response.get("evidence_ids") or []) + len(response.get("analysis_evidence_ids") or [])}
        results.append(result)
        if errors:
            badcases.append({"contract": "dynamic_conversation_badcase_v1", "case_id": case_id,
                             "failure_category": category, "errors": errors,
                             "audit_id": response.get("audit_id")})
    total = len(results)
    passed = sum(1 for item in results if item["passed"])
    ok_results = [item for item in results if item.get("final_status") == "ok"]
    negatives = [item for item in results if item.get("sample_type") == "negative"]
    return {"contract": DYNAMIC_QUALITY_REPORT_CONTRACT, "case_count": total,
            "passed_count": passed, "failed_count": total - passed, "passed": passed == total,
            "metrics": {"declared_safe_outcome_pass_rate": round(float(passed) / float(total or 1), 4),
                        "ok_output_evidence_reference_rate": round(float(sum(1 for item in ok_results if item["evidence_reference_count"] > 0)) / float(len(ok_results) or 1), 4),
                        "negative_case_safe_containment_rate": round(float(sum(1 for item in negatives if item["passed"])) / float(len(negatives) or 1), 4),
                        "audit_trace_coverage": round(float(sum(1 for item in results if item.get("audit_id"))) / float(total or 1), 4),
                        "provider_unresolved_count": sum(1 for item in results if item.get("completion_reason") == "provider_unresolved"),
                        "runtime_error_count": sum(1 for item in results if item.get("completion_reason") == "runtime_error")},
            "results": results, "badcases": badcases,
            "measurement_disclaimer": "Deterministic I/O contract evaluation only; it does not establish business-answer correctness, live retrieval quality, or production reliability."}


def summarize_runs(runs):
    runs = list(runs or [])
    runtime_errors = []
    for row in runs:
        if row.get("completion_reason") != "runtime_error":
            continue
        transcript = row.get("transcript") or []
        response = (transcript[-1].get("response") or {}) if transcript else {}
        runtime_errors.append({"case_id": row.get("case_id"), "safe_error": response.get("safe_error") or {}})
    return {"contract": "dynamic_conversation_report_v1", "case_count": len(runs), "completed_count": sum(1 for row in runs if row.get("completed")), "provider_unresolved_count": sum(1 for row in runs if row.get("completion_reason") == "provider_unresolved"), "runtime_error_count": len(runtime_errors), "runtime_errors": runtime_errors, "max_turns_reached_count": sum(1 for row in runs if row.get("completion_reason") == "max_turns_reached"), "business_turn_count": sum(row.get("business_turn_count", 0) for row in runs), "provider_failure_count": sum(row.get("provider_failure_count", 0) for row in runs), "measurement_disclaimer": "Dynamic follow-ups are deterministic response-aware test policy, not simulated human judgments or provider accuracy metrics."}


__all__ = ["DynamicConversationRunner", "choose_dynamic_followup", "choose_resume_choice", "is_provider_failure", "sanitize_turn_envelope", "summarize_runs", "evaluate_dynamic_runs", "DYNAMIC_QUALITY_REPORT_CONTRACT"]
