# -*- coding: utf-8 -*-
"""Multi-turn conversation session manager for the Data Agent.

Maintains conversation state across multiple queries, enabling follow-up
questions that build on previous context (e.g., "那各渠道的呢？").

Persists sessions to disk as JSON for cross-restart survival.
"""

import json
import os
import time
import uuid

import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from contracts import normalize_status
from followup_policy import merge_context


class Turn(object):
    def __init__(self, query, result, timestamp=None, diagnosis=None):
        self.query = query
        self.result = result
        self.timestamp = timestamp if timestamp is not None else time.time()
        self.diagnosis = diagnosis

    @property
    def status(self):
        return self.result.get("status", "error")


# Valid session states
SESSION_STATE_IDLE = "idle"
SESSION_STATE_CLARIFYING = "clarifying"
SESSION_STATE_PLANNED = "planned"
SESSION_STATE_EXECUTING = "executing"
SESSION_STATE_ANSWERED = "answered"
SESSION_STATE_FOLLOW_UP = "follow_up"
SESSION_STATE_DRILL_DOWN = "drill_down"

_VALID_STATES = {SESSION_STATE_IDLE, SESSION_STATE_CLARIFYING, SESSION_STATE_PLANNED,
                 SESSION_STATE_EXECUTING, SESSION_STATE_ANSWERED, SESSION_STATE_FOLLOW_UP,
                 SESSION_STATE_DRILL_DOWN}


class Session(object):
    def __init__(self, session_id):
        self.id = session_id
        self.turns = []
        self.created_at = time.time()
        self.updated_at = time.time()
        self.state = SESSION_STATE_IDLE
        self.context = {
            "model": None,
            "metric": None,
            "dimensions": [],
            "time_range": None,
            "filters": {},
            "task_type": None,
            "current_dataid": None,
        }

    def transition(self, new_state):
        if new_state not in _VALID_STATES:
            raise ValueError("invalid session state: %s" % new_state)
        self.state = new_state

    def last_turn(self):
        return self.turns[-1] if self.turns else None

    def last_status(self):
        t = self.last_turn()
        return t.status if t else "ok"

    def history_summary(self):
        from context_manager import RollingSummarizer
        if not self.turns:
            return "No history."
        rs = RollingSummarizer(max_summary_tokens=300)
        for t in self.turns:
            rs.add_turn(t.query, t.result)
        return rs.get_injectable_context()


SESSION_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, "sessions"))
if not os.path.exists(SESSION_DIR):
    os.makedirs(SESSION_DIR)


class SessionManager(object):
    def __init__(self):
        self.sessions = {}

    def create(self, session_id=None):
        sid = session_id or uuid.uuid4().hex[:12]
        self.sessions[sid] = Session(sid)
        return sid

    def get(self, session_id):
        return self.sessions.get(session_id)

    def run(self, session_id, query, use_db=True, use_llm=False, tracer=None):
        session = self.sessions.get(session_id)
        if session is None:
            raise KeyError("Session '%s' not found. Use create() first." % session_id)

        # Detect follow-up intent and resolve state before routing
        from followup_policy import resolve_followup
        follow_up_ctx = resolve_followup(query, session)

        if follow_up_ctx:
            next_state = follow_up_ctx.get("state", SESSION_STATE_FOLLOW_UP)
            if next_state not in _VALID_STATES:
                next_state = SESSION_STATE_FOLLOW_UP
            session.transition(next_state)
        else:
            session.transition(SESSION_STATE_EXECUTING)

        enriched_query = merge_context(query, session)
        from graph_agent import run_graph
        result = run_graph(enriched_query, use_db=use_db, use_llm=use_llm, tracer=tracer)

        if normalize_status(result.get("status")) == "need_clarification":
            session.transition(SESSION_STATE_CLARIFYING)
            session.turns.append(Turn(query=query, result=result))
            session.updated_at = time.time()
            return result

        if result.get("status") == "ok":
            session.context["model"] = result.get("model") or session.context.get("model")
            session.context["metric"] = result.get("metric") or session.context.get("metric")
            session.context["dimensions"] = result.get("dimensions") or session.context.get("dimensions", [])
            session.context["time_range"] = result.get("time_range") or session.context.get("time_range")
            session.context["current_dataid"] = result.get("current_dataid")
            if follow_up_ctx and follow_up_ctx.get("filters"):
                filters = dict(session.context.get("filters") or {})
                filters.update(follow_up_ctx.get("filters") or {})
                session.context["filters"] = filters
            if follow_up_ctx and follow_up_ctx.get("task_type"):
                session.context["task_type"] = follow_up_ctx.get("task_type")
            session.transition(SESSION_STATE_ANSWERED)

        diagnosis = result.get("diagnosis")
        session.turns.append(Turn(query=query, result=result, diagnosis=diagnosis))
        session.updated_at = time.time()
        return result

    def run_react(self, session_id, query, use_db=True, tracer=None):
        session = self.sessions.get(session_id)
        if session is None:
            raise KeyError("Session '%s' not found." % session_id)
        return self.run(session_id, query, use_db=use_db, use_llm=False, tracer=tracer)
