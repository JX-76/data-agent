"""Diagnosis label system — automatically classify agent failures.

Part of the Harness Engine's "40% self-built" core:
  1. Diagnosis Label System (this module)
  2. Remediation Strategy Mapping
  3. Data Flywheel

Labels are deterministic rules applied to agent output, with an optional
LLM fallback for edge cases. Each diagnosis carries a severity level and
a suggested remediation strategy.

Usage:
    from diagnosis import DiagnosisEngine

    engine = DiagnosisEngine()
    result = engine.diagnose(agent_output)
    # → [Diagnosis(label="SQL_SYNTAX_ERROR", severity="critical", ...)]
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional


# ── Diagnosis Labels ──

class Label:
    """Diagnostic label constants."""

    # Critical — agent cannot proceed
    SQL_SYNTAX_ERROR = "SQL_SYNTAX_ERROR"
    SQL_EXECUTION_ERROR = "SQL_EXECUTION_ERROR"
    ROUTE_FAILURE = "ROUTE_FAILURE"
    PLAN_TIMEOUT = "PLAN_TIMEOUT"

    # Warning — agent can proceed with degraded output
    METRIC_NOT_FOUND = "METRIC_NOT_FOUND"
    DIMENSION_NOT_FOUND = "DIMENSION_NOT_FOUND"
    MODEL_NOT_FOUND = "MODEL_NOT_FOUND"
    RESULT_EMPTY = "RESULT_EMPTY"
    MERGE_COLLISION = "MERGE_COLLISION"

    # Info — non-blocking
    CLARIFICATION_LOOP = "CLARIFICATION_LOOP"
    BLOCKED_QUERY = "BLOCKED_QUERY"
    NL_GENERATION_ERROR = "NL_GENERATION_ERROR"
    ANALYSIS_ERROR = "ANALYSIS_ERROR"
    PLACEHOLDER_INSIGHT = "PLACEHOLDER_INSIGHT"


# ── Remediation Strategy (diagnosis → fix) ──

REMEDIATION = {
    Label.SQL_SYNTAX_ERROR: [
        "Check CTE chain for missing edges or undefined dataids.",
        "Validate that all referenced columns exist in the final CTE.",
        "Verify the SQL was compiled from a complete tool chain (switch → filter → aggregate → sort).",
    ],
    Label.SQL_EXECUTION_ERROR: [
        "Check that the mock database has data for the requested time range.",
        "Verify column types match (e.g., numeric metric, string dimension).",
        "Consider expanding the time filter or using a different data model.",
    ],
    Label.METRIC_NOT_FOUND: [
        "List available metrics from the semantic layer for the current model.",
        "Try a synonymous metric (e.g., 'revenue' for 'gmv', 'orders' for 'order_count').",
        "If the metric genuinely doesn't exist, ask the user to clarify.",
    ],
    Label.DIMENSION_NOT_FOUND: [
        "List available dimensions for the current model.",
        "Try a synonymous dimension (e.g., 'channel' for '渠道', 'region' for '大区').",
        "Remove the unknown dimension and return metric-only results.",
    ],
    Label.MODEL_NOT_FOUND: [
        "List available models from the semantic layer.",
        "Try mapping the query to a different model (e.g., 'order_detail' instead of 'sales_detail').",
        "If no model fits, ask the user what data source they want.",
    ],
    Label.RESULT_EMPTY: [
        "Expand the time range (from 'yesterday' to 'last_7_days').",
        "Remove dimension filters that may be too restrictive.",
        "Check if the metric has data at all (try 'total' without dimensions).",
    ],
    Label.MERGE_COLLISION: [
        "Use auto-suffix '_dataid' to disambiguate colliding columns.",
        "Change the merge key to a shared dimension.",
        "Check that both datasets share at least one common dimension.",
    ],
    Label.ROUTE_FAILURE: [
        "The query could not be mapped to a known intent/metric/model.",
        "Try adding more keywords (e.g., 'GMV', '订单', '渠道') to disambiguate.",
        "Use the clarification flow to ask the user what they want.",
    ],
    Label.PLAN_TIMEOUT: [
        "The agent exceeded the maximum number of steps.",
        "Reduce query complexity (fewer dimensions, simpler metric).",
        "Check for infinite loops in conditional edge definitions.",
    ],
    Label.CLARIFICATION_LOOP: [
        "Clarification was triggered but the user's choice was not applied correctly.",
        "Verify that resume_graph passes the user_choice to the plan correctly.",
        "Clear the clarification flag before re-entering the route node.",
    ],
    Label.BLOCKED_QUERY: [
        "The query was blocked for safety reasons (DELETE, DROP, UPDATE, etc.).",
        "No remediation available — the query is intentionally blocked.",
        "If the user wants real data operations, use a separate admin interface.",
    ],
    Label.NL_GENERATION_ERROR: [
        "NL insight generation failed (LLM API error or timeout).",
        "Fall back to heuristic insight from analysis.py structured data.",
        "Check DeepSeek API key, network connectivity, and rate limits.",
    ],
    Label.ANALYSIS_ERROR: [
        "The analysis.py layer failed (pandas error, empty data, etc.).",
        "Check that results contain valid numeric and categorical columns.",
        "Skip to heuristic insight generation without analysis enrichment.",
    ],
    Label.PLACEHOLDER_INSIGHT: [
        "The NL insight is the default placeholder ('分析完成。').",
        "This usually means no results were available, or analysis.py returned empty.",
        "Check that use_db=True and the mock database has data for the query.",
    ],
}


# ── Data Structures ──

@dataclass
class Diagnosis:
    """A single diagnostic finding."""

    label: str
    severity: str  # "critical" | "warning" | "info"
    evidence: str  # Human-readable explanation of what was detected
    suggested_fix: str  # Top-1 remediation action
    all_fixes: list[str] = field(default_factory=list)
    raw_evidence: Any = None  # Raw data that triggered the diagnosis

    @property
    def is_critical(self) -> bool:
        return self.severity == "critical"

    @property
    def is_blocking(self) -> bool:
        """Whether this diagnosis blocks the agent from proceeding."""
        return self.severity == "critical"


@dataclass
class DiagnosisReport:
    """Full diagnosis report for an agent run."""

    query: str
    status: str  # "ok" | "error" | "clarification_needed" | "blocked"
    diagnoses: list[Diagnosis]
    overall_severity: str  # "healthy" | "degraded" | "failed"
    summary: str  # Single-sentence summary

    @property
    def is_healthy(self) -> bool:
        return self.overall_severity == "healthy"

    @property
    def has_critical(self) -> bool:
        return any(d.is_critical for d in self.diagnoses)

    def to_dict(self) -> dict:
        return {
            "query": self.query,
            "status": self.status,
            "overall_severity": self.overall_severity,
            "summary": self.summary,
            "diagnoses": [
                {
                    "label": d.label,
                    "severity": d.severity,
                    "evidence": d.evidence,
                    "suggested_fix": d.suggested_fix,
                }
                for d in self.diagnoses
            ],
        }


# ── Detection Rules ──

def _detect_sql_syntax_error(output: dict) -> Optional[Diagnosis]:
    """Detect SQL compilation failures."""
    if output.get("status") == "error" and "compilation failed" in str(output.get("reason", "")):
        fixes = REMEDIATION[Label.SQL_SYNTAX_ERROR]
        return Diagnosis(
            label=Label.SQL_SYNTAX_ERROR,
            severity="critical",
            evidence=f"SQL compilation failed: {output.get('reason')}",
            suggested_fix=fixes[0],
            all_fixes=fixes,
            raw_evidence=output.get("reason"),
        )
    # Also check for invalid SQL
    valid = output.get("valid")
    if isinstance(valid, tuple) and not valid[0]:
        fixes = REMEDIATION[Label.SQL_SYNTAX_ERROR]
        return Diagnosis(
            label=Label.SQL_SYNTAX_ERROR,
            severity="critical",
            evidence=f"SQL validation failed: {valid[1]}",
            suggested_fix=fixes[0],
            all_fixes=fixes,
            raw_evidence=valid,
        )
    return None


def _detect_sql_execution_error(output: dict) -> Optional[Diagnosis]:
    """Detect SQL execution failures."""
    if output.get("status") == "error" and "execution failed" in str(output.get("reason", "")):
        fixes = REMEDIATION[Label.SQL_EXECUTION_ERROR]
        return Diagnosis(
            label=Label.SQL_EXECUTION_ERROR,
            severity="critical",
            evidence=f"SQL execution failed: {output.get('reason')}",
            suggested_fix=fixes[0],
            all_fixes=fixes,
            raw_evidence=output.get("reason"),
        )
    return None


def _detect_result_empty(output: dict) -> Optional[Diagnosis]:
    """Detect empty result sets."""
    results = output.get("results")
    if results is not None and len(results) == 0:
        fixes = REMEDIATION[Label.RESULT_EMPTY]
        return Diagnosis(
            label=Label.RESULT_EMPTY,
            severity="warning",
            evidence="Query returned 0 rows.",
            suggested_fix=fixes[0],
            all_fixes=fixes,
            raw_evidence={"row_count": 0},
        )
    return None


def _detect_route_failure(output: dict) -> Optional[Diagnosis]:
    """Detect routing failures."""
    if output.get("status") == "error" and "No output produced" in str(output.get("reason", "")):
        fixes = REMEDIATION[Label.ROUTE_FAILURE]
        return Diagnosis(
            label=Label.ROUTE_FAILURE,
            severity="critical",
            evidence="Graph produced no output — likely route failure or missing node.",
            suggested_fix=fixes[0],
            all_fixes=fixes,
            raw_evidence=output.get("reason"),
        )
    return None


def _detect_clarification_loop(output: dict, original_query: str) -> Optional[Diagnosis]:
    """Detect when clarification re-triggers after resume."""
    if output.get("status") == "clarification_needed":
        # Only flag as a loop if there's evidence of repeated clarification
        # (e.g., the state has __resume_payload__ but still needs clarification)
        # For now, flag any clarification as info-level
        fixes = REMEDIATION[Label.CLARIFICATION_LOOP]
        return Diagnosis(
            label=Label.CLARIFICATION_LOOP,
            severity="info",
            evidence=f"Clarification requested: {json.dumps(output.get('interrupt'), default=str)[:200]}",
            suggested_fix=fixes[0],
            all_fixes=fixes,
            raw_evidence=output.get("interrupt"),
        )
    return None


def _detect_blocked_query(output: dict) -> Optional[Diagnosis]:
    """Detect blocked queries."""
    if output.get("status") == "blocked":
        fixes = REMEDIATION[Label.BLOCKED_QUERY]
        return Diagnosis(
            label=Label.BLOCKED_QUERY,
            severity="info",
            evidence=f"Query blocked: {output.get('reason', 'unknown reason')}",
            suggested_fix=fixes[0],
            all_fixes=fixes,
            raw_evidence=output.get("reason"),
        )
    return None


def _detect_placeholder_insight(output: dict) -> Optional[Diagnosis]:
    """Detect when NL insight is the default placeholder."""
    insight = output.get("insight", {})
    if isinstance(insight, dict):
        text = insight.get("insight", "")
        if text == "分析完成。" or text == "分析完成":
            fixes = REMEDIATION[Label.PLACEHOLDER_INSIGHT]
            return Diagnosis(
                label=Label.PLACEHOLDER_INSIGHT,
                severity="info",
                evidence="NL insight is the default placeholder — no analysis or results were available.",
                suggested_fix=fixes[0],
                all_fixes=fixes,
            )
    return None


def _detect_nl_generation_error(output: dict) -> Optional[Diagnosis]:
    """Detect NL generation failures."""
    insight = output.get("insight", {})
    if isinstance(insight, dict):
        text = insight.get("insight", "")
        if text and ("失败" in text or "API key" in text or "error" in text.lower()):
            fixes = REMEDIATION[Label.NL_GENERATION_ERROR]
            return Diagnosis(
                label=Label.NL_GENERATION_ERROR,
                severity="info",
                evidence=f"NL generation error: {text[:200]}",
                suggested_fix=fixes[0],
                all_fixes=fixes,
            )
    return None


def _detect_analysis_error(output: dict) -> Optional[Diagnosis]:
    """Detect analysis layer failures."""
    analysis = output.get("analysis", {})
    if isinstance(analysis, dict) and "error" in analysis:
        fixes = REMEDIATION[Label.ANALYSIS_ERROR]
        return Diagnosis(
            label=Label.ANALYSIS_ERROR,
            severity="warning",
            evidence=f"Analysis layer failed: {analysis['error']}",
            suggested_fix=fixes[0],
            all_fixes=fixes,
        )
    return None


def _detect_plan_timeout(output: dict) -> Optional[Diagnosis]:
    """Detect step limit exceeded."""
    if output.get("status") == "error" and "max_steps" in str(output.get("reason", "")):
        fixes = REMEDIATION[Label.PLAN_TIMEOUT]
        return Diagnosis(
            label=Label.PLAN_TIMEOUT,
            severity="critical",
            evidence=f"Plan exceeded max steps: {output.get('reason')}",
            suggested_fix=fixes[0],
            all_fixes=fixes,
        )
    return None


def _detect_merge_collision(output: dict) -> Optional[Diagnosis]:
    """Detect merge collision from trace."""
    trace = output.get("trace") or []
    for entry in trace:
        if isinstance(entry, dict):
            op = entry.get("op", "")
            if "merge" in op and "collision" in str(entry.get("columns", "")):
                fixes = REMEDIATION[Label.MERGE_COLLISION]
                return Diagnosis(
                    label=Label.MERGE_COLLISION,
                    severity="warning",
                    evidence=f"Merge collision in trace: {entry}",
                    suggested_fix=fixes[0],
                    all_fixes=fixes,
                )
    return None


def _detect_metric_or_dim_not_found(sql: str, output: dict) -> list[Diagnosis]:
    """Detect metric/dimension/model references that don't exist.

    Checks SQL against known semantic layer entities.
    """
    diagnoses = []
    if not sql:
        return diagnoses

    # Known metrics, dimensions, models from the semantic layer
    # (imported lazily to avoid circular deps)
    try:
        from config import SEMANTIC_SUMMARY
        metrics = list(SEMANTIC_SUMMARY.get("metrics", {}).keys())
        dimensions = list(SEMANTIC_SUMMARY.get("dimensions", {}).keys())
        models = list(SEMANTIC_SUMMARY.get("models", {}).keys())

        # Check metric references in aggregate calls (from trace, not SQL)
        metric = output.get("metric", "")
        if metric and metric not in metrics:
            alt = [m for m in metrics if any(
                keyword in m.lower() for keyword in metric.lower().split()
            )]
            alt_suffix = f" Similar: {alt}" if alt else ""
            fixes = REMEDIATION[Label.METRIC_NOT_FOUND]
            diagnoses.append(Diagnosis(
                label=Label.METRIC_NOT_FOUND,
                severity="warning",
                evidence=f"Metric '{metric}' not in semantic layer.{alt_suffix}",
                suggested_fix=fixes[0],
                all_fixes=fixes + [f"Known metrics: {metrics}"],
            ))

        dimensions_used = output.get("dimensions", []) or []
        for dim in dimensions_used:
            if dim not in dimensions and dim:
                alt = [d for d in dimensions if dim in d or d in dim]
                alt_suffix = f" Similar: {alt}" if alt else ""
                fixes = REMEDIATION[Label.DIMENSION_NOT_FOUND]
                diagnoses.append(Diagnosis(
                    label=Label.DIMENSION_NOT_FOUND,
                    severity="warning",
                    evidence=f"Dimension '{dim}' not in semantic layer.{alt_suffix}",
                    suggested_fix=fixes[0],
                    all_fixes=fixes + [f"Known dimensions: {dimensions}"],
                ))

        model = output.get("model", "")
        if model and model not in models:
            fixes = REMEDIATION[Label.MODEL_NOT_FOUND]
            diagnoses.append(Diagnosis(
                label=Label.MODEL_NOT_FOUND,
                severity="warning",
                evidence=f"Model '{model}' not in semantic layer. Known: {models}",
                suggested_fix=fixes[0],
                all_fixes=fixes + [f"Known models: {models}"],
            ))
    except ImportError:
        pass

    return diagnoses


# ── Diagnosis Engine ──

import json


class DiagnosisEngine:
    """Diagnostic engine for Data Agent output.

    Applies a chain of detection rules to agent output and produces
    a structured DiagnosisReport with severity classification and
    suggested remediation actions.

    Usage:
        engine = DiagnosisEngine()
        report = engine.diagnose(agent_output)
        if report.has_critical:
            for d in report.diagnoses:
                print(f"[{d.severity}] {d.label}: {d.suggested_fix}")
    """

    def diagnose(self, output: dict, query: str = "") -> DiagnosisReport:
        """Run all detection rules on agent output.

        Args:
            output: Agent output dict (from run_graph, run_react, etc.)
            query: Original user query (optional, for context)

        Returns:
            DiagnosisReport with all findings.
        """
        diagnoses: list[Diagnosis] = []

        # Run all detection rules
        rules = [
            (_detect_sql_syntax_error, output),
            (_detect_sql_execution_error, output),
            (_detect_route_failure, output),
            (_detect_plan_timeout, output),
            (_detect_result_empty, output),
            (_detect_blocked_query, output),
            (_detect_placeholder_insight, output),
            (_detect_nl_generation_error, output),
            (_detect_analysis_error, output),
            (_detect_merge_collision, output),
        ]

        for rule_fn, arg in rules:
            result = rule_fn(arg)
            if result:
                diagnoses.append(result)

        # Clarification loop — needs query context
        clarity = _detect_clarification_loop(output, query)
        if clarity:
            diagnoses.append(clarity)

        # Semantic layer checks — needs SQL
        sql = output.get("sql", "")
        if sql:
            metric_diags = _detect_metric_or_dim_not_found(sql, output)
            diagnoses.extend(metric_diags)

        # Determine overall severity
        status = output.get("status", "error")
        if not diagnoses:
            overall = "healthy"
            summary = "Agent completed successfully with no issues detected."
        elif any(d.severity == "critical" for d in diagnoses):
            overall = "failed"
            criticals = [d.label for d in diagnoses if d.severity == "critical"]
            summary = f"Agent failed with {len(criticals)} critical issue(s): {', '.join(criticals)}."
        elif any(d.severity == "warning" for d in diagnoses):
            overall = "degraded"
            warnings = [d.label for d in diagnoses if d.severity == "warning"]
            summary = f"Agent completed with {len(warnings)} warning(s): {', '.join(warnings)}."
        else:
            overall = "healthy"
            summary = "Agent completed with minor informational findings."

        return DiagnosisReport(
            query=query or output.get("query", ""),
            status=status,
            diagnoses=diagnoses,
            overall_severity=overall,
            summary=summary,
        )


def diagnose_agent_output(output: dict, query: str = "") -> DiagnosisReport:
    """Convenience function: diagnose a single agent output."""
    engine = DiagnosisEngine()
    return engine.diagnose(output, query)
