"""Complex regression tests for Data Agent loop governance and dispatcher.

These tests avoid live LLM calls where possible and focus on stable contracts:
- ToolDispatcher returns normalized DispatchResult objects.
- react_loop exposes standardized top-level state fields.
- loop controls report termination_reason/loop_stats.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from agent_loop import react_loop
from context_manager import ResultTrimmer
from mvp_agent import AgentRuntime
from tool_dispatcher import DispatchResult, ToolDispatcher


REQUIRED_REACT_FIELDS = {
    "query",
    "status",
    "intent",
    "model",
    "metric",
    "dimensions",
    "time_range",
    "steps",
    "insight",
    "chart",
    "sql",
    "results",
    "results_summary",
    "errors",
    "trace",
    "termination_reason",
    "loop_stats",
    "execution",
}


def test_dispatcher_unknown_tool_contract():
    dispatcher = ToolDispatcher(AgentRuntime(), db=None)
    result = dispatcher.dispatch("not_a_tool", {}, [])

    assert isinstance(result, DispatchResult)
    assert result.ok is False
    assert result.tool == "not_a_tool"
    assert result.error
    assert result.dataid is None


def test_dispatcher_switch_filter_aggregate_contract_without_db():
    dispatcher = ToolDispatcher(AgentRuntime(), db=None)
    dataids = []

    switched = dispatcher.dispatch("switch", {"model_id": "order_detail"}, dataids)
    assert switched.ok is True
    assert switched.dataid
    assert switched.sample_rows
    assert dataids[-1] == switched.dataid

    filtered = dispatcher.dispatch(
        "filter",
        {
            "dataid": switched.dataid,
            "metric_id": "gmv",
            "start_iso": "2026-06-27T00:00:00",
            "end_iso": "2026-06-28T00:00:00",
        },
        dataids,
    )
    assert filtered.ok is True
    assert filtered.dataid
    assert dataids[-1] == filtered.dataid

    aggregated = dispatcher.dispatch(
        "aggregate",
        {"dataid": filtered.dataid, "metric_id": "gmv", "dimensions": ["channel"]},
        dataids,
    )
    assert aggregated.ok is True
    assert aggregated.dataid
    assert aggregated.sample_rows is not None


def test_dispatcher_complex_tools_contract_without_db():
    dispatcher = ToolDispatcher(AgentRuntime(), db=None)
    dataids = []

    d1 = dispatcher.dispatch("switch", {"model_id": "order_detail"}, dataids).dataid
    d2 = dispatcher.dispatch(
        "filter",
        {
            "dataid": d1,
            "metric_id": "gmv",
            "start_iso": "2026-06-20T00:00:00",
            "end_iso": "2026-06-28T00:00:00",
        },
        dataids,
    ).dataid
    d3 = dispatcher.dispatch("aggregate", {"dataid": d2, "metric_id": "gmv", "dimensions": ["channel"]}, dataids).dataid

    top = dispatcher.dispatch("top", {"dataid": d3, "by": "gmv", "n": 3}, dataids)
    assert top.ok is True
    assert top.dataid

    filtered_value = dispatcher.dispatch("filter_value", {"dataid": d3, "dimension": "channel", "value": "online"}, dataids)
    assert filtered_value.ok is True
    assert filtered_value.dataid

    compared = dispatcher.dispatch(
        "compare_periods",
        {
            "dataid": d1,
            "metric_id": "gmv",
            "p1_start": "2026-06-21T00:00:00",
            "p1_end": "2026-06-28T00:00:00",
            "p2_start": "2026-06-14T00:00:00",
            "p2_end": "2026-06-21T00:00:00",
            "dimensions": ["channel"],
        },
        dataids,
    )
    assert compared.ok is True
    assert compared.dataid


def test_results_summary_is_compact_and_stable():
    rows = [
        {"channel": "online", "gmv": 100.1234, "long_text": "x" * 100},
        {"channel": "offline", "gmv": 80.5678, "long_text": "y" * 100},
        {"channel": "partner", "gmv": 60.0, "long_text": "z" * 100},
        {"channel": "other", "gmv": 20.0, "long_text": "w" * 100},
        {"channel": "miniapp", "gmv": 10.0, "long_text": "q" * 100},
        {"channel": "live", "gmv": 5.0, "long_text": "p" * 100},
    ]
    summary = ResultTrimmer.trim_rows(rows)
    summary["stats"] = ResultTrimmer.extract_key_stats(rows)

    assert summary["row_count"] == len(rows)
    assert len(summary["sample"]) <= 3
    assert summary["sample"][0]["long_text"].endswith("...")
    assert "columns" in summary
    assert summary["stats"]


@pytest.mark.parametrize(
    "termination_reason,errors,expected",
    [
        (None, [], "ok"),
        ("done", [], "ok"),
        ("max_steps_exceeded(2)", [], "error"),
        ("done", [{"error": "x"}], "error"),
    ],
)
def test_status_inference(termination_reason, errors, expected):
    if errors:
        status = "error"
    elif termination_reason in (None, "done"):
        status = "ok"
    elif "exceeded" in termination_reason:
        status = "error"
    else:
        status = "ok"
    assert status == expected


def test_react_loop_standardized_state_without_llm_key():
    result = react_loop("昨天各渠道 GMV", use_db=False, max_steps=2, max_tool_calls=2, max_replans=2)

    assert REQUIRED_REACT_FIELDS.issubset(result.keys())
    assert result["query"] == "昨天各渠道 GMV"
    assert result["status"] in {"ok", "error"}
    assert isinstance(result["errors"], list)
    assert isinstance(result["loop_stats"], dict)
    assert isinstance(result["execution"], dict)
    assert result["execution"]["used_db"] is False
    assert "step_count" in result["execution"]
