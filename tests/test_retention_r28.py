# -*- coding: utf-8 -*-
"""Phase R28 retention/cohort contract tests."""
from __future__ import unicode_literals

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, "src"))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _events():
    return [
        {"user_id": "u1", "tenant_id": "tenant_a", "event_name": "register", "event_date": "2026-07-01"},
        {"user_id": "u2", "tenant_id": "tenant_a", "event_name": "register", "event_date": "2026-07-01"},
        {"user_id": "u3", "tenant_id": "tenant_b", "event_name": "register", "event_date": "2026-07-02"},
        {"user_id": "u1", "tenant_id": "tenant_a", "event_name": "active", "event_date": "2026-07-02"},
        {"user_id": "u1", "tenant_id": "tenant_a", "event_name": "active", "event_date": "2026-07-02"},
        {"user_id": "u2", "tenant_id": "tenant_a", "event_name": "active", "event_date": "2026-07-08"},
        {"user_id": "u3", "tenant_id": "tenant_b", "event_name": "active", "event_date": "2026-07-03"},
    ]


def test_default_cohort_registry_has_supported_definitions():
    from cohort_registry import get_cohort_registry

    registry = get_cohort_registry()
    assert registry.names() == ["first_purchase_weekly", "registration_daily"]
    resolution = registry.resolve("registration_daily", "day", ["D1", "D7"])
    assert resolution["ok"] is True
    assert resolution["definition"]["tenant_column"] == "tenant_id"


def test_unknown_cohort_requires_clarification():
    from cohort_registry import get_cohort_registry

    result = get_cohort_registry().resolve("missing")
    assert result["ok"] is False
    assert result["errors"] == ["cohort_definition_missing"]


def test_unsupported_horizon_is_explicit():
    from cohort_registry import get_cohort_registry

    result = get_cohort_registry().resolve("registration_daily", horizons=["D99"])
    assert result["ok"] is False
    assert "unsupported_retention_horizon:D99" in result["errors"]


def test_cohort_analysis_deduplicates_and_returns_aggregate_matrix_only():
    from cohort_analysis import analyze_cohort_events
    from cohort_registry import get_cohort_registry

    definition = get_cohort_registry().resolve("registration_daily")["definition"]
    result = analyze_cohort_events(_events(), definition, ["D1", "D7"], tenant_id="tenant_a")
    assert result["status"] == "ok"
    assert result["summary_facts"]["cohort_count"] == 1
    assert result["summary_facts"]["sample_size"] == 2
    d1 = [row for row in result["matrix"] if row["period"] == "D1"][0]
    assert d1 == {"cohort": "2026-07-01", "period": "D1", "cohort_size": 2,
                  "active_users": 1, "retention_rate": 0.5}
    for row in result["matrix"]:
        assert "user_id" not in row


def test_cohort_analysis_tenant_isolation():
    from cohort_analysis import analyze_cohort_events
    from cohort_registry import get_cohort_registry

    definition = get_cohort_registry().resolve("registration_daily")["definition"]
    result = analyze_cohort_events(_events(), definition, ["D1"], tenant_id="tenant_b")
    assert result["matrix"] == [{"cohort": "2026-07-02", "period": "D1", "cohort_size": 1,
                                  "active_users": 1, "retention_rate": 1.0}]


def test_retention_execution_reports_missing_definition_as_clarification():
    from retention_execution import run_retention

    result = run_retention(cohort_id="does_not_exist", events=_events())
    assert result["status"] == "need_clarification"
    assert result["diagnostics"]["phase"] == "cohort_resolution"


def test_retention_execution_returns_readonly_aggregate_result_and_provenance_data():
    from retention_execution import run_retention

    result = run_retention("registration_daily", horizons=["D1", "D7"],
                           tenant_id="tenant_a", events=_events())
    assert result["status"] == "ok"
    assert result["task_type"] == "retention"
    assert result["results_summary"]["row_count"] == 2
    assert result["diagnostics"]["is_sample"] is True
    assert "COUNT(DISTINCT entity)" in result["sql"]
    assert "user_id" not in result["results"][0]


def test_retention_chart_policy_is_heatmap_with_reason():
    from chart_policy import select_chart

    chart = select_chart({"task_type": "retention", "metric": "retention_rate"},
                         {"results": [{"cohort": "2026-07-01", "period": "D1", "retention_rate": .5}]})
    assert chart["type"] == "heatmap"
    assert chart["reason"] == "cohort retention"
    assert chart["policy_id"] == "retention_cohort"


def test_retention_analysis_output_and_report_have_stable_product_shape():
    from analysis_output import standardize_analysis_output
    from report_generator import build_product_report
    from retention_execution import run_retention

    execution = run_retention("registration_daily", horizons=["D1"], tenant_id="tenant_a", events=_events())
    analysis = {"type": "retention", "status": "ok", "definition": execution["diagnostics"]["cohort_definition"],
                "items": execution["results"], "summary_facts": {"row_count": 1, "cohort_count": 1, "horizons": ["D1"]}}
    plan = {"task_type": "retention", "metric": "retention_rate", "dimensions": ["cohort", "period"],
            "cohort_definition": execution["diagnostics"]["cohort_definition"]}
    output = standardize_analysis_output(plan, execution, analysis=analysis)
    report = build_product_report(output).to_dict()
    assert output["contract"] == "analysis_output_v1"
    assert output["chart"]["type"] == "heatmap"
    assert output["evidence"]["cohort_definition"]["cohort_id"] == "registration_daily"
    assert report["task_type"] == "retention"
    assert report["methodology"]
    assert report["chart"]["type"] == "heatmap"


def test_retention_provenance_excludes_raw_rows_and_identifiers():
    from provenance import build_provenance

    result = {"task_type": "retention", "results": [{"cohort": "2026-07-01", "period": "D1"}],
              "diagnostics": {"cohort_definition": {"cohort_id": "registration_daily", "source": "user_events",
              "period_grain": "day", "retention_horizons": ["D1"], "timezone": "UTC"}, "is_sample": True, "sample_size": 2}}
    provenance = build_provenance({"task_type": "retention", "model": "user_events"}, result)
    assert provenance["cohort"]["aggregate_only"] is True
    assert provenance["cohort"]["sample_size"] == 2
    assert "user_id" not in repr(provenance)


def test_sandbox_has_deterministic_cohort_events():
    from sandbox_data_factory import build_sandbox_connection

    conn = build_sandbox_connection()
    rows = conn.execute("SELECT event_name, COUNT(*) AS n FROM user_events GROUP BY event_name ORDER BY event_name").fetchall()
    # The fixture is deterministic; it includes repeated activity events so
    # downstream cohort calculations can verify distinct-entity semantics.
    assert rows == [("active", 5), ("purchase", 3), ("register", 4)]


if __name__ == "__main__":
    for name, value in sorted(globals().items()):
        if name.startswith("test_") and callable(value):
            value()
    print("All R28 retention tests passed!")
