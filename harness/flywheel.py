"""Data Flywheel — track diagnosis patterns and improve strategy selection.

Part of the Harness Engine's "40% self-built" core (piece 3/3):
  1. Diagnosis Label System (src/diagnosis.py)
  2. Remediation Strategy Mapping (embedded in diagnosis.py)
  3. Data Flywheel (this module) — uses historical diagnosis data to:
     - Identify failure hotspots
     - Track remediation effectiveness
     - Feed patterns back into strategy prioritization

Usage:
    from harness.flywheel import Flywheel

    fw = Flywheel()
    fw.record(run_output)  # Record each agent run
    report = fw.report()   # Get pattern analysis
    fw.save()              # Persist to disk
"""

from __future__ import annotations

import json
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from diagnosis import Label, DiagnosisReport, Diagnosis, diagnose_agent_output


# ── Data Structures ──

@dataclass
class FlywheelRecord:
    """A single recorded agent run."""
    timestamp: float
    query: str
    status: str
    overall_severity: str  # healthy / degraded / failed
    labels: list[str]  # Diagnosis labels
    duration_ms: float = 0
    metadata: dict = field(default_factory=dict)


@dataclass
class FlywheelReport:
    """Aggregated pattern analysis over recorded runs."""
    total_runs: int
    time_range: tuple[float, float]  # (earliest, latest)

    # Severity distribution
    failure_rate: float  # 0.0–1.0
    healthy_rate: float
    degraded_rate: float

    # Top failure labels
    top_labels: list[tuple[str, int]]  # (label, count)

    # Query patterns that most often fail
    failure_query_patterns: list[tuple[str, int]]

    # Trend: improving or worsening?
    trend: str  # "improving" | "stable" | "worsening"
    trend_metric: float  # failure rate change over time

    # Recommendations
    recommendations: list[str]


# ── Flywheel Engine ──

FLYWHEEL_DIR = Path(__file__).resolve().parents[1] / "flywheel_data"
FLYWHEEL_DIR.mkdir(exist_ok=True)


class Flywheel:
    """Tracks agent runs, aggregates diagnosis patterns, feeds back improvements.

    Usage:
        fw = Flywheel()
        for query in test_queries:
            output = run_graph(query)
            fw.record(output, query)
        report = fw.report()
        print(report.recommendations)
    """

    def __init__(self):
        self.records: list[FlywheelRecord] = []
        self._query_patterns: dict[str, list[str]] = defaultdict(list)

    def record(
        self,
        agent_output: dict,
        query: str = "",
        duration_ms: float = 0,
        metadata: Optional[dict] = None,
    ) -> FlywheelRecord:
        """Record an agent run into the flywheel.

        Args:
            agent_output: Output dict from run_graph() or react_loop()
            query: Original user query
            duration_ms: Execution time in ms
            metadata: Additional metadata (model, config, etc.)

        Returns:
            The FlywheelRecord created
        """
        report = agent_output.get("diagnosis")
        if isinstance(report, dict):
            severity = report.get("overall_severity", "healthy")
            labels = [d.get("label", "") for d in report.get("diagnoses", [])]
        else:
            # No diagnosis — run it now
            diag = diagnose_agent_output(agent_output, query)
            severity = diag.overall_severity
            labels = [d.label for d in diag.diagnoses]

        record = FlywheelRecord(
            timestamp=time.time(),
            query=query or agent_output.get("query", ""),
            status=agent_output.get("status", "unknown"),
            overall_severity=severity,
            labels=labels,
            duration_ms=duration_ms,
            metadata=metadata or {},
        )

        self.records.append(record)

        # Track query patterns for hotspot analysis
        pattern = self._extract_pattern(query or "")
        if pattern and labels:
            self._query_patterns[pattern].extend(labels)

        return record

    def report(self, window: Optional[int] = None) -> FlywheelReport:
        """Generate aggregated pattern analysis.

        Args:
            window: Only analyze the last N records (None = all)
        """
        records = self.records[-window:] if window else self.records
        if not records:
            return FlywheelReport(
                total_runs=0,
                time_range=(0, 0),
                failure_rate=0,
                healthy_rate=0,
                degraded_rate=0,
                top_labels=[],
                failure_query_patterns=[],
                trend="stable",
                trend_metric=0,
                recommendations=["No data yet. Run more queries to build the flywheel."],
            )

        total = len(records)
        times = [r.timestamp for r in records]

        # Severity distribution
        failed = [r for r in records if r.overall_severity == "failed"]
        degraded = [r for r in records if r.overall_severity == "degraded"]
        healthy = [r for r in records if r.overall_severity == "healthy"]

        failure_rate = len(failed) / total
        degraded_rate = len(degraded) / total
        healthy_rate = len(healthy) / total

        # Top failure labels
        label_counter = Counter()
        failure_queries = Counter()
        for r in records:
            if r.overall_severity != "healthy":
                for label in r.labels:
                    label_counter[label] += 1
                    failure_queries[self._extract_pattern(r.query)] += 1

        top_labels = label_counter.most_common(10)
        failure_query_patterns = failure_queries.most_common(5)

        # Trend analysis: compare first half vs second half
        midpoint = len(records) // 2
        first_half = records[:midpoint]
        second_half = records[midpoint:]
        first_fail = sum(1 for r in first_half if r.overall_severity == "failed") / max(1, len(first_half))
        second_fail = sum(1 for r in second_half if r.overall_severity == "failed") / max(1, len(second_half))
        trend_metric = first_fail - second_fail  # Positive = improving

        if trend_metric > 0.05:
            trend = "improving"
        elif trend_metric < -0.05:
            trend = "worsening"
        else:
            trend = "stable"

        # Recommendations based on patterns
        recommendations = []
        if failure_rate > 0.3:
            recommendations.append(f"High failure rate ({failure_rate:.0%}). Prioritize fixing top issues.")
        if label_counter.get(Label.SQL_SYNTAX_ERROR, 0) > 2:
            recommendations.append("Frequent SQL syntax errors: audit CTE chain compilation.")
        if label_counter.get(Label.RESULT_EMPTY, 0) > 2:
            recommendations.append("Frequent empty results: expand mock data or time ranges.")
        if label_counter.get(Label.ROUTE_FAILURE, 0) > 1:
            recommendations.append("Route failures detected: add more regex patterns or use LLM routing.")
        if label_counter.get(Label.PLACEHOLDER_INSIGHT, 0) > 3:
            recommendations.append("Placeholder insights: ensure use_db=True and analysis layer is working.")
        if trend == "worsening":
            recommendations.append("Failure rate is INCREASING. Stop and investigate before continuing.")
        if not recommendations:
            recommendations.append("System is healthy. Continue monitoring.")

        # Sort recommendations by urgency
        urgent_keywords = ["High", "INCREASING", "Frequent"]
        recommendations.sort(
            key=lambda r: (
                0 if any(kw in r for kw in urgent_keywords) else 1,
                r,
            )
        )

        return FlywheelReport(
            total_runs=total,
            time_range=(min(times), max(times)),
            failure_rate=failure_rate,
            healthy_rate=healthy_rate,
            degraded_rate=degraded_rate,
            top_labels=top_labels,
            failure_query_patterns=failure_query_patterns,
            trend=trend,
            trend_metric=trend_metric,
            recommendations=recommendations,
        )

    def _extract_pattern(self, query: str) -> str:
        """Extract a simplified query pattern for grouping."""
        if not query:
            return "empty"
        # Normalize: remove specific dates, numbers, punctuation
        import re
        q = query.lower().strip()
        q = re.sub(r'\d+', 'N', q)
        q = re.sub(r'[？?。.！!，,]', '', q)
        # Keep meaningful keywords
        keywords = ["gmv", "订单", "渠道", "大区", "品类", "客单价", "均价",
                    "删除", "口径", "昨天", "今天", "本周", "本月", "最近"]
        found = [k for k in keywords if k in q]
        return "+".join(found) if found else q[:30]

    def best_strategy(self, diagnosis_label: str) -> Optional[str]:
        """Recommend the best remediation strategy based on historical data.

        Analyzes past cases with the same diagnosis to find which
        remediation was most effective.
        """
        # Find cases with this label
        cases = [r for r in self.records if diagnosis_label in r.labels]
        if not cases:
            return None

        # Check subsequent runs for the same query pattern
        success_count = 0
        total_count = 0
        for i, r in enumerate(self.records):
            if diagnosis_label in r.labels:
                # Check if next run for same pattern was successful
                pattern = self._extract_pattern(r.query)
                subsequent = [
                    s for j, s in enumerate(self.records)
                    if j > i and self._extract_pattern(s.query) == pattern
                ]
                if subsequent:
                    next_run = subsequent[0]
                    if next_run.overall_severity == "healthy":
                        success_count += 1
                total_count += 1

        if total_count == 0:
            return None

        success_rate = success_count / total_count
        if success_rate > 0.5:
            return f"Historical fix success rate: {success_rate:.0%} ({success_count}/{total_count})"
        return None

    def save(self, path: Optional[Path] = None) -> Path:
        """Persist flywheel data to disk."""
        filepath = path or (FLYWHEEL_DIR / "flywheel.json")
        data = {
            "records": [
                {
                    "timestamp": r.timestamp,
                    "query": r.query,
                    "status": r.status,
                    "overall_severity": r.overall_severity,
                    "labels": r.labels,
                    "duration_ms": r.duration_ms,
                    "metadata": r.metadata,
                }
                for r in self.records
            ],
            "query_patterns": dict(self._query_patterns),
        }
        filepath.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str))
        return filepath

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "Flywheel":
        """Load flywheel data from disk."""
        filepath = path or (FLYWHEEL_DIR / "flywheel.json")
        fw = cls()
        if not filepath.exists():
            return fw

        data = json.loads(filepath.read_text())
        for rdata in data.get("records", []):
            fw.records.append(FlywheelRecord(**rdata))
        fw._query_patterns = defaultdict(list, data.get("query_patterns", {}))
        return fw

    def reset(self):
        """Clear all records."""
        self.records.clear()
        self._query_patterns.clear()
