"""Agent trajectory recording and step-by-step evaluation.

Two-layer evaluation:
  Layer 1 (Result): task completion rate — did the agent answer correctly?
  Layer 2 (Trajectory): step quality — was each tool call correct, efficient?

Key metrics (7 dimensions):
  1) Tool Call Accuracy      — proportion of correctly chosen tools
  2) Argument Accuracy       — proportion of correct parameters
  3) Unnecessary Steps       — redundant or no-op tool calls
  4) Invalid Cycles          — retry-then-abandon loops
  5) Step Efficiency         — (minimum possible steps) / (actual steps)
  6) Token Efficiency        — tokens consumed vs query complexity
  7) Reasoning Consistency   — claimed reasoning vs actual trajectory match

Extended evaluation (industrial-grade):
  - Coverage: does the answer cover N expected key points?
  - Citation: is every factual claim backed by a source reference?
  - Module-level: planning → retrieval → tool → generation scored separately
  - Edit Distance: Levenshtein alignment of actual vs gold trajectory sequence

Trajectory data is the foundation for:
  - LLM-as-Judge calibration (need trajectories to judge)
  - Shadow replay (record trajectory, replay, compare divergence)
  - Regression detection (trajectory change = potential regression)
"""

from __future__ import annotations

import json
import time
import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Optional

import structlog

logger = structlog.get_logger("trajectory")


# ══════════════════════════════════════════════════════════════
# Data Models
# ══════════════════════════════════════════════════════════════

@dataclass
class ToolCall:
    """A single tool invocation within a trajectory."""
    tool: str
    args: dict
    result_summary: str  # Compact result (row count, error, etc.)
    dataid: str = ""
    success: bool = True
    error: str = ""
    latency_ms: float = 0.0
    # Token tracking
    token_count: int = 0                    # Tokens consumed by this tool call
    reasoning_text: str = ""                # Agent's stated reasoning for this step
    # Evaluation annotations (filled by evaluator)
    tool_correct: Optional[bool] = None      # Was this the right tool?
    args_correct: Optional[bool] = None       # Were the arguments correct?
    necessary: Optional[bool] = None          # Was this step necessary?
    optimal_tool: Optional[str] = None        # What should have been called?
    module_phase: str = ""                    # planning | retrieval | tool | generation


@dataclass
class Trajectory:
    """Complete execution trace of one agent run."""
    trace_id: str
    query: str
    intent: str = ""
    model: str = ""
    metric: str = ""
    status: str = ""

    # Step-by-step tool calls
    tool_calls: list[ToolCall] = field(default_factory=list)

    # Final output
    sql: str = ""
    results: list[dict] = field(default_factory=list)
    insight: str = ""
    error: str = ""

    # Token consumption
    token_count_total: int = 0

    # Coverage: expected key points in answer
    expected_key_points: list[str] = field(default_factory=list)
    covered_key_points: list[str] = field(default_factory=list)

    # Citation tracking
    citation_count: int = 0
    citations_valid: int = 0
    answer_text: str = ""  # Full NL answer for citation scanning

    # Module phase breakdown: planning → retrieval → tool → generation
    module_phases: dict = field(default_factory=dict)  # {"planning": {...}, ...}

    # Gold trajectory for edit distance comparison
    gold_sequence: list[str] = field(default_factory=list)

    # Timing
    total_latency_ms: float = 0.0
    step_count: int = 0
    retry_count: int = 0

    # Evaluation scores (filled by TrajectoryEvaluator)
    scores: dict = field(default_factory=dict)
    judged_at: float = 0.0

    def to_dict(self) -> dict:
        return {
            "trace_id": self.trace_id,
            "query": self.query,
            "intent": self.intent,
            "model": self.model,
            "metric": self.metric,
            "status": self.status,
            "tool_calls": [
                {
                    "tool": tc.tool, "args": tc.args,
                    "result": tc.result_summary, "dataid": tc.dataid,
                    "success": tc.success, "error": tc.error,
                    "latency_ms": tc.latency_ms,
                    "token_count": tc.token_count,
                    "reasoning_text": tc.reasoning_text[:120],
                    "tool_correct": tc.tool_correct,
                    "args_correct": tc.args_correct,
                    "necessary": tc.necessary,
                    "optimal_tool": tc.optimal_tool,
                    "module_phase": tc.module_phase,
                }
                for tc in self.tool_calls
            ],
            "sql": self.sql,
            "result_count": len(self.results),
            "insight": self.insight[:200],
            "error": self.error,
            "token_count_total": self.token_count_total,
            "expected_key_points": self.expected_key_points,
            "covered_key_points": self.covered_key_points,
            "citation_count": self.citation_count,
            "citations_valid": self.citations_valid,
            "gold_sequence": self.gold_sequence,
            "module_phases": self.module_phases,
            "total_latency_ms": self.total_latency_ms,
            "step_count": self.step_count,
            "retry_count": self.retry_count,
            "scores": self.scores,
        }


# ══════════════════════════════════════════════════════════════
# Trajectory Recorder
# ══════════════════════════════════════════════════════════════

class TrajectoryRecorder:
    """Instruments the agent to capture complete execution trajectory.

    Usage:
        recorder = TrajectoryRecorder()
        traj = recorder.start("昨天GMV是多少？")

        # After each tool call:
        recorder.record_tool("switch", {"model_id": "order_detail"}, "120 rows", "d1")

        # At completion:
        recorder.finish(status="ok", sql="SELECT ...", results=[...], insight="...")
        traj = recorder.trajectory  # Full trajectory ready for evaluation
    """

    def __init__(self):
        self.trajectory: Optional[Trajectory] = None
        self._start_time: float = 0.0
        self._token_total: int = 0

    def start(self, query: str, intent: str = "", model: str = "",
              metric: str = "", expected_key_points: list[str] = None,
              gold_sequence: list[str] = None) -> Trajectory:
        self._start_time = time.time()
        self._token_total = 0
        self.trajectory = Trajectory(
            trace_id=hashlib.md5(f"{query}{time.time()}".encode()).hexdigest()[:12],
            query=query,
            intent=intent,
            model=model,
            metric=metric,
            expected_key_points=expected_key_points or [],
            gold_sequence=gold_sequence or [],
        )
        return self.trajectory

    def record_tool(self, tool: str, args: dict, result_summary: str,
                    dataid: str = "", success: bool = True, error: str = "",
                    latency_ms: float = 0.0, token_count: int = 0,
                    reasoning_text: str = "", module_phase: str = ""):
        if self.trajectory is None:
            return

        self._token_total += token_count
        tc = ToolCall(
            tool=tool, args=args, result_summary=result_summary,
            dataid=dataid, success=success, error=error,
            latency_ms=latency_ms, token_count=token_count,
            reasoning_text=reasoning_text, module_phase=module_phase,
        )
        self.trajectory.tool_calls.append(tc)
        if not success:
            self.trajectory.retry_count += 1

    def record_retry(self, tool: str, args: dict, error: str,
                     token_count: int = 0, reasoning_text: str = ""):
        """Record a failed attempt that triggered retry."""
        if self.trajectory is None:
            return
        self._token_total += token_count
        self.trajectory.retry_count += 1
        self.trajectory.tool_calls.append(ToolCall(
            tool=tool, args=args, result_summary=error,
            success=False, error=error, token_count=token_count,
            reasoning_text=reasoning_text,
        ))

    def set_answer(self, answer_text: str):
        """Set the full NL answer for citation scanning."""
        if self.trajectory:
            self.trajectory.answer_text = answer_text

    def set_coverage(self, covered_key_points: list[str]):
        """Set which key points were covered in the answer."""
        if self.trajectory:
            self.trajectory.covered_key_points = covered_key_points

    def set_citations(self, total: int, valid: int):
        """Set citation counts."""
        if self.trajectory:
            self.trajectory.citation_count = total
            self.trajectory.citations_valid = valid

    def set_module_phases(self, phases: dict):
        """Set per-module-phase metrics. Keys: planning, retrieval, tool, generation."""
        if self.trajectory:
            self.trajectory.module_phases = phases

    def finish(self, status: str, sql: str = "", results: list[dict] = None,
               insight: str = "", error: str = "", token_overhead: int = 0):
        if self.trajectory is None:
            return
        self.trajectory.status = status
        self.trajectory.sql = sql
        self.trajectory.results = results or []
        self.trajectory.insight = insight
        self.trajectory.error = error
        self.trajectory.token_count_total = self._token_total + token_overhead
        # Guard against ultra-fast finishes on low-resolution clocks so downstream
        # consumers can always rely on a strictly positive observed latency once
        # a trajectory is marked as finished.
        elapsed = (time.time() - self._start_time) * 1000
        self.trajectory.total_latency_ms = max(0.001, elapsed)
        self.trajectory.step_count = len(self.trajectory.tool_calls)



# ══════════════════════════════════════════════════════════════
# Sequence Alignment — Levenshtein Edit Distance
# ══════════════════════════════════════════════════════════════

def levenshtein_sequence(actual: list[str], gold: list[str]) -> tuple[int, float]:
    """Compute Levenshtein edit distance between two sequences of tool names.

    Returns (edit_distance, normalized_similarity).

    normalized_similarity = 1 - (edit_distance / max(len(actual), len(gold)))
    Range: [0.0, 1.0], where 1.0 = identical sequences.

    This measures how far the agent's trajectory deviated from the gold path.
    """
    m, n = len(actual), len(gold)
    if m == 0 and n == 0:
        return 0, 1.0
    if m == 0 or n == 0:
        return max(m, n), 0.0

    # DP matrix
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(m + 1):
        dp[i][0] = i
    for j in range(n + 1):
        dp[0][j] = j

    for i in range(1, m + 1):
        for j in range(1, n + 1):
            cost = 0 if actual[i - 1] == gold[j - 1] else 1
            dp[i][j] = min(
                dp[i - 1][j] + 1,     # deletion
                dp[i][j - 1] + 1,     # insertion
                dp[i - 1][j - 1] + cost,  # substitution
            )

    edit_distance = dp[m][n]
    max_len = max(m, n)
    similarity = 1.0 - (edit_distance / max_len)
    return edit_distance, similarity


def longest_common_subsequence(seq_a: list[str], seq_b: list[str]) -> list[str]:
    """Return the longest common subsequence (preserving order)."""
    m, n = len(seq_a), len(seq_b)
    dp = [[0] * (n + 1) for _ in range(m + 1)]

    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if seq_a[i - 1] == seq_b[j - 1]:
                dp[i][j] = dp[i - 1][j - 1] + 1
            else:
                dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])

    # Backtrack
    lcs = []
    i, j = m, n
    while i > 0 and j > 0:
        if seq_a[i - 1] == seq_b[j - 1]:
            lcs.append(seq_a[i - 1])
            i -= 1
            j -= 1
        elif dp[i - 1][j] > dp[i][j - 1]:
            i -= 1
        else:
            j -= 1
    lcs.reverse()
    return lcs


# ══════════════════════════════════════════════════════════════
# Trajectory Evaluator (Extended — 7 dimensions)
# ══════════════════════════════════════════════════════════════

@dataclass
class TrajectoryScores:
    """Evaluation scores for a single trajectory."""
    # ── Layer 1: Result ──
    result_correct: bool = False
    result_error: str = ""

    # ── Layer 2: Trajectory (5 core) ──
    tool_call_accuracy: float = 0.0       # Correct tools / total tools
    argument_accuracy: float = 0.0         # Correct args / total tools
    unnecessary_steps: int = 0             # Redundant or no-op calls
    invalid_cycles: int = 0                # Retry loops that failed
    step_efficiency: float = 0.0           # Optimal / actual steps

    # ── Token efficiency ──
    token_efficiency: float = 0.0          # How token-efficient was this run?
    token_total: int = 0

    # ── Reasoning consistency ──
    reasoning_consistency: float = 0.0     # How well does reasoning match trajectory?
    reasoning_gaps: list[str] = field(default_factory=list)

    # ── Coverage ──
    coverage_score: float = 0.0            # Covered key points / expected key points
    missed_points: list[str] = field(default_factory=list)

    # ── Citation ──
    citation_score: float = 1.0            # Valid citations / total citations (or 1 if no citation expected)
    citation_penalty: str = ""

    # ── Module-level ──
    module_scores: dict = field(default_factory=dict)

    # ── Edit distance ──
    edit_distance: int = 0
    edit_distance_score: float = 0.0       # 1 - normalized edit distance
    lcs_length: int = 0

    # ── Composite ──
    overall_score: float = 0.0             # Weighted aggregate
    grade: str = "F"                       # A/B/C/D/F

    # Breakdown
    per_step: list[dict] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class TrajectoryEvaluator:
    """Evaluates agent trajectories against expected optimal paths.

    For each query intent, defines the optimal tool chain.
    Compares actual vs. optimal to compute trajectory metrics.

    7 evaluation dimensions:
      tool_call_accuracy, argument_accuracy, unnecessary_steps,
      invalid_cycles, step_efficiency, token_efficiency, reasoning_consistency
    + industrial extensions: coverage, citation, module-level, edit distance
    """

    # Optimal tool chains per intent
    OPTIMAL_PATHS = {
        "metric_query": [
            "switch", "filter", "aggregate",
        ],
        "breakdown": [
            "switch", "filter", "aggregate",
        ],
        "filter_value": [
            "switch", "filter", "filter_value", "aggregate",
        ],
        "merge": [
            "switch", "filter", "aggregate",
            "switch", "filter", "aggregate",
            "merge",
        ],
        "compare_periods": [
            "switch", "filter", "compare_periods",
        ],
        "blocked": [],  # Should be blocked before any tools
    }

    # Expected token budgets per intent (for token efficiency scoring)
    TOKEN_BUDGETS = {
        "metric_query": 3000,
        "breakdown": 4000,
        "filter_value": 4500,
        "merge": 7000,
        "compare_periods": 6000,
        "blocked": 500,
    }

    # Tools that are ALWAYS redundant (never needed consecutively)
    REDUNDANT_PAIRS = {
        ("preview", "preview"),
        ("switch", "switch"),
    }

    # Reasoning-tool consistency map: reasoning keywords → expected tool
    # Shorter substrings match longer text: "选择订单明细模型" contains "选择"
    REASONING_TOOL_MAP = {
        "选择模型": "switch",
        "切换": "switch",
        "选择": "switch",   # Generic: "选择订单明细模型"
        "过滤时间": "filter",
        "过滤": "filter",
        "筛选": "filter",
        "时间范围": "filter",
        "汇总": "aggregate",
        "聚合": "aggregate",
        "计算": "aggregate",
        "分组": "aggregate",
        "渠道": "aggregate",
        "合并": "merge",
        "对比": "compare_periods",
        "排序": "sort",
        "翻看": "preview",
        "查看": "preview",
        "筛选维度": "filter_value",
    }

    # Citation patterns to scan in answers
    CITATION_PATTERNS = [
        r'\[来源[：:\s]*([^\]]+)\]',
        r'\[ref[：:\s]*([^\]]+)\]',
        r'\[引用[：:\s]*([^\]]+)\]',
        r'基于.*?数据',
        r'根据.*?（表|数据）',
        r'source[：:\s]*([^\]]+)',
    ]

    # ──── Main evaluate ────

    def evaluate(self, trajectory: Trajectory) -> TrajectoryScores:
        """Evaluate a trajectory across all 7+ dimensions."""
        scores = TrajectoryScores()

        if not trajectory.tool_calls:
            scores.result_correct = trajectory.status in ("ok", "blocked")
            scores.overall_score = 100.0 if scores.result_correct else 0.0
            scores.grade = "A" if scores.result_correct else "F"
            scores.token_efficiency = 1.0
            scores.reasoning_consistency = 1.0
            scores.coverage_score = 1.0
            scores.citation_score = 1.0
            scores.edit_distance_score = 1.0
            return scores

        # ── Layer 1: Result evaluation ──
        scores.result_correct = trajectory.status == "ok"
        if not scores.result_correct:
            scores.result_error = trajectory.error or trajectory.status
            scores.issues.append(f"Result: {trajectory.status}")

        # ── Layer 2: Trajectory evaluation (5 core metrics) ──
        intent = trajectory.intent or self._infer_intent(trajectory)
        optimal = self.OPTIMAL_PATHS.get(intent, [])

        tools_called = [tc.tool for tc in trajectory.tool_calls if tc.success]
        tools_all = [tc.tool for tc in trajectory.tool_calls]

        # 1. Tool call accuracy
        if optimal:
            correct_tools = 0
            for i, actual in enumerate(tools_called):
                expected = optimal[i] if i < len(optimal) else None
                is_correct = (actual == expected)
                if is_correct:
                    correct_tools += 1
                if i < len(trajectory.tool_calls):
                    trajectory.tool_calls[i].tool_correct = is_correct
                    trajectory.tool_calls[i].optimal_tool = expected
            scores.tool_call_accuracy = correct_tools / len(tools_called) if tools_called else 0.0
        else:
            scores.tool_call_accuracy = 0.0

        # 2. Argument accuracy
        arg_correct = 0
        for tc in trajectory.tool_calls:
            if tc.success:
                ok = self._validate_args(tc.tool, tc.args)
                if ok:
                    arg_correct += 1
                tc.args_correct = ok
            else:
                tc.args_correct = False
        scores.argument_accuracy = arg_correct / len(trajectory.tool_calls) if trajectory.tool_calls else 0.0

        # 3. Unnecessary steps
        unnecessary = 0
        for i in range(1, len(tools_called)):
            pair = (tools_called[i-1], tools_called[i])
            if pair in self.REDUNDANT_PAIRS:
                unnecessary += 1
                if i-1 < len(trajectory.tool_calls):
                    trajectory.tool_calls[i-1].necessary = False

        for i, tc in enumerate(trajectory.tool_calls):
            if tc.tool == "preview" and tc.success:
                next_tools = tools_called[i+1:i+2]
                if not any(t in ("filter", "aggregate", "filter_value") for t in next_tools):
                    tc.necessary = False
                    unnecessary += 1

        scores.unnecessary_steps = unnecessary

        # 4. Invalid cycles
        failed_retries = sum(1 for tc in trajectory.tool_calls if not tc.success)
        scores.invalid_cycles = failed_retries

        # 5. Step efficiency
        optimal_count = len(optimal) if optimal else 1
        actual_count = len(tools_called)
        scores.step_efficiency = min(1.0, optimal_count / actual_count) if actual_count > 0 else 0.0

        # ── 6. Token efficiency ──
        scores.token_total = trajectory.token_count_total
        scores.token_efficiency = self._eval_token_efficiency(trajectory, intent)

        # ── 7. Reasoning consistency ──
        cons_result = self._eval_reasoning_consistency(trajectory)
        scores.reasoning_consistency = cons_result["score"]
        scores.reasoning_gaps = cons_result["gaps"]
        if cons_result["gaps"]:
            scores.warnings.extend(cons_result["gaps"])

        # ── Industrial extensions ──

        # Coverage
        coverage_result = self._eval_coverage(trajectory)
        scores.coverage_score = coverage_result["score"]
        scores.missed_points = coverage_result["missed"]
        if coverage_result["missed"]:
            scores.warnings.append(f"Missed key points: {', '.join(coverage_result['missed'])}")

        # Citation
        citation_result = self._eval_citations(trajectory)
        scores.citation_score = citation_result["score"]
        scores.citation_penalty = citation_result["reason"]
        if citation_result["reason"]:
            scores.warnings.append(citation_result["reason"])

        # Module-level
        scores.module_scores = self._eval_modules(trajectory)

        # Edit distance
        edit_result = self._compute_edit_distance(trajectory)
        scores.edit_distance = edit_result["distance"]
        scores.edit_distance_score = edit_result["score"]
        scores.lcs_length = edit_result["lcs_length"]
        if edit_result["score"] < 0.5:
            scores.warnings.append(f"Trajectory diverged significantly from gold (edit_score={edit_result['score']:.2f})")

        # ── Composite score (updated weights) ──
        penalty = (unnecessary + failed_retries) * 0.04
        scores.overall_score = max(0.0, min(1.0,
            0.20 * (1.0 if scores.result_correct else 0.0) +
            0.20 * scores.tool_call_accuracy +
            0.10 * scores.argument_accuracy +
            0.10 * scores.step_efficiency +
            0.10 * scores.token_efficiency +
            0.10 * scores.reasoning_consistency +
            0.10 * scores.coverage_score +
            0.05 * scores.citation_score +
            0.05 * scores.edit_distance_score -
            penalty
        )) * 100

        # Grade
        if scores.overall_score >= 90:
            scores.grade = "A"
        elif scores.overall_score >= 80:
            scores.grade = "B"
        elif scores.overall_score >= 70:
            scores.grade = "C"
        elif scores.overall_score >= 60:
            scores.grade = "D"
        else:
            scores.grade = "F"

        # Per-step breakdown
        scores.per_step = []
        for i, tc in enumerate(trajectory.tool_calls):
            scores.per_step.append({
                "step": i + 1,
                "tool": tc.tool,
                "tool_correct": tc.tool_correct,
                "args_correct": tc.args_correct,
                "necessary": tc.necessary,
                "optimal": tc.optimal_tool,
                "token_count": tc.token_count,
                "module_phase": tc.module_phase,
            })

        # Collect issues
        if unnecessary > 0:
            scores.issues.append(f"{unnecessary} unnecessary steps")
        if failed_retries > 0:
            scores.issues.append(f"{failed_retries} failed retries")
        if scores.tool_call_accuracy < 0.5:
            scores.issues.append(f"Tool accuracy only {scores.tool_call_accuracy:.0%}")
        if scores.argument_accuracy < 0.5:
            scores.issues.append(f"Argument accuracy only {scores.argument_accuracy:.0%}")
        if scores.token_efficiency < 0.3:
            scores.issues.append(f"Token efficiency very low ({scores.token_total} tokens)")
        if scores.reasoning_consistency < 0.5:
            scores.issues.append(f"Reasoning-trajectory gap: {scores.reasoning_consistency:.0%} consistent")

        # Annotate trajectory
        trajectory.scores = {
            "result_correct": scores.result_correct,
            "tool_call_accuracy": round(scores.tool_call_accuracy, 3),
            "argument_accuracy": round(scores.argument_accuracy, 3),
            "unnecessary_steps": scores.unnecessary_steps,
            "invalid_cycles": scores.invalid_cycles,
            "step_efficiency": round(scores.step_efficiency, 3),
            "token_efficiency": round(scores.token_efficiency, 3),
            "token_total": scores.token_total,
            "reasoning_consistency": round(scores.reasoning_consistency, 3),
            "coverage_score": round(scores.coverage_score, 3),
            "citation_score": round(scores.citation_score, 3),
            "edit_distance_score": round(scores.edit_distance_score, 3),
            "module_scores": scores.module_scores,
            "overall_score": round(scores.overall_score, 1),
            "grade": scores.grade,
            "issues": scores.issues,
            "warnings": scores.warnings,
        }
        trajectory.judged_at = time.time()

        return scores

    # ──── Sub-evaluators ────

    def _eval_token_efficiency(self, trajectory: Trajectory, intent: str) -> float:
        """Score token usage against expected budget for this intent."""
        budget = self.TOKEN_BUDGETS.get(intent, 5000)
        actual = trajectory.token_count_total
        if actual == 0:
            return 1.0  # No token info = assume efficient
        # Under budget is good, over budget is penalized logarithmically
        if actual <= budget:
            return 1.0
        ratio = budget / actual
        # Penalize but not too harshly — 2x budget = 0.5, 4x = 0.25
        return ratio

    def _eval_reasoning_consistency(self, trajectory: Trajectory) -> dict:
        """Check if the agent's stated reasoning matches the tools it actually called.

        Returns {"score": float, "gaps": list[str]}.
        Score 1.0 = perfect consistency, 0.0 = complete mismatch.
        """
        gaps = []
        total_tc = len(trajectory.tool_calls)
        consistent_count = 0

        for i, tc in enumerate(trajectory.tool_calls):
            if not tc.reasoning_text:
                # No reasoning text provided — assume consistent (legacy data)
                consistent_count += 1
                continue

            # Check reasoning text for keywords indicating expected tool
            # Uses substring matching: "选择订单明细模型" should match keyword "选择"
            expected_tool = None
            best_match_len = 0
            for keyword, tool_name in self.REASONING_TOOL_MAP.items():
                if keyword in tc.reasoning_text:
                    # Prefer longer keyword matches ("选择模型" > "选择")
                    if len(keyword) > best_match_len:
                        expected_tool = tool_name
                        best_match_len = len(keyword)

            if expected_tool is None:
                # Also try loose matching: check if any keyword character matches
                for keyword, tool_name in self.REASONING_TOOL_MAP.items():
                    # Split keyword and check individual characters
                    if len(keyword) >= 2 and keyword[:2] in tc.reasoning_text:
                        expected_tool = tool_name
                        break

            if expected_tool is None:
                # Reasoning doesn't map to any known tool pattern
                gaps.append(f"Step {i+1} ({tc.tool}): reasoning doesn't map to any tool")
                continue

            if expected_tool != tc.tool:
                gaps.append(
                    f"Step {i+1}: reasoning suggests '{expected_tool}' but called '{tc.tool}'"
                )
                continue

            consistent_count += 1

        score = consistent_count / total_tc if total_tc > 0 else 1.0
        return {"score": score, "gaps": gaps}

    def _eval_coverage(self, trajectory: Trajectory) -> dict:
        """Check how many expected key points are covered in the answer.

        Returns {"score": float, "missed": list[str]}.
        """
        expected = trajectory.expected_key_points
        covered = set(trajectory.covered_key_points)
        answer = (trajectory.insight + " " + trajectory.answer_text).lower()

        if not expected:
            return {"score": 1.0, "missed": []}  # No expectations = full score

        missed = []
        found = 0
        for i, point in enumerate(expected):
            is_covered = False
            if point.lower() in answer:
                is_covered = True
            elif point in covered:
                is_covered = True
            # Also check if SQL/insight covers it implicitly
            elif any(kw.lower() in answer for kw in point.split()):
                is_covered = True

            if is_covered:
                found += 1
            else:
                missed.append(point)

        score = found / len(expected)
        return {"score": score, "missed": missed}

    def _eval_citations(self, trajectory: Trajectory) -> dict:
        """Evaluate citation quality in the answer.

        Returns {"score": float, "reason": str}.
        Score 1.0 = all claims cited, 0.0 = no citations when expected.
        """
        # If no answer text AND no explicit citation count, assume no requirement
        text = trajectory.answer_text or (trajectory.insight + " " + trajectory.sql)
        if not text.strip() and trajectory.citation_count == 0:
            return {"score": 1.0, "reason": ""}
        if not text.strip():
            return {"score": 1.0, "reason": ""}

        # If explicit citation counts are provided, use them
        if trajectory.citation_count > 0:
            if trajectory.citations_valid >= trajectory.citation_count:
                return {"score": 1.0, "reason": ""}
            score = trajectory.citations_valid / trajectory.citation_count
            invalid = trajectory.citation_count - trajectory.citations_valid
            reason = f"{invalid}/{trajectory.citation_count} citations invalid or unverifiable"
            return {"score": score, "reason": reason}

        # Scan for citation patterns in answer text AND insight text
        found_citations = 0
        for pattern in self.CITATION_PATTERNS:
            matches = re.findall(pattern, text, re.IGNORECASE)
            found_citations += len(matches)

        # Heuristic: for factual answers, expect at least 1 citation
        is_factual = any(kw in text.lower() for kw in ["gmv", "订单", "销售", "数据", "统计", "sum", "count"])
        if is_factual and found_citations == 0:
            return {"score": 0.3, "reason": "Factual answer has no source citations"}
        elif found_citations == 0:
            return {"score": 1.0, "reason": "No factual claims detected, citation not required"}

        return {"score": 1.0, "reason": ""}

    def _eval_modules(self, trajectory: Trajectory) -> dict:
        """Evaluate agent performance broken down by module phase.

        Returns per-phase scores dict:
          { "planning": {score, issues}, "retrieval": {...}, ... }
        """
        phases = {"planning": [], "retrieval": [], "tool": [], "generation": []}

        # Classify tool calls into phases
        for tc in trajectory.tool_calls:
            phase = tc.module_phase or self._classify_phase(tc.tool)
            phases[phase].append(tc)

        module_scores = {}
        phase_labels = {
            "planning": "意图解析+模型选择",
            "retrieval": "数据检索+切换",
            "tool": "过滤+聚合+计算",
            "generation": "结果解释+图表",
        }

        for phase_name, calls in phases.items():
            if not calls:
                module_scores[phase_name] = {"score": 1.0, "calls": 0, "issues": []}
                continue

            # Per-phase: correct tool selection AND successful execution
            correct = sum(1 for tc in calls
                        if (tc.tool_correct is True or tc.tool_correct is None)
                        and tc.success)
            accuracy = correct / len(calls) if calls else 1.0
            issues = []
            for tc in calls:
                if tc.tool_correct is False:
                    issues.append(f"{tc.tool} should be {tc.optimal_tool}")
                if not tc.success:
                    issues.append(f"{tc.tool} failed: {tc.error[:60]}")

            module_scores[phase_name] = {
                "score": round(accuracy, 3),
                "calls": len(calls),
                "label": phase_labels.get(phase_name, phase_name),
                "issues": issues[:3],  # Cap at 3
            }

        # If we have explicit module_phases data, merge it
        if trajectory.module_phases:
            for phase_name, phase_data in trajectory.module_phases.items():
                if phase_name in module_scores:
                    module_scores[phase_name]["custom"] = phase_data.get("score", phase_data)

        return module_scores

    def _classify_phase(self, tool: str) -> str:
        """Classify a tool into standard eval phases."""
        planning_tools = {"clarify", "catalog"}
        retrieval_tools = {"switch", "preview"}
        tool_tools = {"filter", "aggregate", "sort", "top", "filter_value",
                     "merge", "compare_periods"}
        generation_tools = {"insight", "chart", "explain"}

        if tool in planning_tools:
            return "planning"
        if tool in retrieval_tools:
            return "retrieval"
        if tool in tool_tools:
            return "tool"
        if tool in generation_tools:
            return "generation"
        # Unknown tools default to tool phase
        return "tool"

    def _compute_edit_distance(self, trajectory: Trajectory) -> dict:
        """Compute Levenshtein edit distance between actual and gold trajectory.

        Returns {"distance": int, "score": float, "lcs_length": int}.
        """
        actual_seq = [tc.tool for tc in trajectory.tool_calls if tc.success]
        gold_seq = trajectory.gold_sequence

        if not gold_seq:
            # No gold sequence provided — compute using optimal path
            intent = trajectory.intent or self._infer_intent(trajectory)
            gold_seq = self.OPTIMAL_PATHS.get(intent, [])

        distance, similarity = levenshtein_sequence(actual_seq, gold_seq)
        lcs = longest_common_subsequence(actual_seq, gold_seq)

        # Also compare against optimal path for completeness
        intent = trajectory.intent or self._infer_intent(trajectory)
        optimal = self.OPTIMAL_PATHS.get(intent, [])
        if optimal and optimal != gold_seq:
            _, opt_similarity = levenshtein_sequence(actual_seq, optimal)
            # Take the better of the two comparisons
            similarity = max(similarity, opt_similarity)

        return {
            "distance": distance,
            "score": similarity,
            "lcs_length": len(lcs),
        }

    # ──── Helpers (unchanged from before) ────

    def _infer_intent(self, trajectory: Trajectory) -> str:
        """Infer intent from trajectory if not explicitly set."""
        tools = [tc.tool for tc in trajectory.tool_calls]
        if "merge" in tools:
            return "merge"
        if "compare_periods" in tools:
            return "compare_periods"
        if "filter_value" in tools:
            return "filter_value"
        if "aggregate" in tools:
            return "breakdown"
        if "filter" in tools:
            return "metric_query"
        return "unknown"

    def _validate_args(self, tool: str, args: dict) -> bool:
        """Quick argument sanity check."""
        if not isinstance(args, dict):
            return False

        if tool == "switch":
            return args.get("model_id") in ("order_detail", "user_summary", "product_analysis", None)
        if tool == "aggregate":
            dims = args.get("dimensions", [])
            return isinstance(dims, list) and all(isinstance(d, str) for d in dims)
        if tool == "preview":
            n = args.get("n", 5)
            return isinstance(n, int) and 0 < n <= 20
        if tool == "filter_value":
            return isinstance(args.get("dimension"), str) and isinstance(args.get("value"), str)
        return True  # Unknown tool, pass through


# ══════════════════════════════════════════════════════════════
# Batch Evaluator (run N trajectories, compute aggregate stats)
# ══════════════════════════════════════════════════════════════

@dataclass
class BatchReport:
    total: int
    passed: int
    # Result metrics
    completion_rate: float
    # Trajectory metrics
    avg_tool_accuracy: float
    avg_argument_accuracy: float
    avg_unnecessary_steps: float
    avg_invalid_cycles: float
    avg_step_efficiency: float
    avg_token_efficiency: float
    avg_reasoning_consistency: float
    avg_coverage_score: float
    avg_citation_score: float
    avg_edit_distance_score: float
    avg_overall_score: float
    # Distribution
    grade_distribution: dict
    # Per-trajectory
    per_trajectory: list[dict]
    # Per-module aggregates
    module_aggregates: dict = field(default_factory=dict)
    # Timestamp
    evaluated_at: float = field(default_factory=time.time)


class BatchEvaluator:
    """Evaluates a batch of trajectories and produces aggregate reports."""

    def __init__(self, evaluator: TrajectoryEvaluator = None):
        self.evaluator = evaluator or TrajectoryEvaluator()

    def evaluate_batch(self, trajectories: list[Trajectory]) -> BatchReport:
        all_scores = []
        for traj in trajectories:
            scores = self.evaluator.evaluate(traj)
            all_scores.append((traj, scores))

        total = len(all_scores)
        if total == 0:
            return _empty_report()

        passed = sum(1 for _, s in all_scores if s.result_correct)

        grades = {"A": 0, "B": 0, "C": 0, "D": 0, "F": 0}
        per_traj = []

        for traj, scores in all_scores:
            grades[scores.grade] = grades.get(scores.grade, 0) + 1
            per_traj.append({
                "trace_id": traj.trace_id,
                "query": traj.query[:60],
                "intent": traj.intent,
                "status": traj.status,
                "grade": scores.grade,
                "overall_score": scores.overall_score,
                "tool_accuracy": scores.tool_call_accuracy,
                "token_efficiency": scores.token_efficiency,
                "reasoning_consistency": scores.reasoning_consistency,
                "coverage_score": scores.coverage_score,
                "citation_score": scores.citation_score,
                "edit_distance_score": scores.edit_distance_score,
                "issues": scores.issues,
                "warnings": scores.warnings,
            })

        # Aggregate module scores
        module_aggregates = {"planning": [], "retrieval": [], "tool": [], "generation": []}
        for _, scores in all_scores:
            for phase_name, phase_data in scores.module_scores.items():
                if phase_name in module_aggregates and phase_data["calls"] > 0:
                    module_aggregates[phase_name].append(phase_data["score"])
        module_avg = {
            k: (sum(v) / len(v) if v else 1.0)
            for k, v in module_aggregates.items()
        }

        return BatchReport(
            total=total,
            passed=passed,
            completion_rate=passed / total,
            avg_tool_accuracy=sum(s.tool_call_accuracy for _, s in all_scores) / total,
            avg_argument_accuracy=sum(s.argument_accuracy for _, s in all_scores) / total,
            avg_unnecessary_steps=sum(s.unnecessary_steps for _, s in all_scores) / total,
            avg_invalid_cycles=sum(s.invalid_cycles for _, s in all_scores) / total,
            avg_step_efficiency=sum(s.step_efficiency for _, s in all_scores) / total,
            avg_token_efficiency=sum(s.token_efficiency for _, s in all_scores) / total,
            avg_reasoning_consistency=sum(s.reasoning_consistency for _, s in all_scores) / total,
            avg_coverage_score=sum(s.coverage_score for _, s in all_scores) / total,
            avg_citation_score=sum(s.citation_score for _, s in all_scores) / total,
            avg_edit_distance_score=sum(s.edit_distance_score for _, s in all_scores) / total,
            avg_overall_score=sum(s.overall_score for _, s in all_scores) / total,
            grade_distribution=grades,
            per_trajectory=per_traj,
            module_aggregates=module_avg,
        )


def _empty_report() -> BatchReport:
    return BatchReport(
        total=0, passed=0, completion_rate=0.0,
        avg_tool_accuracy=0.0, avg_argument_accuracy=0.0,
        avg_unnecessary_steps=0.0, avg_invalid_cycles=0.0,
        avg_step_efficiency=0.0, avg_token_efficiency=0.0,
        avg_reasoning_consistency=0.0, avg_coverage_score=0.0,
        avg_citation_score=0.0, avg_edit_distance_score=0.0,
        avg_overall_score=0.0,
        grade_distribution={}, per_trajectory=[],
    )
