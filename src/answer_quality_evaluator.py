# -*- coding: utf-8 -*-
"""Per-turn answer evaluation for temporary evidence suites.

The evaluator is deterministic and intentionally simple: it compares the
observable answer text against expected facts, forbidden distractors, evidence
mode, context links and usefulness signals.  It is designed for fast regression
feedback, not for judging open-ended prose style.
"""
from __future__ import unicode_literals

import re

try:
    unicode
except NameError:  # pragma: no cover
    unicode = str


def _text(value):
    if value is None:
        return u""
    if isinstance(value, unicode):
        return value
    try:
        return value.decode("utf-8", "ignore")
    except Exception:
        return unicode(value)


def _dict(value):
    return value if isinstance(value, dict) else {}


def answer_text(result):
    result = _dict(result)
    report = _dict(result.get("report"))
    envelope = _dict(result.get("answer_envelope"))
    parts = [result.get("answer"), result.get("summary"), report.get("summary"),
             report.get("answer"), report.get("conclusion"), envelope.get("user_answer")]
    structured = _dict(envelope.get("structured_answer"))
    parts.extend([structured.get("summary"), structured.get("key_findings"), structured.get("limitations")])
    return u"\n".join([_text(x) for x in parts if x])


def _contains_all(text, items):
    low = text.lower()
    return [item for item in items or [] if _text(item).lower() not in low]


def _contains_any(text, items):
    low = text.lower()
    return [item for item in items or [] if _text(item).lower() in low]


def evidence_mode(result, text=None):
    result = _dict(result)
    text = text if text is not None else answer_text(result)
    if result.get("status") in ("blocked", "pending_human_review"):
        return "blocked"
    if result.get("status") in ("need_clarification", "unsupported"):
        return "clarification_or_unsupported"
    if result.get("status") == "degraded" or u"受限证据" in text or u"尚未由当前数据源核验" in text:
        return "limited_analysis"
    evidence = (_dict(result.get("answer_envelope")).get("evidence_refs") or
                (_dict(result.get("diagnostics")).get("evidence_cards") or []))
    if evidence:
        return "verified_data"
    return "ungrounded"


def evaluate_turn(query, result, expected=None):
    expected = _dict(expected)
    result = _dict(result)
    text = answer_text(result)
    mode = evidence_mode(result, text)
    missing_facts = _contains_all(text, expected.get("expected_facts") or [])
    missing_hypotheses = _contains_all(text, expected.get("expected_hypotheses") or [])
    forbidden_hits = _contains_any(text, expected.get("forbidden_claims") or [])
    required_actions = expected.get("expected_actions") or []
    missing_actions = _contains_all(text, required_actions)
    expected_modes = expected.get("expected_evidence_modes") or []
    mode_ok = (not expected_modes) or mode in expected_modes
    context_required = bool(expected.get("require_parent_context"))
    follow = _dict(result.get("follow_up_context"))
    context_ok = (not context_required) or bool(result.get("parent_task_id") or follow.get("parent_task_id"))

    credibility = 60
    credibility -= min(20, 8 * len(missing_facts))
    credibility -= min(10, 10 * len(forbidden_hits))
    credibility -= 10 if not mode_ok else 0
    credibility -= 10 if mode == "ungrounded" and re.search(r"\d+(?:\.\d+)?\s*(%|％|元|万|亿|件|单|人)", text) else 0
    credibility = max(0, credibility)

    usefulness = 40
    usefulness -= 10 if not context_ok else 0
    usefulness -= min(10, 4 * len(missing_hypotheses))
    usefulness -= min(10, 4 * len(missing_actions))
    usefulness -= 10 if not text.strip() else 0
    usefulness = max(0, usefulness)

    score = credibility + usefulness
    issues = []
    if missing_facts:
        issues.append({"code": "missing_expected_facts", "items": missing_facts})
    if missing_hypotheses:
        issues.append({"code": "missing_expected_hypotheses", "items": missing_hypotheses})
    if forbidden_hits:
        issues.append({"code": "forbidden_claims_hit", "items": forbidden_hits})
    if not mode_ok:
        issues.append({"code": "wrong_evidence_mode", "expected": expected_modes, "actual": mode})
    if not context_ok:
        issues.append({"code": "missing_parent_context"})
    if missing_actions:
        issues.append({"code": "missing_expected_actions", "items": missing_actions})

    return {
        "query": query,
        "status": result.get("status"),
        "evidence_mode": mode,
        "score": int(score),
        "credibility_score": int(credibility),
        "usefulness_score": int(usefulness),
        "dimensions": {
            "fact_coverage": max(0, 20 - min(20, 8 * len(missing_facts))),
            "distractor_immunity": max(0, 10 - min(10, 10 * len(forbidden_hits))),
            "evidence_boundary": 10 if mode_ok else 0,
            "numeric_grounding_safety": 0 if (mode == "ungrounded" and re.search(r"\d+(?:\.\d+)?\s*(%|％|元|万|亿|件|单|人)", text)) else 10,
            "context_continuity": 10 if context_ok else 0,
            "hypothesis_value": max(0, 10 - min(10, 4 * len(missing_hypotheses))),
            "actionability": max(0, 10 - min(10, 4 * len(missing_actions))),
            "completeness": 10 if text.strip() else 0,
        },
        "issues": issues,
        "answer_excerpt": text[:800],
    }


def summarize_turn_scores(items):
    items = items or []
    total = len(items)
    avg = round(sum(x.get("score", 0) for x in items) / float(max(1, total)), 2)
    by_issue = {}
    by_mode = {}
    pass_count = 0
    for item in items:
        if item.get("score", 0) >= 75:
            pass_count += 1
        by_mode[item.get("evidence_mode") or "unknown"] = by_mode.get(item.get("evidence_mode") or "unknown", 0) + 1
        for issue in item.get("issues") or []:
            code = issue.get("code")
            by_issue[code] = by_issue.get(code, 0) + 1
    return {"total_turns": total, "avg_score": avg, "pass_count": pass_count,
            "pass_rate": round(pass_count / float(max(1, total)), 4),
            "issue_hotspots": by_issue, "evidence_mode_distribution": by_mode}


__all__ = ["evaluate_turn", "summarize_turn_scores", "answer_text", "evidence_mode"]
