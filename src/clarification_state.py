# -*- coding: utf-8 -*-
"""Recoverable clarification state with optional durable storage.

Router owns whether clarification is needed; this module owns only the pending
plan and validates option IDs. The store boundary permits a file/database/redis
implementation later without changing AgentFacade orchestration.
"""
from __future__ import unicode_literals

import copy
import json
import os
import time

from semantic_registry import validate_plan_semantics


class InMemoryClarificationStore(object):
    def __init__(self):
        self.data = {}
    def get(self, session_id):
        value = self.data.get(session_id)
        return copy.deepcopy(value) if value else None
    def set(self, session_id, value):
        self.data[session_id] = copy.deepcopy(value)
    def delete(self, session_id):
        return self.data.pop(session_id, None) is not None


class RepositoryClarificationStore(object):
    """Adapter from ClarificationStateMachine store API to repository contract."""
    def __init__(self, repository, access):
        self.repository = repository
        self.access = access
    def get(self, session_id):
        return self.repository.get_clarification(self.access, session_id)
    def set(self, session_id, value):
        return self.repository.save_clarification(self.access, session_id, value)
    def delete(self, session_id):
        return self.repository.delete_clarification(self.access, session_id)


class JsonFileClarificationStore(object):
    """Small default-friendly durable store. Never stores raw query results."""
    def __init__(self, path):
        self.path = path
    def _load(self):
        if not self.path or not os.path.exists(self.path):
            return {}
        try:
            with open(self.path, "r") as f:
                return json.load(f) or {}
        except Exception:
            return {}
    def _save(self, data):
        parent = os.path.dirname(os.path.abspath(self.path))
        if parent and not os.path.exists(parent):
            os.makedirs(parent)
        temp = self.path + ".tmp"
        with open(temp, "w") as f:
            json.dump(data, f, ensure_ascii=False, sort_keys=True)
        if os.path.exists(self.path):
            try:
                os.remove(self.path)
            except Exception:
                pass
        os.rename(temp, self.path)
    def get(self, session_id):
        return self._load().get(session_id)
    def set(self, session_id, value):
        data = self._load(); data[session_id] = value; self._save(data)
    def delete(self, session_id):
        data = self._load()
        existed = session_id in data
        if existed:
            del data[session_id]; self._save(data)
        return existed


class ClarificationStateMachine(object):
    def __init__(self, store=None, ttl_seconds=1800, now=None):
        self.store = store or InMemoryClarificationStore()
        self.ttl_seconds = ttl_seconds
        self.now = now or time.time

    def _record(self, session_id):
        record = self.store.get(session_id)
        if not record:
            return None
        created = record.get("created_at", 0)
        if self.ttl_seconds is not None and self.now() - created > self.ttl_seconds:
            self.store.delete(session_id)
            return None
        return record

    def begin(self, session_id, query, plan, task_id=None):
        data = plan.to_dict() if hasattr(plan, "to_dict") else dict(plan or {})
        clarification = dict(data.get("clarification") or {})
        # Preserve the public v1 contract for existing clients; state_version
        # identifies the durable/TTL storage enhancement introduced in R7.
        record = {"contract": "clarification_state_v1", "state_version": "v2", "session_id": session_id,
                  "query": query, "task_id": task_id or data.get("task_id"),
                  "plan": copy.deepcopy(data), "clarification": clarification,
                  "options": list(clarification.get("options") or []),
                  "created_at": self.now(), "expires_at": self.now() + self.ttl_seconds if self.ttl_seconds is not None else None}
        self.store.set(session_id, record)
        return self.describe(session_id)

    def has_pending(self, session_id):
        return self._record(session_id) is not None

    def describe(self, session_id):
        record = self._record(session_id)
        if not record:
            return None
        return {"contract": record["contract"], "session_id": record["session_id"],
                "task_id": record.get("task_id"), "query": record.get("query"),
                "question": record.get("clarification", {}).get("question"),
                "options": copy.deepcopy(record.get("options") or []), "pending": True,
                "expires_at": record.get("expires_at")}

    def cancel(self, session_id):
        return self.store.delete(session_id)

    def resolve(self, session_id, choice_id):
        record = self._record(session_id)
        if not record:
            return {"status": "error", "reason": "no_clarification_pending_or_expired"}
        choice_id = (choice_id or "").strip()
        option_ids = [item.get("id") for item in record.get("options") or []]
        if choice_id not in option_ids:
            return {"status": "need_clarification", "reason": "invalid_clarification_choice",
                    "clarification": record.get("clarification"), "valid_option_ids": option_ids}
        plan = copy.deepcopy(record["plan"])
        plan["status"] = "ok"; plan["clarification"] = None
        plan["resume_payload"] = {"contract": "clarification_resume_v1", "choice_id": choice_id,
                                  "original_task_id": record.get("task_id")}
        if choice_id == "breakdown":
            plan["intent"] = "breakdown"
            # A clarification option must never turn an otherwise governed plan
            # into an invalid executable plan.  Retain only dimensions admitted
            # by the same semantic registry used by the execution boundary.
            original_dimensions = list(plan.get("dimensions") or ["channel"])
            candidate = dict(plan)
            candidate["dimensions"] = original_dimensions
            semantic = validate_plan_semantics(candidate)
            semantic_data = semantic.to_dict() if hasattr(semantic, "to_dict") else dict(semantic or {})
            rejected = set([item.get("value") for item in semantic_data.get("errors", [])
                            if item.get("field") == "dimensions"])
            admitted = [dimension for dimension in original_dimensions if dimension not in rejected]
            plan["dimensions"] = admitted or ["channel"]
            plan["resume_payload"]["dimension_resolution"] = {
                "contract": "clarification_dimension_resolution_v1",
                "requested_dimensions": original_dimensions,
                "admitted_dimensions": list(plan["dimensions"]),
                "rejected_dimensions": [dimension for dimension in original_dimensions if dimension not in plan["dimensions"]],
                "semantic_version": (semantic_data.get("metadata") or {}).get("semantic_version"),
            }
        elif choice_id == "metric_query":
            plan["intent"] = "metric_query"; plan["dimensions"] = []
        self.store.delete(session_id)
        return {"status": "ok", "choice_id": choice_id, "plan": plan,
                "original_query": record.get("query"), "parent_task_id": record.get("task_id")}


__all__ = ["ClarificationStateMachine", "InMemoryClarificationStore", "JsonFileClarificationStore", "RepositoryClarificationStore"]
