# -*- coding: utf-8 -*-
"""Phase 21-B: ReAct observation governance.

Every ReAct tool observation is compacted into an ``EvidenceCard`` and passed
through ``MemoryPolicy`` / ``TaskAnchor`` before it may enter the LLM context.
Raw result rows are never copied into memory or into the injectable reference;
only a compact ``OBSERVATION_REF`` (evidence id, dataid, row_count, columns,
status) is emitted. Anchor conflicts are quarantined; pivots/clarifications
signal the caller to replan.

Python 2.7 compatible: no f-strings, no type hints, no dataclasses.
"""
from __future__ import unicode_literals

from memory_contracts import EvidenceCard, AUTHORITY_VERIFIED, AUTHORITY_UNVERIFIED
from memory_policy import MemoryPolicy
from task_anchor import (DECISION_ALLOW, DECISION_QUARANTINE,
                         DECISION_PIVOT, DECISION_CLARIFICATION)


ACTION_ALLOW = DECISION_ALLOW
ACTION_QUARANTINE = DECISION_QUARANTINE
ACTION_PIVOT = DECISION_PIVOT
ACTION_CLARIFICATION = DECISION_CLARIFICATION

# Keys that may hold raw/bulky payloads; never copied into memory summaries.
_RAW_KEYS = ("results", "rows", "data", "raw", "payload", "records")


def _extract_rows(observation):
    if not isinstance(observation, dict):
        return []
    rows = observation.get("results")
    if rows is None:
        rows = observation.get("rows")
    return rows or []


def _columns(rows):
    if rows and isinstance(rows[0], dict):
        return sorted(rows[0].keys())[:8]
    return []


def _summarize(tool_name, observation, rows):
    status = observation.get("status", "ok") if isinstance(observation, dict) else "ok"
    summary = "react_tool=%s status=%s rows=%s" % (tool_name, status, len(rows))
    cols = _columns(rows)
    if cols:
        summary += " columns=%s" % ",".join(cols)
    return summary


class ReActObservationGovernor(object):
    """Gate ReAct observations through EvidenceCard + MemoryPolicy."""

    def __init__(self, memory_policy=None, observer=None, trimmer=None):
        self.memory_policy = memory_policy or MemoryPolicy()
        self.observer = observer
        self.trimmer = trimmer

    def _record(self, event, trace_id, task_id, session_id, metadata, status="ok"):
        if self.observer is None:
            return
        try:
            self.observer.record(event, trace_id=trace_id, status=status,
                                 task_id=task_id, session_id=session_id,
                                 metadata=metadata)
        except Exception:
            # Observability must never break the runtime loop.
            pass

    def build_card(self, task_anchor, tool_name, observation):
        """Compact an observation into an EvidenceCard (no raw rows copied)."""
        obs = observation if isinstance(observation, dict) else {}
        rows = _extract_rows(obs)
        status = obs.get("status", "ok")
        dataid = obs.get("dataid") or obs.get("current_dataid")
        verified = status == "ok" and bool(dataid)
        anchor_metric = getattr(task_anchor, "metric", None) if task_anchor else None
        anchor_dims = list(getattr(task_anchor, "dimensions", []) or []) if task_anchor else []
        card = EvidenceCard(
            task_id=getattr(task_anchor, "task_id", None) if task_anchor else None,
            source="react_tool:%s" % tool_name,
            summary=_summarize(tool_name, obs, rows),
            metric=obs.get("metric") or anchor_metric,
            dimensions=obs.get("dimensions") or anchor_dims,
            time_range=obs.get("time_range"),
            dataid=dataid,
            authority=AUTHORITY_VERIFIED if verified else AUTHORITY_UNVERIFIED,
            confidence=1.0 if verified else 0.0,
            metadata={"row_count": len(rows), "columns": _columns(rows),
                      "tool_name": tool_name, "obs_status": status},
        )
        return card

    def _injectable_ref(self, card):
        """Compact reference string safe to inject into an LLM context."""
        meta = card.metadata or {}
        return {
            "evidence_id": card.evidence_id,
            "dataid": card.dataid,
            "status": meta.get("obs_status"),
            "row_count": meta.get("row_count"),
            "columns": list(meta.get("columns") or []),
            "metric": card.metric,
            "summary": card.summary,
        }

    def govern(self, task_anchor, step_index, tool_name, observation,
               trace_id=None, task_id=None, session_id=None):
        """Govern a single ReAct observation.

        Returns a dict: {action, evidence, injectable, decision}.
        ``injectable`` is None whenever the observation is not allowed, so a
        quarantined/pivoted observation can never leak into the context.
        """
        card = self.build_card(task_anchor, tool_name, observation)
        if task_anchor is None:
            # No anchor to guard against: treat as allow but still compact.
            ref = self._injectable_ref(card)
            self._record("memory_retrieved", trace_id, task_id, session_id,
                         {"evidence_id": card.evidence_id, "step": step_index,
                          "action": ACTION_ALLOW, "anchorless": True})
            return {"action": ACTION_ALLOW, "evidence": card.to_dict(),
                    "injectable": ref, "decision": None}

        card, decision = self.memory_policy.apply(task_anchor, card)
        action = decision.action
        meta = {"evidence_id": card.evidence_id, "step": step_index,
                "action": action, "reason": decision.reason,
                "conflicts": list(decision.conflicts),
                "relevance": decision.relevance}

        if action == ACTION_ALLOW:
            self._record("memory_retrieved", trace_id, task_id, session_id, meta)
            return {"action": action, "evidence": card.to_dict(),
                    "injectable": self._injectable_ref(card),
                    "decision": decision.to_dict()}

        if action in (ACTION_PIVOT, ACTION_CLARIFICATION):
            self._record("memory_pivot", trace_id, task_id, session_id, meta,
                         status=action)
            return {"action": action, "evidence": card.to_dict(),
                    "injectable": None, "decision": decision.to_dict()}

        # Default: quarantine (never injectable).
        self._record("memory_quarantined", trace_id, task_id, session_id, meta,
                     status="quarantined")
        return {"action": ACTION_QUARANTINE, "evidence": card.to_dict(),
                "injectable": None, "decision": decision.to_dict()}

    def compact(self, cards):
        """Delegate to MemoryPolicy.compact_context for a batch of cards."""
        return self.memory_policy.compact_context(cards or [])


__all__ = ["ReActObservationGovernor", "ACTION_ALLOW", "ACTION_QUARANTINE",
           "ACTION_PIVOT", "ACTION_CLARIFICATION"]
