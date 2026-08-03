# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from release_api import ask_release, release_dashboard
from release_dashboard import compute_dashboard, format_dashboard_text


def test_compute_dashboard_rates_and_quality():
    history = [
        {"status": "ok", "metric": "gmv", "quality_score": 1.0},
        {"status": "blocked", "metric": None, "quality_score": 0.8},
        {"status": "need_clarification", "metric": None, "quality_score": 0.7},
    ]
    metrics = {"total": 3, "ok": 1, "blocked": 1, "need_clarification": 1, "error": 0}
    d = compute_dashboard(history, metrics)
    assert d["contract"] == "release_v1_dashboard"
    assert d["rates"]["ok_rate"] == 0.3333
    assert d["quality"]["count"] == 3
    assert d["quality"]["below_threshold"] == 1
    assert d["metric_distribution"]["gmv"] == 1


def test_format_dashboard_text_contains_sections():
    d = compute_dashboard([], {"total": 0})
    text = format_dashboard_text(d)
    assert "Release v1 Operations Dashboard" in text
    assert "Terminal breakdown" in text
    assert "Quality scores" in text


def test_release_dashboard_from_api_state():
    ask_release("最近7天GMV", session_id="dashboard_test", use_llm=False)
    d = release_dashboard()
    assert d["contract"] == "release_v1_dashboard"
    assert d["history_count"] >= 1
    assert d["quality"]["count"] >= 1
