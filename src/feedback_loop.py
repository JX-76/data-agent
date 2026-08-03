# -*- coding: utf-8 -*-
"""Governed low-score feedback loop for Agent evaluation.

Evaluation results can propose changes to intent rules, semantic dictionaries or
execution policies, but this module never edits them automatically. A human
owner must accept a proposal before it becomes an implementation work item.
"""
from __future__ import unicode_literals

import time
import uuid


class FeedbackLoop(object):
    def __init__(self, pass_threshold=0.8):
        self.pass_threshold = pass_threshold
        self._proposals = []

    def ingest(self, evaluation, query="", trace_id=None):
        data = dict(evaluation or {})
        scores = list(data.get("scores") or [])
        total = data.get("total_count") or len(scores)
        passed = data.get("pass_count")
        if passed is None:
            passed = len([x for x in scores if x.get("pass")])
        score = float(passed) / total if total else 1.0
        if score >= self.pass_threshold:
            return {"contract": "feedback_proposal_v1", "created": False, "score": score, "reason": "score_above_threshold"}
        failed = [x for x in scores if not x.get("pass")]
        kinds = self._proposal_kinds(failed)
        proposal = {
            "contract": "feedback_proposal_v1", "proposal_id": uuid.uuid4().hex,
            "query": query, "trace_id": trace_id, "score": score,
            "status": "pending_human_review", "created_at": time.time(),
            "failed_dimensions": [x.get("name") for x in failed],
            "recommended_targets": kinds,
            "suggested_actions": self._actions(kinds),
            "evaluation": {"pass_count": passed, "total_count": total},
        }
        self._proposals.append(proposal)
        return dict(proposal, created=True)

    def pending(self):
        return [dict(x) for x in self._proposals if x.get("status") == "pending_human_review"]

    def decide(self, proposal_id, decision, reviewer_id=None, note=None):
        for proposal in self._proposals:
            if proposal.get("proposal_id") == proposal_id:
                if decision not in ("approve", "reject"):
                    return {"status": "error", "reason": "invalid_feedback_decision"}
                proposal["status"] = "approved_backlog" if decision == "approve" else "rejected"
                proposal["review"] = {"reviewer_id": reviewer_id, "note": note, "decision": decision, "reviewed_at": time.time()}
                return dict(proposal)
        return {"status": "error", "reason": "feedback_proposal_not_found"}

    def _proposal_kinds(self, failed):
        names = [x.get("name") for x in failed]
        kinds = []
        if any(x in ("intent", "metric", "model", "dimensions") for x in names):
            kinds.append("semantic_or_routing_rules")
        if "sql_structure" in names:
            kinds.append("sql_strategy")
        if "tool_chain" in names:
            kinds.append("tool_selection_policy")
        if "diagnosis" in names:
            kinds.append("reliability_or_result_quality")
        return kinds or ["manual_case_review"]

    def _actions(self, kinds):
        mapping = {
            "semantic_or_routing_rules": "Review the failed query and add a versioned semantic synonym or intent rule only after approval.",
            "sql_strategy": "Add a minimal regression case, then improve the SQL compilation strategy without weakening preflight checks.",
            "tool_selection_policy": "Review tool trace and update the governed selection policy or registry capability declaration.",
            "reliability_or_result_quality": "Inspect diagnostics, trace and quality checks; create a bounded retry or validation improvement.",
            "manual_case_review": "Label the case and decide whether it is a supported business scenario before changing agent behavior.",
        }
        return [mapping[x] for x in kinds]


__all__ = ["FeedbackLoop"]
