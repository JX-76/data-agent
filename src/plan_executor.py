"""Plan-then-Execute: Dynamic plan generation and execution.

Core concept:
1. PlannerNode: LLM generates a structured Plan (list of ExecutionStep)
2. PlanExecutorNode: Executes steps sequentially, maintaining state
3. Each step can be: SQL_GENERATE, PYTHON_GENERATE, REPORT_GENERATE, etc.
4. Plan can be repaired if a step fails (up to PLAN_REPAIR_COUNT)

Plan structure:
{
    "thought_process": "...",
    "execution_plan": [
        {
            "step": 1,
            "tool_to_use": "SQL_GENERATE_NODE",
            "tool_parameters": {"instruction": "..."},
            "depends_on": []
        },
        {
            "step": 2,
            "tool_to_use": "PYTHON_GENERATE_NODE",
            "tool_parameters": {"instruction": "..."},
            "depends_on": [1]
        }
    ]
}
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional
from enum import Enum


class ToolType(Enum):
    """Available tool types for plan execution."""
    SQL_GENERATE = "SQL_GENERATE_NODE"
    PYTHON_GENERATE = "PYTHONGENERATE_NODE"
    REPORT_GENERATE = "REPORT_GENERATOR_NODE"
    SCHEMA_RECALL = "SCHEMA_RECALL_NODE"
    EVIDENCE_RECALL = "EVIDENCE_RECALL_NODE"
    SEMANTIC_CHECK = "SEMANTIC_CONSISTENCY_NODE"


@dataclass
class ExecutionStep:
    """A single step in the execution plan."""
    step: int
    tool_to_use: str
    tool_parameters: dict[str, Any] = field(default_factory=dict)
    depends_on: list[int] = field(default_factory=list)
    status: str = "pending"  # pending, running, success, failed
    result: Any = None
    error: str | None = None
    retry_count: int = 0


@dataclass
class Plan:
    """A structured execution plan."""
    thought_process: str
    execution_plan: list[ExecutionStep]
    status: str = "pending"  # pending, running, success, failed
    current_step: int = 0
    repair_count: int = 0


# ── Constants ──

PLAN_REPAIR_COUNT = 3  # Max plan repairs before termination
STEP_RETRY_COUNT = 2   # Max retries per step


# ── Plan Parser ──

def parse_plan(raw_plan: str | dict) -> Plan:
    """Parse a raw plan into a structured Plan object.
    
    Args:
        raw_plan: JSON string or dict from LLM
    
    Returns:
        Structured Plan object
    """
    if isinstance(raw_plan, str):
        data = json.loads(raw_plan)
    else:
        data = raw_plan
    
    steps = []
    for step_data in data.get("execution_plan", []):
        steps.append(ExecutionStep(
            step=step_data["step"],
            tool_to_use=step_data["tool_to_use"],
            tool_parameters=step_data.get("tool_parameters", {}),
            depends_on=step_data.get("depends_on", []),
        ))
    
    return Plan(
        thought_process=data.get("thought_process", ""),
        execution_plan=steps,
    )


def validate_plan(plan: Plan) -> tuple[bool, str]:
    """Validate a plan for structural correctness.
    
    Returns:
        (is_valid, error_message)
    """
    if not plan.execution_plan:
        return False, "Empty execution plan"
    
    # Check step numbers are sequential
    step_nums = [s.step for s in plan.execution_plan]
    if step_nums != list(range(1, len(step_nums) + 1)):
        return False, "Step numbers must be sequential starting from 1"
    
    # Check dependencies exist
    for step in plan.execution_plan:
        for dep in step.depends_on:
            if dep not in step_nums:
                return False, f"Step {step.step} depends on non-existent step {dep}"
    
    # Check for circular dependencies
    visited = set()
    def has_cycle(step_num: int, path: set) -> bool:
        if step_num in path:
            return True
        if step_num in visited:
            return False
        path.add(step_num)
        step = next((s for s in plan.execution_plan if s.step == step_num), None)
        if step:
            for dep in step.depends_on:
                if has_cycle(dep, path):
                    return True
        path.remove(step_num)
        visited.add(step_num)
        return False
    
    for step in plan.execution_plan:
        if has_cycle(step.step, set()):
            return False, f"Circular dependency detected at step {step.step}"
    
    return True, ""


# ── Plan Executor ──

class PlanExecutor:
    """Executes a plan step by step, handling retries and repairs."""
    
    def __init__(self, tool_registry: dict[str, callable] | None = None):
        self.tool_registry = tool_registry or {}
        self.state: dict[str, Any] = {}
    
    def register_tool(self, name: str, func: callable) -> None:
        """Register a tool for execution."""
        self.tool_registry[name] = func
    
    def execute_plan(self, plan: Plan) -> dict[str, Any]:
        """Execute a plan and return final results.
        
        Args:
            plan: The plan to execute
        
        Returns:
            Execution results including all step outputs
        """
        plan.status = "running"
        
        while plan.current_step < len(plan.execution_plan):
            step = plan.execution_plan[plan.current_step]
            
            # Check dependencies
            if not self._check_dependencies(step, plan):
                plan.status = "failed"
                return {"status": "failed", "reason": "dependency_failed", "step": step.step}
            
            # Execute step
            success = self._execute_step(step)
            
            if not success:
                # Try repair
                if plan.repair_count < PLAN_REPAIR_COUNT:
                    plan.repair_count += 1
                    self._repair_step(step, plan)
                    continue  # Retry current step
                else:
                    plan.status = "failed"
                    return {
                        "status": "failed",
                        "reason": "max_repairs_reached",
                        "step": step.step,
                        "error": step.error,
                    }
            
            plan.current_step += 1
        
        plan.status = "success"
        return {
            "status": "success",
            "steps": [
                {
                    "step": s.step,
                    "tool": s.tool_to_use,
                    "status": s.status,
                    "result": s.result,
                }
                for s in plan.execution_plan
            ],
            "final_state": self.state,
        }
    
    def _check_dependencies(self, step: ExecutionStep, plan: Plan) -> bool:
        """Check if all dependencies are satisfied."""
        for dep_num in step.depends_on:
            dep_step = next((s for s in plan.execution_plan if s.step == dep_num), None)
            if not dep_step or dep_step.status != "success":
                return False
        return True
    
    def _execute_step(self, step: ExecutionStep) -> bool:
        """Execute a single step.
        
        Returns:
            True if successful, False otherwise
        """
        step.status = "running"
        tool_name = step.tool_to_use
        
        if tool_name not in self.tool_registry:
            step.status = "failed"
            step.error = f"Unknown tool: {tool_name}"
            return False
        
        try:
            # Inject state into parameters
            params = {**step.tool_parameters, "_state": self.state}
            result = self.tool_registry[tool_name](**params)
            step.result = result
            step.status = "success"
            
            # Update state with result
            self.state[f"step_{step.step}_result"] = result
            
            return True
        except Exception as e:
            step.status = "failed"
            step.error = str(e)
            step.retry_count += 1
            
            if step.retry_count < STEP_RETRY_COUNT:
                return self._execute_step(step)  # Retry
            
            return False
    
    def _repair_step(self, step: ExecutionStep, plan: Plan) -> None:
        """Repair a failed step by modifying its parameters.
        
        In a real implementation, this would call the Planner to regenerate
        the step with error context.
        """
        # Add error context to parameters
        step.tool_parameters["_error_context"] = step.error
        step.tool_parameters["_previous_attempts"] = step.retry_count
        step.retry_count = 0  # Reset retry count for repair
        step.error = None


# ── Planner Node ──

def generate_plan(query: str, context: dict[str, Any] | None = None) -> Plan:
    """Generate a plan from a user query.
    
    In production, this would call an LLM with a structured prompt.
    For now, returns a simple plan based on query intent.
    
    Args:
        query: User query
        context: Additional context
    
    Returns:
        Generated plan
    """
    # Simple rule-based planning for demonstration
    plan_data = {
        "thought_process": f"分析查询: {query}",
        "execution_plan": [
            {
                "step": 1,
                "tool_to_use": "SQL_GENERATE_NODE",
                "tool_parameters": {"instruction": query},
                "depends_on": [],
            },
            {
                "step": 2,
                "tool_to_use": "SEMANTIC_CONSISTENCY_NODE",
                "tool_parameters": {"check": "sql_validity"},
                "depends_on": [1],
            },
            {
                "step": 3,
                "tool_to_use": "REPORT_GENERATOR_NODE",
                "tool_parameters": {"format": "summary"},
                "depends_on": [2],
            },
        ],
    }
    
    return parse_plan(plan_data)


# ── Integration with existing DAG ──

def plan_then_execute(query: str, tool_registry: dict[str, callable]) -> dict[str, Any]:
    """High-level function: Plan then Execute.
    
    Args:
        query: User query
        tool_registry: Available tools
    
    Returns:
        Execution results
    """
    # Generate plan
    plan = generate_plan(query)
    
    # Validate plan
    is_valid, error = validate_plan(plan)
    if not is_valid:
        return {"status": "failed", "reason": "invalid_plan", "error": error}
    
    # Execute plan
    executor = PlanExecutor(tool_registry)
    return executor.execute_plan(plan)
