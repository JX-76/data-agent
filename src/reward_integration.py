"""Reward Integration: Reward calculation integration with DAG.

Integrates RewardCalculator with the DAG execution.
"""

from __future__ import annotations

from typing import Any

from reward_steps import RewardCalculator, StepReward, ExecutionReward


class RewardIntegration:
    """Integrates reward calculation with DAG execution."""
    
    def __init__(self):
        self.calculator = RewardCalculator()
    
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
        return self.calculator.calculate_step_reward(step, plan, result, expected)
    
    def calculate_overall_reward(self) -> ExecutionReward:
        """Calculate overall reward.
        
        Returns:
            Execution reward
        """
        return self.calculator.calculate_overall_reward()
    
    def get_step_rewards(self) -> list[StepReward]:
        """Get all step rewards.
        
        Returns:
            List of step rewards
        """
        return self.calculator._step_rewards


def create_reward_integration() -> RewardIntegration:
    """Convenience function to create reward integration.
    
    Returns:
        Reward integration
    """
    return RewardIntegration()
