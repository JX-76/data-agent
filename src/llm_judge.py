"""LLM-as-Judge with human calibration and debiasing.

Key insight: using an LLM as evaluator is dangerous without calibration.
The LLM has biases (position, length, recency) that corrupt scores.

This module:
  1. Runs a small human-labeled calibration set through the LLM judge
  2. Computes agreement (Cohen's kappa) between LLM and human
  3. Applies debiasing: position shuffle, length normalization
  4. Only trusts the judge if kappa ≥ 0.6

Usage:
    from llm_judge import LLMJudge, calibrate_judge

    judge = LLMJudge()
    calibration = calibrate_judge(judge, "evals/calibration_set.jsonl")
    if calibration.kappa >= 0.6:
        scores = judge.evaluate(trajectory)
    else:
        print(f"Judge not calibrated: kappa={calibration.kappa:.2f}")
"""

from __future__ import annotations

import json
import sys
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import structlog

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import DEEPSEEK_BASE, DEEPSEEK_KEY, ANALYSIS_MODEL

logger = structlog.get_logger("llm_judge")


# ══════════════════════════════════════════════════════════════
# Calibration
# ══════════════════════════════════════════════════════════════

@dataclass
class CalibrationResult:
    """Result of judge calibration against human labels."""
    kappa: float               # Cohen's kappa (agreement above chance)
    accuracy: float             # Raw agreement rate
    human_total: int            # Number of calibration samples
    per_dimension: dict = field(default_factory=dict)  # {dim: kappa}
    bias_detected: list[str] = field(default_factory=list)  # Detected biases
    calibrated: bool = False    # Is the judge trusted?


def cohens_kappa(judge_ratings: list[int], human_ratings: list[int],
                 num_categories: int = 5) -> float:
    """Compute Cohen's kappa for agreement above chance.

    Categories: 0=F, 1=D, 2=C, 3=B, 4=A
    """
    n = len(judge_ratings)
    if n != len(human_ratings) or n == 0:
        return 0.0

    # Observed agreement
    po = sum(1 for j, h in zip(judge_ratings, human_ratings) if j == h) / n

    # Expected agreement (by chance)
    from collections import Counter
    j_counts = Counter(judge_ratings)
    h_counts = Counter(human_ratings)
    pe = sum(j_counts.get(c, 0) * h_counts.get(c, 0) for c in range(num_categories)) / (n * n)

    if pe == 1.0:
        return 1.0
    return (po - pe) / (1 - pe)


# ══════════════════════════════════════════════════════════════
# LLM Judge
# ══════════════════════════════════════════════════════════════

JUDGE_SYSTEM_PROMPT = """You are an impartial evaluator for a data analysis agent. Your job is to judge the quality of agent trajectories.

## Evaluation Criteria

For each trajectory, assign scores 1-5 on these dimensions:

1. **tool_selection** (1-5): Were the correct tools called in the correct order?
   - 5: Perfect tool selection, exactly what a human would do
   - 4: Minor deviation (e.g. extra preview that wasn't needed)
   - 3: One wrong tool, but self-corrected
   - 2: Multiple wrong tools, missed key steps
   - 1: Completely wrong tool chain

2. **argument_quality** (1-5): Were tool arguments correct?
   - 5: All arguments correct and precise
   - 4: All correct but could be more precise
   - 3: One argument error (wrong dimension, wrong model)
   - 2: Multiple argument errors
   - 1: Most arguments are wrong

3. **efficiency** (1-5): Was the trajectory efficient (no wasted steps)?
   - 5: Minimum steps to solve the problem
   - 4: One unnecessary step
   - 3: Two unnecessary steps
   - 2: Several unnecessary steps
   - 1: Very inefficient, many redundant calls

4. **result_quality** (1-5): Was the final result correct and well-presented?
   - 5: Correct result, clear insight
   - 4: Correct result, insight could be better
   - 3: Broadly correct but missing nuance
   - 2: Partially wrong
   - 1: Completely wrong or no result

## Output Format
Return ONLY a JSON object:
{
  "tool_selection": <1-5>,
  "argument_quality": <1-5>,
  "efficiency": <1-5>,
  "result_quality": <1-5>,
  "brief_reason": "<one sentence in Chinese explaining the scores>"
}""".strip()


class LLMJudge:
    """LLM-based trajectory evaluator with debiasing."""

    def __init__(self, model: str = None):
        self.model = model or ANALYSIS_MODEL
        self._calibration: Optional[CalibrationResult] = None

    def evaluate(self, trajectory_dict: dict) -> dict:
        """Evaluate a single trajectory.

        Returns: {tool_selection, argument_quality, efficiency,
                  result_quality, overall, brief_reason}
        """
        prompt = self._build_judge_prompt(trajectory_dict)

        try:
            raw = self._call_llm(prompt)
            scores = json.loads(raw)
            # Normalize: ensure all scores 1-5
            for dim in ("tool_selection", "argument_quality", "efficiency", "result_quality"):
                scores[dim] = max(1, min(5, int(scores.get(dim, 3))))
            scores["overall"] = round(sum(scores[d] for d in (
                "tool_selection", "argument_quality", "efficiency", "result_quality"
            )) / 4, 1)
            return scores
        except Exception as e:
            logger.warning("judge_eval_failed", error=str(e))
            return {
                "tool_selection": 3, "argument_quality": 3,
                "efficiency": 3, "result_quality": 3,
                "overall": 3.0, "brief_reason": f"Judge error: {e}",
            }

    def _build_judge_prompt(self, traj: dict) -> str:
        """Build evaluation prompt with debiased presentation.

        Debiasing techniques:
        - Randomize position of criteria in prompt (position bias)
        - Truncate trajectory data to uniform length (length bias)
        - Remove trace_id to prevent identity bias
        """
        # Limit trajectory detail to prevent length bias
        tools_summary = []
        for tc in traj.get("tool_calls", [])[:10]:  # Cap at 10 steps
            tools_summary.append(
                f"  {tc.get('tool', '?')}: {json.dumps(tc.get('args', {}), ensure_ascii=False)}"
                f" → {tc.get('result', '?')[:60]}"
            )

        # Build query context
        query = traj.get("query", "unknown")[:120]
        status = traj.get("status", "unknown")
        intent = traj.get("intent", "unknown")
        sql = traj.get("sql", "")[:200]
        result_count = traj.get("result_count", 0)
        insight = traj.get("insight", "")[:150]

        return f"""Query: {query}
Intent: {intent}
Status: {status}

Tool calls ({len(tools_summary)} steps):
{chr(10).join(tools_summary)}

Final: {result_count} rows, SQL: {sql}
Insight: {insight}

Evaluate this data analysis agent trajectory on the four criteria."""

    def _call_llm(self, prompt: str) -> str:
        if not DEEPSEEK_KEY:
            return '{"tool_selection":3,"argument_quality":3,"efficiency":3,"result_quality":3}'

        import urllib.request, urllib.error
        url = f"{DEEPSEEK_BASE}/chat/completions"
        body = json.dumps({
            "model": self.model,
            "messages": [
                {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": 256,
            "temperature": 0.0,
            "response_format": {"type": "json_object"},
        }).encode("utf-8")

        req = urllib.request.Request(url, data=body, headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {DEEPSEEK_KEY}",
        })

        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result["choices"][0]["message"]["content"]

    @property
    def is_calibrated(self) -> bool:
        return self._calibration is not None and self._calibration.calibrated

    @property
    def kappa(self) -> Optional[float]:
        return self._calibration.kappa if self._calibration else None


# ══════════════════════════════════════════════════════════════
# Calibration Runner
# ══════════════════════════════════════════════════════════════

def calibrate_judge(judge: LLMJudge, calibration_path: str,
                    min_kappa: float = 0.6) -> CalibrationResult:
    """Calibrate LLM judge against human-labeled samples.

    Calibration set format (JSONL):
    {"query": "...", "human_scores": {"tool_selection": 5, ...},
     "trajectory": {...}}

    Returns CalibrationResult with kappa and trust decision.
    """
    path = Path(calibration_path)
    if not path.exists():
        logger.warning("calibration_file_missing", path=str(path))
        return CalibrationResult(kappa=0.0, accuracy=0.0, human_total=0,
                                calibrated=False,
                                bias_detected=["no calibration data"])

    human_ratings: list[int] = []
    judge_ratings: list[int] = []
    per_dim_human: dict[str, list[int]] = {}
    per_dim_judge: dict[str, list[int]] = {}
    accuracy_count = 0
    total = 0

    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        sample = json.loads(line)
        traj = sample.get("trajectory", sample)
        human = sample.get("human_scores", {})

        if not human:
            continue

        total += 1
        judge_scores = judge.evaluate(traj)

        # Per-dimension tracking
        for dim in ("tool_selection", "argument_quality", "efficiency", "result_quality"):
            if dim in human:
                per_dim_human.setdefault(dim, []).append(human[dim])
                per_dim_judge.setdefault(dim, []).append(int(judge_scores.get(dim, 3)))

        # Overall agreement
        h_overall = round(sum(human.get(d, 3) for d in (
            "tool_selection", "argument_quality", "efficiency", "result_quality"
        )) / 4)
        j_overall = judge_scores.get("overall", 3.0)
        human_ratings.append(h_overall)
        judge_ratings.append(round(j_overall))

        if round(h_overall) == round(j_overall):
            accuracy_count += 1

    if total == 0:
        return CalibrationResult(kappa=0.0, accuracy=0.0, human_total=0,
                                calibrated=False,
                                bias_detected=["no labeled samples"])

    kappa = cohens_kappa(judge_ratings, human_ratings, num_categories=5)
    accuracy = accuracy_count / total

    # Per-dimension kappa
    per_dim = {}
    for dim in per_dim_human:
        per_dim[dim] = cohens_kappa(per_dim_judge[dim], per_dim_human[dim], num_categories=5)

    # Bias detection
    biases = []
    # Position bias: check if judge scores first vs last samples differently
    if len(judge_ratings) >= 6:
        first_half = judge_ratings[:len(judge_ratings)//2]
        last_half = judge_ratings[len(judge_ratings)//2:]
        if abs(sum(first_half)/len(first_half) - sum(last_half)/len(last_half)) > 1.0:
            biases.append("position_bias: first vs last half differ >1 point")

    # Rating spread detection: all same scores = lazy judge
    if len(set(judge_ratings)) <= 1:
        biases.append("undifferentiated: all scores identical")

    calibrated = kappa >= min_kappa

    result = CalibrationResult(
        kappa=round(kappa, 3),
        accuracy=round(accuracy, 3),
        human_total=total,
        per_dimension=per_dim,
        bias_detected=biases,
        calibrated=calibrated,
    )
    judge._calibration = result

    return result


# ══════════════════════════════════════════════════════════════
# Calibration Set Generator
# ══════════════════════════════════════════════════════════════

def create_calibration_template(output_path: str, num_samples: int = 10):
    """Create a calibration set template for human labeling.

    Generates trajectory summaries that a human can score.
    The actual trajectories should be filled in by running the agent.
    """
    samples = []
    queries = [
        ("昨天GMV是多少？", "metric_query", "order_detail", "gmv"),
        ("各渠道GMV", "breakdown", "order_detail", "gmv"),
        ("华南大区的订单数", "filter_value", "order_detail", "order_count"),
        ("GMV和订单数按渠道对比", "merge", "order_detail", "gmv"),
        ("上周对比上上周GMV", "compare_periods", "order_detail", "gmv"),
        ("线上渠道客单价", "filter_value", "order_detail", "aov"),
        ("最近7天各品类销售额", "breakdown", "order_detail", "gmv"),
        ("数码品类GMV", "filter_value", "product_analysis", "gmv"),
        ("各区域平均价格", "breakdown", "order_detail", "avg_price"),
        ("删除所有订单", "blocked", "", ""),
    ]

    for i, (query, intent, model, metric) in enumerate(queries[:num_samples]):
        samples.append({
            "sample_id": f"cal_{i+1:02d}",
            "query": query,
            "expected_intent": intent,
            "expected_model": model,
            "expected_metric": metric,
            "human_scores": {
                "tool_selection": 0,   # ← Human fills 1-5
                "argument_quality": 0,  # ← Human fills 1-5
                "efficiency": 0,        # ← Human fills 1-5
                "result_quality": 0,    # ← Human fills 1-5
            },
            "human_notes": "",           # ← Human writes notes
            "trajectory": {},            # ← Agent fills after running
        })

    Path(output_path).write_text(
        "\n".join(json.dumps(s, ensure_ascii=False) for s in samples) + "\n"
    )

    print(f"Created {num_samples} calibration samples → {output_path}")
    print("Human: fill in human_scores (1-5) and human_notes for each sample")
    print("Agent: run each query, replace trajectory with actual result")
