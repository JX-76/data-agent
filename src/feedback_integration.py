"""Feedback Integration: Human feedback integration with DAG.

Integrates HumanFeedbackNode with the DAG execution.
"""

from __future__ import annotations

from typing import Any

from human_feedback import HumanFeedbackNode, HumanFeedback


class FeedbackIntegration:
    """Integrates human feedback with DAG execution."""
    
    def __init__(self):
        self.feedback_node = HumanFeedbackNode()
    
    def request_feedback(self, step: int, node: str, content: str, context: dict[str, Any] | None = None) -> str:
        """Request feedback from human.
        
        Args:
            step: Step number
            node: Node name
            content: Content to review
            context: Optional context
        
        Returns:
            Feedback request ID
        """
        return self.feedback_node.request_feedback(step, node, content, context)
    
    def submit_feedback(self, request_id: str, action: str, comment: str = "", modified_content: str | None = None) -> HumanFeedback:
        """Submit feedback from human.
        
        Args:
            request_id: Feedback request ID
            action: Feedback action
            comment: Optional comment
            modified_content: Optional modified content
        
        Returns:
            Human feedback
        """
        return self.feedback_node.submit_feedback(request_id, action, comment, modified_content)
    
    def get_feedback(self, request_id: str) -> HumanFeedback | None:
        """Get feedback result.
        
        Args:
            request_id: Feedback request ID
        
        Returns:
            Human feedback or None
        """
        return self.feedback_node.get_feedback(request_id)
    
    def is_pending(self, request_id: str) -> bool:
        """Check if feedback is pending.
        
        Args:
            request_id: Feedback request ID
        
        Returns:
            True if pending
        """
        return self.feedback_node.is_pending(request_id)
    
    def get_pending_requests(self) -> dict:
        """Get all pending feedback requests.
        
        Returns:
            Dictionary of pending requests
        """
        return self.feedback_node.get_pending_requests()


def create_feedback_integration() -> FeedbackIntegration:
    """Convenience function to create feedback integration.
    
    Returns:
        Feedback integration
    """
    return FeedbackIntegration()
