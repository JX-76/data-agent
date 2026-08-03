"""Shadow traffic replay: record → replay → compare for safe deployment.

Complete online evaluation pipeline (simulated offline):
  1. RECORD: Capture agent trajectories from a reference version
  2. REPLAY: Re-run the same queries through the new version
  3. COMPARE: Detect regressions in trajectory divergence

This simulates the "shadow mode" of a production deployment:
  - Reference = current version (production)
  - Candidate = new version
  - Both see identical inputs, candidate output is NOT served to users
  - Divergence = potential regression signal

Real production would add: traffic mirroring, canary percentage, A/B bucket.
This offline version uses recorded trajectories from deterministic (regex) mode.
"""

from __future__ import annotations

import json
import time
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Callable

import structlog

logger = structlog.get_logger("shadow")


# ══════════════════════════════════════════════════════════════
# Data Models
# ══════════════════════════════════════════════════════════════

@dataclass
class ShadowRun:
    """A single shadow comparison: reference vs candidate trajectory."""
    run_id: str
    query: str
    # Reference (current "production")
    ref_status: str = ""
    ref_tools: list[str] = field(default_factory=list)
    ref_sql: str = ""
    ref_result_count: int = 0
    ref_insight: str = ""
    ref_latency_ms: float = 0.0
    # Candidate (new version)
    cand_status: str = ""
    cand_tools: list[str] = field(default_factory=list)
    cand_sql: str = ""
    cand_result_count: int = 0
    cand_insight: str = ""
    cand_latency_ms: float = 0.0
    # Divergence
    diverged: bool = False
    divergence_type: str = ""  # status | tools | sql | result | insight
    divergence_detail: str = ""


@dataclass
class ShadowReport:
    total_queries: int
    matched: int
    diverged: int
    divergence_rate: float
    # By divergence type
    status_changes: int
    tool_divergences: int
    sql_divergences: int
    result_divergences: int
    insight_divergences: int
    # Performance
    avg_ref_latency: float
    avg_cand_latency: float
    latency_delta_pct: float  # (cand - ref) / ref * 100, negative = faster
    # Per-query detail
    runs: list[dict] = field(default_factory=list)
    # Verdict
    safe_to_deploy: bool = False
    risk_level: str = "low"  # low | medium | high | blocked
    warnings: list[str] = field(default_factory=list)


# ══════════════════════════════════════════════════════════════
# Shadow Runner
# ══════════════════════════════════════════════════════════════

class ShadowRunner:
    """Run shadow comparison between reference and candidate agent versions.

    Workflow:
      1. Record reference trajectories (run each query, capture output)
      2. Run candidate with same queries (different config/code/model)
      3. Compare trajectories: detect status changes, tool divergence, result changes
      4. Generate deployment risk report
    """

    DIVERGENCE_PATTERNS = {
        "status_ok_to_error": {
            "type": "status_change",
            "risk": "high",
            "message": "Query status changed from ok to error",
        },
        "status_blocked_to_ok": {
            "type": "status_change",
            "risk": "high",
            "message": "Previously blocked query now passes — potential safety bypass",
        },
        "tool_chain_changed": {
            "type": "tool_divergence",
            "risk": "medium",
            "message": "Tool selection changed — routing regression possible",
        },
        "sql_structure_changed": {
            "type": "sql_divergence",
            "risk": "medium",
            "message": "Generated SQL changed — review for correctness",
        },
        "result_count_diff": {
            "type": "result_divergence",
            "risk": "medium",
            "message": "Result row count changed",
        },
        "insight_changed": {
            "type": "insight_divergence",
            "risk": "low",
            "message": "Insight text changed (expected with model updates)",
        },
        "latency_degraded": {
            "type": "performance",
            "risk": "medium",
            "message": "Latency increased significantly",
        },
    }

    def __init__(self, ref_agent_fn: Callable, cand_agent_fn: Callable = None):
        self.ref_agent = ref_agent_fn
        self.cand_agent = cand_agent_fn or ref_agent_fn  # Default: same agent
        self.runs: list[ShadowRun] = []

    def run(self, queries: list[str]) -> ShadowReport:
        """Run shadow comparison on a set of queries."""
        self.runs = []

        for i, query in enumerate(queries):
            run_id = hashlib.md5(f"{query}{i}".encode()).hexdigest()[:8]

            # Reference
            t0 = time.time()
            ref_result = self.ref_agent(query)
            ref_latency = (time.time() - t0) * 1000

            # Candidate
            t0 = time.time()
            cand_result = self.cand_agent(query)
            cand_latency = (time.time() - t0) * 1000

            # Compare
            run = self._compare(run_id, query, ref_result, ref_latency,
                               cand_result, cand_latency)
            self.runs.append(run)

        return self._build_report()

    def _compare(self, run_id: str, query: str,
                 ref: dict, ref_latency: float,
                 cand: dict, cand_latency: float) -> ShadowRun:
        run = ShadowRun(
            run_id=run_id, query=query,
            ref_status=ref.get("status", "?"),
            ref_tools=self._extract_tools(ref),
            ref_sql=ref.get("sql", ""),
            ref_result_count=len(ref.get("results", [])),
            ref_insight=str(ref.get("insight", ""))[:200],
            ref_latency_ms=ref_latency,
            cand_status=cand.get("status", "?"),
            cand_tools=self._extract_tools(cand),
            cand_sql=cand.get("sql", ""),
            cand_result_count=len(cand.get("results", [])),
            cand_insight=str(cand.get("insight", ""))[:200],
            cand_latency_ms=cand_latency,
        )

        # Detect divergences in priority order (most severe first)
        divergences = []

        # 1. Status changes
        if run.ref_status != run.cand_status:
            if run.ref_status == "ok" and run.cand_status != "ok":
                divergences.append(("status_ok_to_error", "high"))
            elif run.ref_status == "blocked" and run.cand_status == "ok":
                divergences.append(("status_blocked_to_ok", "high"))
            else:
                divergences.append(("status_change", "medium"))

        # 2. Tool chain divergence
        if run.ref_tools != run.cand_tools:
            divergences.append(("tool_chain_changed", "medium"))

        # 3. SQL structure change (normalize whitespace for comparison)
        if self._normalize_sql(run.ref_sql) != self._normalize_sql(run.cand_sql):
            divergences.append(("sql_structure_changed", "medium"))

        # 4. Result count difference
        if run.ref_result_count != run.cand_result_count:
            divergences.append(("result_count_diff", "medium"))

        # 5. Insight change
        if run.ref_insight != run.cand_insight:
            divergences.append(("insight_changed", "low"))

        # 6. Performance
        if cand_latency > ref_latency * 2 and ref_latency > 10:
            divergences.append(("latency_degraded", "medium"))

        if divergences:
            run.diverged = True
            # Use the highest-risk divergence type
            risk_order = {"high": 0, "medium": 1, "low": 2}
            primary = sorted(divergences, key=lambda d: risk_order.get(d[1], 99))[0]
            run.divergence_type = primary[0]
            run.divergence_detail = "; ".join(
                self.DIVERGENCE_PATTERNS.get(d[0], {}).get("message", d[0])
                for d in divergences
            )

        return run

    def _extract_tools(self, result: dict) -> list[str]:
        """Extract tool chain from agent result.

        Handles two formats:
        - ReAct: result["steps"] → action → tool + args
        - DAG:   result["trace"] → op string → tool name
        """
        tools = []

        # ReAct format
        for step in result.get("steps", []):
            action = step.get("action", {})
            if isinstance(action, dict) and action.get("action") == "tool":
                tools.append(action.get("tool", "?"))

        # DAG format: trace entries with op strings like "switch(order_detail)"
        if not tools:
            for entry in result.get("trace", []):
                op = entry.get("op", "")
                if op and "(" in op:
                    tool = op.split("(")[0]
                    # Normalize tool names
                    if tool == "filter":
                        tools.append("filter")
                    elif tool == "aggregate":
                        tools.append("aggregate")
                    elif tool == "preview":
                        tools.append("preview")
                    elif tool == "switch":
                        tools.append("switch")
                    elif tool == "filter_value":
                        tools.append("filter_value")
                    elif tool == "merge":
                        tools.append("merge")
                    elif tool == "compare_periods":
                        tools.append("compare_periods")
                    elif tool == "sort":
                        tools.append("sort")
                    elif tool == "top":
                        tools.append("top")
                    else:
                        tools.append(tool)

        return tools

    def _normalize_sql(self, sql: str) -> str:
        """Normalize SQL for comparison (collapse whitespace)."""
        import re
        if not sql:
            return ""
        return re.sub(r'\s+', ' ', sql.strip().lower())

    def _build_report(self) -> ShadowReport:
        total = len(self.runs)
        matched = sum(1 for r in self.runs if not r.diverged)
        diverged = total - matched

        # Count by type
        status_changes = sum(1 for r in self.runs if "status" in r.divergence_type)
        tool_div = sum(1 for r in self.runs if "tool" in r.divergence_type)
        sql_div = sum(1 for r in self.runs if "sql" in r.divergence_type)
        result_div = sum(1 for r in self.runs if "result" in r.divergence_type)
        insight_div = sum(1 for r in self.runs if "insight" in r.divergence_type)

        # Performance
        ref_lats = [r.ref_latency_ms for r in self.runs if r.ref_latency_ms > 0]
        cand_lats = [r.cand_latency_ms for r in self.runs if r.cand_latency_ms > 0]
        avg_ref = sum(ref_lats) / len(ref_lats) if ref_lats else 0
        avg_cand = sum(cand_lats) / len(cand_lats) if cand_lats else 0
        latency_delta = ((avg_cand - avg_ref) / avg_ref * 100) if avg_ref > 0 else 0

        # Risk assessment
        warnings = []
        risk_level = "low"

        high_risk_count = sum(1 for r in self.runs
                             if r.divergence_detail and "high" in r.divergence_detail)
        if status_changes > 0:
            risk_level = "high"
            warnings.append(f"{status_changes} queries changed status (high risk)")
        elif diverged > total * 0.3:
            risk_level = "medium"
            warnings.append(f"{diverged}/{total} queries diverged (>30%)")
        elif diverged > total * 0.1:
            risk_level = "medium"
            warnings.append(f"{diverged}/{total} queries diverged (>10%)")
        elif latency_delta > 50:
            risk_level = "medium"
            warnings.append(f"Latency increased by {latency_delta:.0f}%")

        safe = risk_level == "low" and diverged == 0

        return ShadowReport(
            total_queries=total,
            matched=matched,
            diverged=diverged,
            divergence_rate=diverged / total if total else 0,
            status_changes=status_changes,
            tool_divergences=tool_div,
            sql_divergences=sql_div,
            result_divergences=result_div,
            insight_divergences=insight_div,
            avg_ref_latency=avg_ref,
            avg_cand_latency=avg_cand,
            latency_delta_pct=round(latency_delta, 1),
            runs=[r.__dict__ for r in self.runs],
            safe_to_deploy=safe,
            risk_level=risk_level,
            warnings=warnings,
        )


# ══════════════════════════════════════════════════════════════
# Replay from stored trajectories
# ══════════════════════════════════════════════════════════════

def replay_from_trajectories(traj_path: str, agent_fn: Callable) -> ShadowReport:
    """Replay queries from stored trajectories through the current agent.

    This is the offline equivalent of "shadow mode":
      - Load recorded trajectories as reference
      - Re-run each query through the current agent
      - Compare for regressions

    Useful for CI: before merging, replay golden trajectories against new code.
    """
    path = Path(traj_path)
    if not path.exists():
        logger.warning("trajectory_file_missing", path=str(path))
        return ShadowReport(
            total_queries=0, matched=0, diverged=0, divergence_rate=0.0,
            status_changes=0, tool_divergences=0, sql_divergences=0,
            result_divergences=0, insight_divergences=0,
            avg_ref_latency=0.0, avg_cand_latency=0.0, latency_delta_pct=0.0,
            safe_to_deploy=False, risk_level="blocked",
            warnings=["No trajectory data to compare against"],
        )

    runner = ShadowRunner(ref_agent_fn=agent_fn)

    queries = []
    for line in path.read_text().splitlines():
        if line.strip():
            try:
                data = json.loads(line)
                queries.append(data.get("query", ""))
            except json.JSONDecodeError:
                continue

    return runner.run(queries)
