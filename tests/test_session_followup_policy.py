# -*- coding: utf-8 -*-
"""Tests for follow-up policy extraction and session compatibility."""

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, "src"))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def test_followup_policy_detect_and_merge():
    from followup_policy import detect_follow_up, merge_context
    from session import Session, Turn

    session = Session("s1")
    session.context["model"] = "order_detail"
    session.context["metric"] = "gmv"
    session.turns.append(Turn(query="看GMV", result={"status": "ok", "model": "order_detail", "metric": "gmv"}))

    follow = detect_follow_up("换成订单数", session)
    assert follow is not None
    assert follow.get("metric") == "order_count"

    merged = merge_context("换成订单数", session)
    assert merged.startswith("[context:") or merged == "换成订单数"


def test_session_run_preserves_followup_flow():
    from session import SessionManager, Turn

    sm = SessionManager()
    sid = sm.create("test-followup")
    session = sm.get(sid)
    session.context["model"] = "order_detail"
    session.context["metric"] = "gmv"
    session.turns.append(Turn(query="看GMV", result={"status": "ok", "model": "order_detail", "metric": "gmv"}))

    enriched = sm.get(sid)
    assert enriched is not None
    assert enriched.context["model"] == "order_detail"
