# -*- coding: utf-8 -*-
"""Multi-dimension benchmark scoring for the Agent Harness.

Pure-function module. No side effects, no business logic.
"""
from __future__ import unicode_literals

REPORT_REQUIRED_SECTIONS = [
    "headline", "summary", "key_findings", "evidence",
    "chart", "caveats", "recommendations", "methodology",
]

FAILURE_TYPE_REMAP = {
    "status_mismatch": "routing_error",
    "intent_mismatch": "routing_error",
    "route_mismatch": "routing_error",
    "task_type_mismatch": "planning_error",
    "metric_mismatch": "planning_error",
    "dimension_mismatch": "planning_error",
    "execution_error": "execution_error",
    "unexpected_exception": "execution_error",
    "contract_missing_key": "contract_error",
    "contract_error": "contract_error",
    "human_review_mismatch": "governance_error",
    "approval_mismatch": "governance_error",
    "risk_mismatch": "governance_error",
    "react_observation_missing": "analysis_error",
    "react_action_mismatch": "analysis_error",
    "trace_missing_event": "analysis_error",
    "trace_contract_violation": "trace_error",
}


def _as_dict(value):
    return value if isinstance(value, dict) else {}


def _as_list(value):
    return list(value) if isinstance(value, (list, tuple)) else []


def _rate(hit, total):
    if not total:
        return None
    return round(hit * 1.0 / total, 4)


def _p95(values):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[int((len(ordered) - 1) * 0.95)]


def classify_failure(failure_type):
    """Map raw failure_type to a semantic category."""
    if not failure_type:
        return None
    return FAILURE_TYPE_REMAP.get(failure_type, failure_type)


def failure_stage(failure_type):
    """Project a classified failure into one stable execution stage."""
    if failure_type == "routing_error":
        return "routing"
    if failure_type == "planning_error":
        return "planning"
    if failure_type == "execution_error":
        return "execution"
    if failure_type == "analysis_error":
        return "analysis"
    if failure_type in ("contract_error", "report_error"):
        return "report"
    if failure_type == "governance_error":
        return "governance"
    if failure_type == "trace_error":
        return "trace"
    return "unknown"


def score_report_sections(report):
    """Return fraction of required report sections present in report dict."""
    report = _as_dict(report)
    present = sum(1 for section in REPORT_REQUIRED_SECTIONS if section in report)
    return round(present * 1.0 / len(REPORT_REQUIRED_SECTIONS), 4)


def score_chart_suitability(result, expected):
    """Return 1 if result chart type matches expected_chart_type list, else 0."""
    expected_types = expected.get("expected_chart_type") or []
    if not expected_types:
        return None
    report = _as_dict(result.get("report"))
    chart = _as_dict(report.get("chart") or result.get("chart"))
    got_type = chart.get("type") or chart.get("chart_type")
    return 1 if got_type in expected_types else 0


def score_clarification_correctness(result, expected):
    if expected.get("status") != "need_clarification":
        return None
    return 1 if result.get("status") == "need_clarification" else 0


def _score_slots(result, expected):
    checks = []
    if expected.get("metric") is not None:
        checks.append(result.get("metric") == expected.get("metric"))
    if expected.get("dimensions") is not None:
        checks.append(set(_as_list(result.get("dimensions"))) ==
                      set(_as_list(expected.get("dimensions"))))
    if not checks:
        return None
    return 1 if all(checks) else 0


def _score_tool_call(case, result):
    tool_id = case.get("tool_id")
    if not tool_id:
        return None
    return 1 if result.get("tool_id") == tool_id else 0


def score_case(case, evaluated):
    """Score one evaluated case without executing or changing agent behavior."""
    case = _as_dict(case)
    evaluated = _as_dict(evaluated)
    expected = _as_dict(evaluated.get("expected") or case.get("expected"))
    result = _as_dict(evaluated.get("result"))
    report = _as_dict(result.get("report"))

    status_ok = None
    if expected.get("status") is not None:
        status_ok = 1 if result.get("status") == expected.get("status") else 0
    intent_ok = None
    if expected.get("intent") is not None:
        intent_ok = 1 if result.get("intent") == expected.get("intent") else 0
    task_type_ok = None
    if expected.get("task_type") is not None:
        task_type_ok = 1 if result.get("task_type") == expected.get("task_type") else 0
    metric_ok = None
    if expected.get("metric") is not None:
        metric_ok = 1 if result.get("metric") == expected.get("metric") else 0
    dimension_ok = None
    if expected.get("dimensions") is not None:
        dimension_ok = 1 if set(_as_list(result.get("dimensions"))) == set(_as_list(expected.get("dimensions"))) else 0

    is_multiturn = (case.get("category") == "follow_up" or
                    "multiturn" in _as_list(case.get("tags")))
    raw_failure = evaluated.get("failure_type")
    semantic_failure = classify_failure(raw_failure)
    trace = _as_list(evaluated.get("trace"))
    trace_validation = _as_dict(evaluated.get("trace_validation"))
    trace_contract_valid = None
    if trace_validation:
        trace_contract_valid = 1 if trace_validation.get("valid") is True else 0

    return {
        "id": evaluated.get("id") or case.get("id"),
        "category": evaluated.get("category") or case.get("category"),
        "passed": bool(evaluated.get("passed")),
        "terminal_status_accuracy": status_ok,
        "status_accuracy": status_ok,
        "intent_accuracy": intent_ok,
        "slot_accuracy": _score_slots(result, expected),
        "task_type_accuracy": task_type_ok,
        "metric_accuracy": metric_ok,
        "dimension_accuracy": dimension_ok,
        "tool_call_accuracy": _score_tool_call(case, result),
        "chart_suitability": score_chart_suitability(result, expected),
        "report_section_completeness": score_report_sections(report) if report else (0.0 if result.get("status") == "ok" else None),
        "clarification_correctness": score_clarification_correctness(result, expected),
        "resume_success_rate": 1 if is_multiturn and evaluated.get("passed") else (0 if is_multiturn else None),
        "multiturn_completion_rate": 1 if is_multiturn and evaluated.get("passed") else (0 if is_multiturn else None),
        "duration_ms": evaluated.get("duration_ms"),
        "trace_id": result.get("trace_id"),
        "task_id": result.get("task_id"),
        "session_id": result.get("session_id"),
        "trace_events": [event.get("name") or event.get("stage") for event in trace if isinstance(event, dict)],
        "trace_contract_validity": trace_contract_valid,
        "trace_validation_errors": trace_validation.get("errors") or [],
        "raw_failure_type": raw_failure,
        "semantic_failure_type": semantic_failure,
        "failure_stage": failure_stage(semantic_failure) if semantic_failure else None,
    }


def score_suite(results):
    """Aggregate additive quality metrics and per-case debug projections."""
    scored = [score_case(_as_dict(item.get("case")), item) for item in (results or [])]
    total = len(scored)
    passed = sum(1 for item in scored if item["passed"])

    def dimension_rate(field):
        values = [item[field] for item in scored if item[field] is not None]
        return _rate(sum(values), len(values))

    failure_breakdown = {}
    failure_stage_breakdown = {}
    category_metrics = {}
    for item in scored:
        failure = item["semantic_failure_type"]
        if failure:
            failure_breakdown[failure] = failure_breakdown.get(failure, 0) + 1
            stage = item.get("failure_stage") or "unknown"
            failure_stage_breakdown[stage] = failure_stage_breakdown.get(stage, 0) + 1
        category = item.get("category") or "uncategorized"
        row = category_metrics.setdefault(category, {"total": 0, "passed": 0, "failed": 0, "failure_breakdown": {}})
        row["total"] += 1
        row["passed"] += 1 if item["passed"] else 0
        row["failed"] += 0 if item["passed"] else 1
        if failure:
            row["failure_breakdown"][failure] = row["failure_breakdown"].get(failure, 0) + 1
    for row in category_metrics.values():
        row["pass_rate"] = _rate(row["passed"], row["total"]) or 0.0

    status_acc = dimension_rate("status_accuracy")
    task_type_acc = dimension_rate("task_type_accuracy")
    metric_acc = dimension_rate("metric_accuracy")
    dimension_acc = dimension_rate("dimension_accuracy")
    report_compl = dimension_rate("report_section_completeness")
    clarification_acc = dimension_rate("clarification_correctness")
    trace_contract_validity = dimension_rate("trace_contract_validity")
    weights = [(status_acc, 3), (task_type_acc, 2), (metric_acc, 2),
               (dimension_acc, 1), (report_compl, 1), (clarification_acc, 1)]
    active = [(value, weight) for value, weight in weights if value is not None]
    total_score = round(sum(value * weight for value, weight in active) /
                        sum(weight for value, weight in active), 4) if active else 0.0
    durations = [item["duration_ms"] for item in scored if isinstance(item.get("duration_ms"), (int, float))]

    return {
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate": _rate(passed, total) or 0.0,
        "total_score": total_score,
        "terminal_status_accuracy": dimension_rate("terminal_status_accuracy"),
        "status_accuracy": status_acc,
        "intent_accuracy": dimension_rate("intent_accuracy"),
        "slot_accuracy": dimension_rate("slot_accuracy"),
        "task_type_accuracy": task_type_acc,
        "metric_accuracy": metric_acc,
        "dimension_accuracy": dimension_acc,
        "chart_suitability": dimension_rate("chart_suitability"),
        "report_section_completeness": report_compl,
        "clarification_correctness": clarification_acc,
        "tool_call_accuracy": dimension_rate("tool_call_accuracy"),
        "trace_contract_validity": trace_contract_validity,
        "resume_success_rate": dimension_rate("resume_success_rate"),
        "multiturn_completion_rate": dimension_rate("multiturn_completion_rate"),
        "avg_steps": None,
        "avg_latency_ms": round(sum(durations) * 1.0 / len(durations), 4) if durations else None,
        "p95_latency_ms": _p95(durations),
        "badcase_distribution": failure_breakdown,
        "failure_breakdown": failure_breakdown,
        "failure_stage_breakdown": failure_stage_breakdown,
        "category_metrics": category_metrics,
        "case_scores": scored,
    }


__all__ = [
    "score_case", "score_suite", "classify_failure", "failure_stage",
    "score_report_sections", "score_chart_suitability",
    "FAILURE_TYPE_REMAP", "REPORT_REQUIRED_SECTIONS",
]
