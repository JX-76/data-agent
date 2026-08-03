"""Trajectory Integration — wires TrajectoryRecorder into agent execution paths.

Provides wrappers for:
  - mvp_agent.run() — deterministic pipeline
  - agent_loop.react_loop() — ReAct agent loop
  - dag_agent DAG-based agent

Each wrapper transparently records trajectories during execution,
enabling zero-overhead eval when you don't need trajectories.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Optional

import structlog

from trajectory import TrajectoryRecorder, Trajectory, TrajectoryEvaluator
from trajectory import BatchEvaluator, BatchReport

logger = structlog.get_logger("traj_integration")


# ══════════════════════════════════════════════════════════════
# Agent Wrapper: ReAct Loop
# ══════════════════════════════════════════════════════════════

def run_react_with_trajectory(query: str, use_db: bool = True,
                              max_steps: int = 10) -> tuple[dict, Trajectory]:
    """Run ReAct agent loop with trajectory recording.

    Returns (agent_result, trajectory) — the trajectory is also
    embedded in agent_result["_trajectory"] for convenience.
    """
    from agent_loop import react_loop, _call_agent_llm_raw, _format_observation

    recorder = TrajectoryRecorder()
    recorder.start(query)

    # Run the agent
    result = react_loop(query, use_db=use_db, max_steps=max_steps)

    # Reconstruct trajectory from agent result
    traj = recorder.trajectory
    if traj is None:
        traj = recorder.start(query)

    # Extract steps into tool calls
    for step in result.get("steps", []):
        action = step.get("action", {})
        if isinstance(action, dict) and action.get("action") == "tool":
            tool = action.get("tool", "?")
            args = action.get("args", {})
            obs = step.get("observation", "")
            success = "error" not in (obs or "").lower()
            recorder.record_tool(
                tool, args,
                result_summary=(obs or "")[:120],
                success=success,
                error="" if success else (obs or "").get("error", ""),
            )
        elif isinstance(action, dict) and action.get("action") == "done":
            pass  # Done is not a tool call

    # Determine status
    status = "ok"
    error = ""
    if result.get("error"):
        status = "error"
        error = result["error"]
    elif not result.get("results"):
        status = "blocked"

    recorder.finish(
        status=status,
        sql=result.get("sql", ""),
        results=result.get("results") or [],
        insight=result.get("insight", ""),
        error=error,
    )

    result["_trajectory"] = traj
    return result, traj


# ══════════════════════════════════════════════════════════════
# Agent Wrapper: DAG Agent
# ══════════════════════════════════════════════════════════════

def run_dag_with_trajectory(query: str, use_llm: bool = True,
                            use_db: bool = True) -> tuple[dict, Trajectory]:
    """Run DAG agent with trajectory recording.

    Returns (agent_result, trajectory).
    """
    from dag_agent import run as dag_run

    recorder = TrajectoryRecorder()
    recorder.start(query)

    result = dag_run(query, use_llm=use_llm, use_db=use_db)

    # Extract tool calls from DAG trace
    trace = result.get("trace", [])
    for entry in trace:
        op = entry.get("op", "")
        if op and "(" in op:
            tool_name = op.split("(")[0]
            args_str = op[op.index("(")+1:op.rindex(")")] if ")" in op else ""
            try:
                args = _parse_dag_args(tool_name, args_str)
            except Exception as e:
                logger.warning("bare_exception_caught", error=str(e))
                args = {"raw": args_str}

            recorder.record_tool(
                tool_name, args,
                result_summary=entry.get("status", "ok")[:120],
                success=entry.get("status") != "error",
                error=entry.get("error", ""),
            )

    status = result.get("status", "ok")
    recorder.finish(
        status=status,
        sql=result.get("sql", ""),
        results=result.get("results") or [],
        insight=result.get("insight", ""),
        error=result.get("error", ""),
    )

    result["_trajectory"] = recorder.trajectory
    return result, recorder.trajectory


# ══════════════════════════════════════════════════════════════
# Agent Wrapper: MVP Pipeline (deterministic)
# ══════════════════════════════════════════════════════════════

def run_pipeline_with_trajectory(query: str, use_llm: bool = False,
                                 use_db: bool = True) -> tuple[dict, Trajectory]:
    """Run MVP pipeline with trajectory recording.

    Returns (agent_result, trajectory).
    """
    from mvp_agent import run as pipeline_run

    recorder = TrajectoryRecorder()
    recorder.start(query)

    result = pipeline_run(query, use_llm=use_llm, use_db=use_db)

    # Extract steps from plan execution
    plan = result.get("plan", {})
    recorder.trajectory.intent = plan.get("intent", "")
    recorder.trajectory.model = plan.get("model", "")
    recorder.trajectory.metric = plan.get("metric", "")

    # AgentRuntime.trace contains tool execution records:
    # [{"dataid": "d1", "op": "switch(order_detail)", "parent": null, "columns": [...]}, ...]
    trace = result.get("tools", [])
    for entry in trace:
        op_str = entry.get("op", "")
        if not op_str:
            continue
        # Parse op string like "switch(order_detail)", "filter(time,default_metric_filters)"
        tool_name, tool_args = _parse_op_string(op_str)
        recorder.record_tool(
            tool_name,
            tool_args,
            result_summary=f"dataid={entry.get('dataid', '?')}, cols={len(entry.get('columns', []))}",
            dataid=entry.get("dataid", ""),
            success=True,
        )

    # Determine status
    status = "ok"
    error = ""
    if plan.get("status") == "blocked":
        status = "blocked"
        error = plan.get("block_reason", "")
    elif plan.get("status") == "need_clarification":
        status = "need_clarification"
    elif not result.get("sql"):
        # No SQL generated = likely blocked or clarification
        if plan.get("status") != "ok":
            status = plan.get("status", "error")

    recorder.finish(
        status=status,
        sql=result.get("sql", ""),
        results=result.get("results") or [],
        insight=str(result.get("insight", "")),
        error=error,
    )

    result["_trajectory"] = recorder.trajectory
    return result, recorder.trajectory


# ══════════════════════════════════════════════════════════════
# Trajectory Store (persist/load)
# ══════════════════════════════════════════════════════════════

class TrajectoryStore:
    """Persist and load trajectories for offline evaluation."""

    def __init__(self, store_dir: str = "evals/trajectories"):
        self.dir = Path(store_dir)
        self.dir.mkdir(parents=True, exist_ok=True)

    def save(self, trajectory: Trajectory, label: str = ""):
        """Save a trajectory as JSON."""
        name = label or trajectory.trace_id
        path = self.dir / f"{name}.json"
        path.write_text(json.dumps(trajectory.to_dict(), ensure_ascii=False, indent=2))

    def load_all(self) -> list[Trajectory]:
        """Load all stored trajectories."""
        from trajectory import Trajectory as T, ToolCall as TC

        trajectories = []
        for f in sorted(self.dir.glob("*.json")):
            try:
                data = json.loads(f.read_text())
                traj = T(
                    trace_id=data.get("trace_id", f.stem),
                    query=data.get("query", ""),
                    intent=data.get("intent", ""),
                    model=data.get("model", ""),
                    metric=data.get("metric", ""),
                    status=data.get("status", ""),
                    sql=data.get("sql", ""),
                    results=[{}] * data.get("result_count", 0),
                    insight=data.get("insight", ""),
                    error=data.get("error", ""),
                    step_count=data.get("step_count", 0),
                    retry_count=data.get("retry_count", 0),
                )
                traj.total_latency_ms = data.get("total_latency_ms", 0)
                for tc_data in data.get("tool_calls", []):
                    tc = TC(
                        tool=tc_data.get("tool", "?"),
                        args=tc_data.get("args", {}),
                        result_summary=tc_data.get("result", ""),
                        dataid=tc_data.get("dataid", ""),
                        success=tc_data.get("success", True),
                        error=tc_data.get("error", ""),
                        latency_ms=tc_data.get("latency_ms", 0),
                    )
                    tc.tool_correct = tc_data.get("tool_correct")
                    tc.args_correct = tc_data.get("args_correct")
                    tc.necessary = tc_data.get("necessary")
                    tc.optimal_tool = tc_data.get("optimal_tool")
                    traj.tool_calls.append(tc)
                trajectories.append(traj)
            except Exception as e:
                logger.warning("load_traj_failed", file=str(f), error=str(e))
        return trajectories

    def load(self, label: str) -> Optional[Trajectory]:
        """Load a single trajectory by label."""
        path = self.dir / f"{label}.json"
        if not path.exists():
            return None
        # Reuse load_all for consistency
        data = json.loads(path.read_text())
        from trajectory import Trajectory as T, ToolCall as TC
        traj = T(
            trace_id=data.get("trace_id", label),
            query=data.get("query", ""),
            intent=data.get("intent", ""),
            model=data.get("model", ""),
            metric=data.get("metric", ""),
            status=data.get("status", ""),
            sql=data.get("sql", ""),
            results=[{}] * data.get("result_count", 0),
            insight=data.get("insight", ""),
            error=data.get("error", ""),
            step_count=data.get("step_count", 0),
            retry_count=data.get("retry_count", 0),
        )
        traj.total_latency_ms = data.get("total_latency_ms", 0)
        for tc_data in data.get("tool_calls", []):
            tc = TC(
                tool=tc_data.get("tool", "?"),
                args=tc_data.get("args", {}),
                result_summary=tc_data.get("result", ""),
                dataid=tc_data.get("dataid", ""),
                success=tc_data.get("success", True),
                error=tc_data.get("error", ""),
                latency_ms=tc_data.get("latency_ms", 0),
            )
            tc.tool_correct = tc_data.get("tool_correct")
            tc.args_correct = tc_data.get("args_correct")
            tc.necessary = tc_data.get("necessary")
            tc.optimal_tool = tc_data.get("optimal_tool")
            traj.tool_calls.append(tc)
        return traj


# ══════════════════════════════════════════════════════════════
# Offline Eval: from stored trajectories (no agent run needed)
# ══════════════════════════════════════════════════════════════

def evaluate_stored_trajectories(store_dir: str = "evals/trajectories",
                                 use_llm_judge: bool = False) -> BatchReport:
    """Evaluate all stored trajectories without running the agent.

    Mode 1: Rule-based (fast, deterministic, always available)
    Mode 2: LLM judge (requires API key and calibration)

    Returns BatchReport with aggregate metrics.
    """
    store = TrajectoryStore(store_dir)
    trajectories = store.load_all()

    if not trajectories:
        logger.warning("no_trajectories_found", dir=store_dir)
        return BatchReport(
            total=0, passed=0, completion_rate=0.0,
            avg_tool_accuracy=0.0, avg_argument_accuracy=0.0,
            avg_unnecessary_steps=0.0, avg_invalid_cycles=0.0,
            avg_step_efficiency=0.0, avg_overall_score=0.0,
            grade_distribution={}, per_trajectory=[],
        )

    # Rule-based evaluation
    evaluator = TrajectoryEvaluator()
    batch = BatchEvaluator(evaluator)
    report = batch.evaluate_batch(trajectories)

    # Optionally run LLM judge (produces additional scores)
    if use_llm_judge:
        from llm_judge import LLMJudge
        judge = LLMJudge()
        for i, traj in enumerate(trajectories):
            try:
                llm_scores = judge.evaluate(traj.to_dict())
                report.per_trajectory[i]["llm_scores"] = llm_scores
                report.per_trajectory[i]["llm_overall"] = llm_scores.get("overall")
            except Exception as e:
                logger.warning("llm_judge_failed", trace_id=traj.trace_id, error=str(e))
                report.per_trajectory[i]["llm_error"] = str(e)

    # Save evaluated trajectories back
    for traj in trajectories:
        store.save(traj)  # Now includes scores from evaluation

    return report


# ══════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════

def _parse_dag_args(tool: str, args_str: str) -> dict:
    """Parse DAG trace op strings like 'switch(order_detail)' into dict."""
    args_str = args_str.strip()
    if not args_str:
        return {}

    if tool == "switch":
        return {"model_id": args_str}
    if tool in ("filter_value",):
        parts = args_str.split(",")
        if len(parts) >= 2:
            return {"dataid": parts[0].strip(), "dimension": parts[1].strip(),
                    "value": ",".join(parts[2:]).strip()}
        return {"raw": args_str}
    if tool in ("filter", "aggregate"):
        parts = args_str.split(",")
        result = {}
        for part in parts:
            kv = part.split("=", 1)
            if len(kv) == 2:
                result[kv[0].strip()] = kv[1].strip()
            else:
                if "dataid" not in result:
                    result["dataid"] = part.strip()
        return result

    return {"raw": args_str}


def _parse_op_string(op_str: str) -> tuple[str, dict]:
    """Parse op string like 'switch(order_detail)' or 'filter(time,default_metric_filters)'.

    Returns (tool_name, args_dict).
    """
    if "(" not in op_str:
        return op_str, {}

    tool_name = op_str[:op_str.index("(")].strip()
    args_str = op_str[op_str.index("(") + 1:op_str.rindex(")")].strip() if op_str.endswith(")") else ""

    args = {}
    if tool_name == "switch":
        args = {"model_id": args_str.split(",")[0].strip()}
    elif tool_name == "filter":
        args = {"filters": [f.strip() for f in args_str.split(",")]}
    elif tool_name == "aggregate":
        args = {"metric_id": args_str.split(",")[0].strip()}
    elif tool_name == "sort":
        parts = [p.strip() for p in args_str.split(",")]
        args = {"by": parts[0] if parts else "", "order": parts[1] if len(parts) > 1 else "DESC"}
    elif tool_name == "top":
        parts = [p.strip() for p in args_str.split(",")]
        args = {"by": parts[0] if parts else "", "n": int(parts[1]) if len(parts) > 1 else 5,
                "order": parts[2] if len(parts) > 2 else "DESC"}
    elif tool_name == "merge":
        parts = [p.strip() for p in args_str.split(",")]
        args = {"dataid_a": parts[0] if parts else "",
                "dataid_b": parts[1] if len(parts) > 1 else ""}
        for p in parts:
            if "=" in p:
                k, v = p.split("=", 1)
                args[k.strip()] = v.strip()
    elif tool_name == "filter_value":
        for p in args_str.split(","):
            p = p.strip()
            if "=" in p:
                k, v = p.split("=", 1)
                args[k.strip()] = v.strip()
            elif "!=" in p:
                k, v = p.split("!=", 1)
                args[k.strip()] = "!" + v.strip()
    elif tool_name == "compare_periods":
        args = {"metric_id": args_str.split(",")[0].strip()}
    elif tool_name == "preview":
        parts = [p.strip() for p in args_str.split(",")]
        args = {"dataid": parts[0] if parts else "", "n": int(parts[1]) if len(parts) > 1 else 5}
    elif tool_name == "compare_periods.p1_filter":
        return "filter", {"scope": "period1"}
    elif tool_name == "compare_periods.p2_filter":
        return "filter", {"scope": "period2"}
    elif tool_name == "compare_periods.p1_agg":
        return "aggregate", {"scope": "period1"}
    elif tool_name == "compare_periods.p2_agg":
        return "aggregate", {"scope": "period2"}
    else:
        args = {"raw": args_str}

    return tool_name, args
