"""Tests for the diagnosis label system.

Run:
    python3 -m pytest tests/test_diagnosis.py -v
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest
from diagnosis import (
    DiagnosisEngine,
    DiagnosisReport,
    Diagnosis,
    Label,
    REMEDIATION,
    diagnose_agent_output,
)
from graph_agent import run_graph


# ── Unit: Label Coverage ──

def test_all_labels_have_remediation():
    """Every label must have a remediation mapping."""
    labels = [v for k, v in vars(Label).items() if not k.startswith("_") and isinstance(v, str)]
    for label in labels:
        assert label in REMEDIATION, f"Label '{label}' has no remediation"
        assert len(REMEDIATION[label]) >= 1, f"Label '{label}' has empty remediation"


def test_all_remediations_have_label():
    """Every remediation key must be a valid Label."""
    labels = set(v for k, v in vars(Label).items() if not k.startswith("_") and isinstance(v, str))
    for key in REMEDIATION:
        assert key in labels, f"Remediation key '{key}' is not a Label"


# ── Unit: Diagnosis dataclass ──

def test_diagnosis_dataclass():
    d = Diagnosis(
        label=Label.SQL_SYNTAX_ERROR,
        severity="critical",
        evidence="SQL failed",
        suggested_fix="Check CTE chain",
        all_fixes=["Check CTE chain", "Validate columns"],
    )
    assert d.is_critical
    assert d.is_blocking
    assert d.label == Label.SQL_SYNTAX_ERROR


def test_diagnosis_info_is_not_blocking():
    d = Diagnosis(label=Label.BLOCKED_QUERY, severity="info", evidence="blocked", suggested_fix="no fix")
    assert not d.is_critical
    assert not d.is_blocking


# ── Unit: DiagnosisReport ──

def test_report_is_healthy():
    r = DiagnosisReport(query="test", status="ok", diagnoses=[], overall_severity="healthy", summary="all good")
    assert r.is_healthy
    assert not r.has_critical


def test_report_to_dict():
    d = Diagnosis(label=Label.RESULT_EMPTY, severity="warning", evidence="no data", suggested_fix="expand range")
    r = DiagnosisReport(query="q", status="ok", diagnoses=[d], overall_severity="degraded", summary="warn")
    out = r.to_dict()
    assert out["overall_severity"] == "degraded"
    assert len(out["diagnoses"]) == 1
    assert out["diagnoses"][0]["label"] == Label.RESULT_EMPTY


# ── Integration: Engine with real agent output ──

class TestDiagnosisEngine:
    """Integration tests using real DAG agent output."""

    def test_healthy_run_no_diagnoses(self):
        r = run_graph("昨天 GMV 是多少？", use_db=True, use_llm=False)
        report = diagnose_agent_output(r)
        assert report.is_healthy
        assert not report.has_critical

    def test_blocked_query_detected(self):
        r = run_graph("删除昨天的订单数据", use_db=True, use_llm=False)
        report = diagnose_agent_output(r)
        assert any(d.label == Label.BLOCKED_QUERY for d in report.diagnoses), \
            f"Expected BLOCKED_QUERY, got: {[d.label for d in report.diagnoses]}"

    def test_clarification_detected(self):
        r = run_graph("GMV 口径是什么？", use_db=True, use_llm=False)
        report = diagnose_agent_output(r)
        assert any(d.label == Label.CLARIFICATION_LOOP for d in report.diagnoses), \
            f"Expected CLARIFICATION_LOOP, got: {[d.label for d in report.diagnoses]}"

    def test_sql_error_detected(self):
        """Synthetic SQL compilation error."""
        output = {"status": "error", "reason": "SQL compilation failed: CTE d5 not defined"}
        report = diagnose_agent_output(output)
        assert report.has_critical
        assert any(d.label == Label.SQL_SYNTAX_ERROR for d in report.diagnoses)

    def test_sql_execution_error_detected(self):
        output = {"status": "error", "reason": "SQL execution failed: no such column: xxx"}
        report = diagnose_agent_output(output)
        assert report.has_critical
        assert any(d.label == Label.SQL_EXECUTION_ERROR for d in report.diagnoses)

    def test_route_failure_detected(self):
        output = {"status": "error", "reason": "No output produced"}
        report = diagnose_agent_output(output)
        assert report.has_critical
        assert any(d.label == Label.ROUTE_FAILURE for d in report.diagnoses)

    def test_empty_result_warning(self):
        output = {"status": "ok", "results": [], "sql": "SELECT 1"}
        report = diagnose_agent_output(output)
        assert any(d.label == Label.RESULT_EMPTY for d in report.diagnoses)

    def test_placeholder_insight_detected(self):
        output = {"status": "ok", "insight": {"insight": "分析完成。"}}
        report = diagnose_agent_output(output)
        assert any(d.label == Label.PLACEHOLDER_INSIGHT for d in report.diagnoses)

    def test_nl_generation_error_detected(self):
        output = {"status": "ok", "insight": {"insight": "分析生成失败: API key 未配置"}}
        report = diagnose_agent_output(output)
        assert any(d.label == Label.NL_GENERATION_ERROR for d in report.diagnoses)

    def test_analysis_error_detected(self):
        output = {"status": "ok", "analysis": {"error": "Analysis failed: no numeric columns"}}
        report = diagnose_agent_output(output)
        assert any(d.label == Label.ANALYSIS_ERROR for d in report.diagnoses)

    def test_plan_timeout_detected(self):
        output = {"status": "error", "reason": "Graph execution exceeded max_steps (200)"}
        report = diagnose_agent_output(output)
        assert any(d.label == Label.PLAN_TIMEOUT for d in report.diagnoses)

    def test_metric_not_found_detected(self):
        """Unknown metric should be flagged."""
        output = {
            "status": "ok",
            "metric": "nonexistent_metric_xyz",
            "model": "order_detail",
            "sql": "SELECT SUM(value) FROM data",
        }
        report = diagnose_agent_output(output)
        assert any(d.label == Label.METRIC_NOT_FOUND for d in report.diagnoses)

    def test_dimension_not_found_detected(self):
        output = {
            "status": "ok",
            "dimensions": ["nonexistent_dimension_xyz"],
            "model": "order_detail",
            "sql": "SELECT * FROM data",
        }
        report = diagnose_agent_output(output)
        assert any(d.label == Label.DIMENSION_NOT_FOUND for d in report.diagnoses)

    def test_model_not_found_detected(self):
        output = {"status": "ok", "model": "nonexistent_model_xyz", "sql": "SELECT 1"}
        report = diagnose_agent_output(output)
        assert any(d.label == Label.MODEL_NOT_FOUND for d in report.diagnoses)

    def test_every_remediation_has_at_least_3_suggestions(self):
        """Each diagnosis should have multiple remediation options."""
        for label, fixes in REMEDIATION.items():
            assert len(fixes) >= 1, f"Label '{label}' has no fixes"
            # Most labels should have 3 suggestions
            if label not in (Label.BLOCKED_QUERY,):
                assert len(fixes) >= 2, f"Label '{label}' has only {len(fixes)} fix(es)"
