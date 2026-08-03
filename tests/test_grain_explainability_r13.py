# -*- coding: utf-8 -*-
"""Regression tests for grain rewrite explainability propagation."""
from __future__ import unicode_literals

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, "src"))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _plan():
    return {"status": "ok", "model": "order_detail", "metric": "gmv", "dimensions": ["region"]}


def _selected_rewrite():
    return {"contract": "grain_aggregate_rewrite_v1", "selected": True,
            "strategy": "pre_aggregate", "reason": "safe_fact_to_dimension_join",
            "fact_grain": ["order_id"]}


def _selected_semijoin_rewrite():
    data = dict(_selected_rewrite())
    data["semijoin_pushdowns"] = [{
        "contract": "dimension_filter_semijoin_v1",
        "dimension_table": "dim_store",
        "filter_field": "dim_store.region",
    }]
    return data


def test_credibility_exposes_selected_rewrite_as_evidence():
    from credibility import build_credibility

    out = build_credibility(_plan(), {
        "status": "ok", "results": [{"region": "east", "gmv": 150}],
        "diagnostics": {"grain_rewrite": _selected_rewrite()},
    })

    assert "grain_safe_preaggregation" in out["evidence"]
    assert out["grain_rewrite"]["strategy"] == "pre_aggregate"


def test_credibility_exposes_semijoin_pushdown_as_evidence():
    from credibility import build_credibility

    out = build_credibility(_plan(), {
        "status": "ok", "results": [{"region": "east", "gmv": 150}],
        "diagnostics": {"grain_rewrite": _selected_semijoin_rewrite()},
    })

    assert "dimension_filter_semijoin_pushdown" in out["evidence"]


def test_credibility_preserves_non_selected_reason_as_limitation():
    from credibility import build_credibility

    out = build_credibility(_plan(), {
        "status": "ok", "diagnostics": {
            "strategy_metadata": {"compiled_sql": {"grain_rewrite": {
                "contract": "grain_aggregate_rewrite_v1", "selected": False,
                "strategy": "direct", "reason": "metric_not_declared_additive",
            }}},
        },
    })

    assert "grain_rewrite_not_applied:metric_not_declared_additive" in out["limitations"]


def test_insight_bundle_explains_selected_or_skipped_rewrite():
    from result_explainer import build_insight_bundle

    selected = build_insight_bundle(_plan(), {
        "status": "ok", "diagnostics": {"grain_rewrite": _selected_rewrite()},
    }).to_dict()
    skipped = build_insight_bundle(_plan(), {
        "status": "ok", "diagnostics": {"grain_rewrite": {
            "selected": False, "strategy": "direct", "reason": "multi_join_rewrite_not_supported",
        }},
    }).to_dict()

    semijoin = build_insight_bundle(_plan(), {
        "status": "ok", "diagnostics": {"grain_rewrite": _selected_semijoin_rewrite()},
    }).to_dict()

    assert any(u"先聚合" in item for item in selected["caveats"])
    assert any(u"半连接下推" in item for item in semijoin["caveats"])
    assert any(u"multi_join_rewrite_not_supported" in item for item in skipped["caveats"])


def test_execution_engine_keeps_rewrite_in_trace_and_diagnostics():
    import execution_engine as engine_module
    from execution_engine import ExecutionEngine

    original = engine_module.compile_metric_sql
    compiled = type("Compiled", (object,), {
        "ok": True,
        "sql": "SELECT 1",
        "metadata": {"fingerprint": "fp", "grain_rewrite": _selected_rewrite()},
        "to_dict": lambda self: {"grain_rewrite": _selected_rewrite()},
    })()
    try:
        engine_module.compile_metric_sql = lambda plan, catalog: compiled
        result = ExecutionEngine()._compile_plan(None, _plan(), "descriptive", metadata_catalog={})
    finally:
        engine_module.compile_metric_sql = original

    assert result.metadata["compiled_sql"]["grain_rewrite"]["selected"] is True
    assert result.trace[0]["grain_rewrite"]["strategy"] == "pre_aggregate"


if __name__ == "__main__":
    test_credibility_exposes_selected_rewrite_as_evidence()
    test_credibility_exposes_semijoin_pushdown_as_evidence()
    test_credibility_preserves_non_selected_reason_as_limitation()
    test_insight_bundle_explains_selected_or_skipped_rewrite()
    test_execution_engine_keeps_rewrite_in_trace_and_diagnostics()
    print("All grain explainability R13 tests passed!")
