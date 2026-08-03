"""HumanFeedbackNode: Human feedback integration.

Provides a mechanism for human feedback in the DAG execution:
1. Interrupt execution for human review
2. Collect feedback (approve, reject, modify)
3. Resume execution with feedback
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger("human_feedback")


@dataclass
class HumanFeedback:
    """Human feedback on a step."""
    action: str  # approve, reject, modify, clarify
    comment: str = ""
    modified_content: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class FeedbackRequest:
    """Request for human feedback."""
    step: int
    node: str
    content: str
    context: dict[str, Any] = field(default_factory=dict)
    options: list[str] = field(default_factory=list)


class HumanFeedbackNode:
    """Node for human feedback in DAG execution."""
    
    def __init__(self):
        self._pending_feedback: dict[str, FeedbackRequest] = {}
        self._feedback_results: dict[str, HumanFeedback] = {}
        self._counter = 0
    
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
        self._counter += 1
        request_id = f"feedback-{self._counter}"
        
        request = FeedbackRequest(
            step=step,
            node=node,
            content=content,
            context=context or {},
            options=["approve", "reject", "modify", "clarify"],
        )
        
        self._pending_feedback[request_id] = request
        
        logger.info("feedback_requested", 
            request_id=request_id,
            step=step,
            node=node,
        )
        
        return request_id
    
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
        if request_id not in self._pending_feedback:
            raise ValueError(f"Feedback request {request_id} not found")
        
        feedback = HumanFeedback(
            action=action,
            comment=comment,
            modified_content=modified_content,
        )
        
        self._feedback_results[request_id] = feedback
        del self._pending_feedback[request_id]
        
        logger.info("feedback_submitted",
            request_id=request_id,
            action=action,
        )
        
        return feedback
    
    def get_feedback(self, request_id: str) -> HumanFeedback | None:
        """Get feedback result.
        
        Args:
            request_id: Feedback request ID
        
        Returns:
            Human feedback or None
        """
        return self._feedback_results.get(request_id)
    
    def is_pending(self, request_id: str) -> bool:
        """Check if feedback is pending.
        
        Args:
            request_id: Feedback request ID
        
        Returns:
            True if pending
        """
        return request_id in self._pending_feedback
    
    def get_pending_requests(self) -> dict[str, FeedbackRequest]:
        """Get all pending feedback requests.
        
        Returns:
            Dictionary of pending requests
        """
        return self._pending_feedback.copy()
    
    def process_with_feedback(self, step: int, node: str, content: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        """Process a step with human feedback.
        
        Args:
            step: Step number
            node: Node name
            content: Content to review
            context: Optional context
        
        Returns:
            Processing result
        """
        request_id = self.request_feedback(step, node, content, context)
        
        return {
            "status": "waiting_feedback",
            "request_id": request_id,
            "step": step,
            "node": node,
            "content": content,
        }


def create_human_feedback_node() -> HumanFeedbackNode:
    """Convenience function to create human feedback node.
    
    Returns:
        Human feedback node
    """
    return HumanFeedbackNode()
