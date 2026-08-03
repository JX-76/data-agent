"""Reward steps: Step-by-step reward calculation.

Calculates rewards for each step in the execution:
1. Instruction correctness
2. Tool selection reasonableness
3. Parameter sufficiency
4. Execution success
5. Output quality
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger("reward_steps")


@dataclass
class StepReward:
    """Reward for a single step."""
    step: int
    instruction_correctness: float = 0.0
    tool_selection: float = 0.0
    parameter_sufficiency: float = 0.0
    execution_success: float = 0.0
    output_quality: float = 0.0
    total: float = 0.0
    
    def calculate_total(self) -> float:
        """Calculate total reward."""
        weights = {
            "instruction_correctness": 0.2,
            "tool_selection": 0.2,
            "parameter_sufficiency": 0.2,
            "execution_success": 0.2,
            "output_quality": 0.2,
        }
        
        self.total = (
            self.instruction_correctness * weights["instruction_correctness"] +
            self.tool_selection * weights["tool_selection"] +
            self.parameter_sufficiency * weights["parameter_sufficiency"] +
            self.execution_success * weights["execution_success"] +
            self.output_quality * weights["output_quality"]
        )
        
        return self.total


@dataclass
class ExecutionReward:
    """Reward for entire execution."""
    step_rewards: list[StepReward] = field(default_factory=list)
    overall_reward: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)
    
    def calculate_overall(self) -> float:
        """Calculate overall reward."""
        if not self.step_rewards:
            self.overall_reward = 0.0
            return 0.0
        
        self.overall_reward = sum(s.total for s in self.step_rewards) / len(self.step_rewards)
        return self.overall_reward


class RewardCalculator:
    """Calculates rewards for execution steps."""
    
    def __init__(self):
        self._step_rewards: list[StepReward] = []
    
    def calculate_step_reward(self, step: int, plan: dict[str, Any], result: Any, expected: Any | None = None) -> StepReward:
        """Calculate reward for a step.
        
        Args:
            step: Step number
            plan: Execution plan
            result: Execution result
            expected: Expected result
        
        Returns:
            Step reward
        """
        reward = StepReward(step=step)
        
        # Check instruction correctness
        reward.instruction_correctness = self._check_instruction_correctness(plan)
        
        # Check tool selection
        reward.tool_selection = self._check_tool_selection(plan)
        
        # Check parameter sufficiency
        reward.parameter_sufficiency = self._check_parameter_sufficiency(plan)
        
        # Check execution success
        reward.execution_success = self._check_execution_success(result)
        
        # Check output quality
        reward.output_quality = self._check_output_quality(result, expected)
        
        # Calculate total
        reward.calculate_total()
        
        self._step_rewards.append(reward)
        
        logger.info("step_reward_calculated",
            step=step,
            total=reward.total,
        )
        
        return reward
    
    def calculate_overall_reward(self) -> ExecutionReward:
        """Calculate overall reward.
        
        Returns:
            Execution reward
        """
        execution_reward = ExecutionReward(step_rewards=self._step_rewards)
        execution_reward.calculate_overall()
        
        logger.info("overall_reward_calculated",
            overall=execution_reward.overall_reward,
            steps=len(self._step_rewards),
        )
        
        return execution_reward
    
    def _check_instruction_correctness(self, plan: dict[str, Any]) -> float:
        """Check if instruction is correct."""
        # Check if plan has required fields
        if not plan or "tool_to_use" not in plan:
            return 0.0
        
        # Check if tool is valid
        valid_tools = {
            "SQL_GENERATE_NODE",
            "PYTHONGENERATE_NODE",
            "REPORT_GENERATOR_NODE",
            "SCHEMA_RECALL_NODE",
            "EVIDENCE_RECALL_NODE",
            "SEMANTIC_CONSISTENCY_NODE",
        }
        
        if plan.get("tool_to_use") in valid_tools:
            return 1.0
        
        return 0.5
    
    def _check_tool_selection(self, plan: dict[str, Any]) -> float:
        """Check if tool selection is reasonable."""
        # Check if tool matches intent
        tool = plan.get("tool_to_use", "")
        instruction = plan.get("tool_parameters", {}).get("instruction", "")
        
        if "SQL" in tool and ("查询" in instruction or "select" in instruction.lower()):
            return 1.0
        
        if "PYTHON" in tool and ("计算" in instruction or "分析" in instruction):
            return 1.0
        
        if "REPORT" in tool and ("报告" in instruction or "总结" in instruction):
            return 1.0
        
        return 0.5
    
    def _check_parameter_sufficiency(self, plan: dict[str, Any]) -> float:
        """Check if parameters are sufficient."""
        params = plan.get("tool_parameters", {})
        
        if not params:
            return 0.0
        
        # Check if instruction is present
        if "instruction" in params and params["instruction"]:
            return 1.0
        
        return 0.5
    
    def _check_execution_success(self, result: Any) -> float:
        """Check if execution was successful."""
        if isinstance(result, dict):
            if result.get("status") == "success":
                return 1.0
            elif result.get("status") == "error":
                return 0.0
        
        return 0.5
    
    def _check_output_quality(self, result: Any, expected: Any | None = None) -> float:
        """Check output quality."""
        if expected is None:
            # No expected result, assume medium quality
            return 0.5
        
        # Compare result with expected
        if result == expected:
            return 1.0
        
        return 0.5


def calculate_step_reward(step: int, plan: dict[str, Any], result: Any, expected: Any | None = None) -> StepReward:
    """Convenience function to calculate step reward.
    
    Args:
        step: Step number
        plan: Execution plan
        result: Execution result
        expected: Expected result
    
    Returns:
        Step reward
    """
    calculator = RewardCalculator()
    return calculator.calculate_step_reward(step, plan, result, expected)
