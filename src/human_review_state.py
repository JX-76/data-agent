# -*- coding: utf-8 -*-
"""Safe, session-scoped approval state for high-risk Agent requests.

The policy decides whether review is required. This module stores the reviewed
plan and only permits an explicit approve/reject decision, so high-risk work
cannot accidentally continue through a normal follow-up message.
"""
from __future__ import unicode_literals

import copy
import time


class InMemoryHumanReviewStore(object):
    def __init__(self):
        self.data = {}
    def get(self, session_id):
        return copy.deepcopy(self.data.get(session_id)) if session_id in self.data else None
    def set(self, session_id, value):
        self.data[session_id] = copy.deepcopy(value)
    def delete(self, session_id):
        return self.data.pop(session_id, None) is not None


class RepositoryHumanReviewStore(object):
    def __init__(self, repository, access):
        self.repository = repository
        self.access = access
    def get(self, session_id):
        return self.repository.get_review(self.access, session_id)
    def set(self, session_id, value):
        return self.repository.save_review(self.access, session_id, value)
    def delete(self, session_id):
        return self.repository.delete_review(self.access, session_id)


class HumanReviewStateMachine(object):
    def __init__(self, store=None):
        self.store = store or InMemoryHumanReviewStore()

    def begin(self, session_id, query, plan, risk_level=None, task_id=None, checklist=None):
        data = plan.to_dict() if hasattr(plan, "to_dict") else dict(plan or {})
        record = {
            "contract": "human_review_v1",
            "session_id": session_id,
            "query": query,
            "task_id": task_id or data.get("task_id"),
            "plan": copy.deepcopy(data),
            "risk_level": risk_level or data.get("risk_level") or "high",
            "checklist": list(checklist or data.get("review_checklist") or []),
            "created_at": time.time(),
        }
        self.store.set(session_id, record)
        return self.describe(session_id)

    def describe(self, session_id):
        record = self.store.get(session_id)
        if not record:
            return None
        return {
            "contract": record["contract"], "session_id": session_id,
            "task_id": record.get("task_id"), "query": record.get("query"),
            "risk_level": record.get("risk_level"), "review_checklist": list(record.get("checklist") or []),
            "pending": True,
        }

    def decide(self, session_id, decision, reviewer_id=None, note=None):
        record = self.store.get(session_id)
        if not record:
            return {"status": "error", "reason": "no_human_review_pending"}
        decision = (decision or "").strip().lower()
        if decision not in ("approve", "reject"):
            return {"status": "pending_human_review", "reason": "invalid_human_review_decision",
                    "valid_decisions": ["approve", "reject"], "human_review": self.describe(session_id)}
        self.store.delete(session_id)
        audit = {"contract": "human_review_v1", "decision": decision, "reviewer_id": reviewer_id,
                 "note": note, "reviewed_at": time.time(), "original_task_id": record.get("task_id")}
        if decision == "reject":
            return {"status": "blocked", "reason": "human_review_rejected", "human_review": audit,
                    "parent_task_id": record.get("task_id")}
        plan = copy.deepcopy(record["plan"])
        plan["status"] = "ok"
        plan["requires_human_review"] = False
        plan["approval_status"] = "approved"
        plan["human_review"] = audit
        plan["resume_payload"] = {"contract": "human_review_resume_v1", "original_task_id": record.get("task_id"),
                                  "reviewer_id": reviewer_id}
        return {"status": "ok", "plan": plan, "original_query": record.get("query"),
                "parent_task_id": record.get("task_id"), "human_review": audit}


__all__ = ["HumanReviewStateMachine", "InMemoryHumanReviewStore", "RepositoryHumanReviewStore"]
