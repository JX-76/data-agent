# -*- coding: utf-8 -*-
"""Tests for Phase 5 multi-turn session state and follow-up rewrites."""
from __future__ import unicode_literals

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, "src"))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _seed_session():
    from session import Session, Turn, SESSION_STATE_IDLE

    session = Session("multi-turn")
    assert session.state == SESSION_STATE_IDLE
    session.context.update({
        "model": "order_detail",
        "metric": "gmv",
        "dimensions": ["date"],
        "time_range": "last_7_days",
        "filters": {},
        "task_type": "descriptive",
    })
    session.turns.append(Turn(query="看最近7天GMV", result={
        "status": "ok",
        "model": "order_detail",
        "metric": "gmv",
        "dimensions": ["date"],
        "time_range": "last_7_days",
        "filters": {},
        "task_type": "descriptive",
    }))
    return session


def test_followup_region_filter_patch():
    from followup_policy import resolve_followup

    patch = resolve_followup("换成华东看一遍", _seed_session())
    assert patch["is_follow_up"] is True
    assert patch["metric"] == "gmv"
    assert patch["filters"]["region"] == "华东"
    assert patch["state"] == "follow_up"


def test_followup_channel_filter_patch():
    from followup_policy import resolve_followup

    patch = resolve_followup("只看淘宝", _seed_session())
    assert patch["filters"]["channel"] == "淘宝"
    assert patch["state"] == "follow_up"


def test_followup_category_drilldown_patch():
    from followup_policy import resolve_followup

    patch = resolve_followup("按品类拆一下", _seed_session())
    assert patch["dimensions"] == ["category"]
    assert patch["state"] == "drill_down"


def test_followup_compare_previous_month_patch():
    from followup_policy import resolve_followup

    patch = resolve_followup("和上月比", _seed_session())
    assert patch["task_type"] == "comparison"
    assert patch["compare_to"] == "previous_month"
    assert patch["state"] == "follow_up"


def test_session_state_transition_validation():
    from session import Session, SESSION_STATE_DRILL_DOWN

    session = Session("state-check")
    session.transition(SESSION_STATE_DRILL_DOWN)
    assert session.state == SESSION_STATE_DRILL_DOWN
    try:
        session.transition("bad_state")
        assert False, "expected invalid session state to fail"
    except ValueError as exc:
        assert "invalid session state" in str(exc)


if __name__ == "__main__":
    test_followup_region_filter_patch()
    test_followup_channel_filter_patch()
    test_followup_category_drilldown_patch()
    test_followup_compare_previous_month_patch()
    test_session_state_transition_validation()
    print("All multi-turn session tests passed!")
