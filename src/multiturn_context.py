"""MultiTurnContextManager: Manages multi-turn conversation context.

Handles:
- Context window management (sliding window, summarization)
- Clarification tracking
- Resume after interruption
- Context persistence across turns
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import structlog

logger = structlog.get_logger("multiturn")


@dataclass
class Turn:
    """A single turn in the conversation."""
    turn_id: int
    role: str  # user, assistant, system
    content: str
    timestamp: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ConversationContext:
    """Context for a multi-turn conversation."""
    session_id: str
    turns: list[Turn] = field(default_factory=list)
    current_intent: str | None = None
    current_plan: dict[str, Any] | None = None
    clarification_pending: bool = False
    clarification_question: str | None = None
    clarification_options: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class MultiTurnContextManager:
    """Manages multi-turn conversation context."""
    
    def __init__(self, max_turns: int = 10, summarization_threshold: int = 5):
        self.max_turns = max_turns
        self.summarization_threshold = summarization_threshold
        self._contexts: dict[str, ConversationContext] = {}
    
    def create_session(self, session_id: str) -> ConversationContext:
        """Create a new conversation session.
        
        Args:
            session_id: Unique session ID
        
        Returns:
            New conversation context
        """
        context = ConversationContext(session_id=session_id)
        self._contexts[session_id] = context
        logger.info("session_created", session_id=session_id)
        return context
    
    def get_session(self, session_id: str) -> ConversationContext | None:
        """Get an existing conversation session.
        
        Args:
            session_id: Session ID
        
        Returns:
            Conversation context or None
        """
        return self._contexts.get(session_id)
    
    def add_turn(self, session_id: str, role: str, content: str, metadata: dict[str, Any] | None = None) -> Turn:
        """Add a turn to the conversation.
        
        Args:
            session_id: Session ID
            role: Role (user, assistant, system)
            content: Turn content
            metadata: Optional metadata
        
        Returns:
            Added turn
        """
        context = self._contexts.get(session_id)
        if not context:
            context = self.create_session(session_id)
        
        turn = Turn(
            turn_id=len(context.turns) + 1,
            role=role,
            content=content,
            timestamp=datetime.now().isoformat(),
            metadata=metadata or {},
        )
        
        context.turns.append(turn)
        
        # Check if we need to summarize
        if len(context.turns) > self.summarization_threshold:
            self._summarize_context(session_id)
        
        # Check if we need to trim
        if len(context.turns) > self.max_turns:
            self._trim_context(session_id)
        
        logger.info("turn_added", session_id=session_id, turn_id=turn.turn_id, role=role)
        return turn
    
    def request_clarification(self, session_id: str, question: str, options: list[dict[str, Any]]) -> None:
        """Request clarification from the user.
        
        Args:
            session_id: Session ID
            question: Clarification question
            options: Available options
        """
        context = self._contexts.get(session_id)
        if not context:
            return
        
        context.clarification_pending = True
        context.clarification_question = question
        context.clarification_options = options
        
        logger.info("clarification_requested", session_id=session_id, question=question)
    
    def resolve_clarification(self, session_id: str, choice: str) -> dict[str, Any]:
        """Resolve a clarification with user's choice.
        
        Args:
            session_id: Session ID
            choice: User's choice
        
        Returns:
            Resolution result
        """
        context = self._contexts.get(session_id)
        if not context:
            return {"status": "error", "reason": "session_not_found"}
        
        if not context.clarification_pending:
            return {"status": "error", "reason": "no_clarification_pending"}
        
        context.clarification_pending = False
        context.clarification_question = None
        context.clarification_options = []
        
        logger.info("clarification_resolved", session_id=session_id, choice=choice)
        
        return {
            "status": "ok",
            "choice": choice,
            "session_id": session_id,
        }
    
    def get_context_for_llm(self, session_id: str, max_tokens: int = 4000) -> list[dict[str, str]]:
        """Get conversation context formatted for LLM.
        
        Args:
            session_id: Session ID
            max_tokens: Maximum tokens to include
        
        Returns:
            List of messages for LLM
        """
        context = self._contexts.get(session_id)
        if not context:
            return []
        
        messages = []
        for turn in context.turns:
            messages.append({
                "role": turn.role,
                "content": turn.content,
            })
        
        # Simple token estimation (rough)
        total_chars = sum(len(m["content"]) for m in messages)
        estimated_tokens = total_chars // 4
        
        if estimated_tokens > max_tokens:
            # Keep only recent turns
            keep_ratio = max_tokens / estimated_tokens
            keep_count = max(1, int(len(messages) * keep_ratio))
            messages = messages[-keep_count:]
        
        return messages
    
    def _summarize_context(self, session_id: str) -> None:
        """Summarize old conversation turns.
        
        Args:
            session_id: Session ID
        """
        context = self._contexts.get(session_id)
        if not context:
            return
        
        # In a real implementation, this would call an LLM to summarize
        # For now, just keep the most recent turns
        if len(context.turns) > self.summarization_threshold:
            # Mark old turns as summarized
            for turn in context.turns[:-self.summarization_threshold]:
                turn.metadata["summarized"] = True
        
        logger.info("context_summarized", session_id=session_id)
    
    def _trim_context(self, session_id: str) -> None:
        """Trim conversation context to max turns.
        
        Args:
            session_id: Session ID
        """
        context = self._contexts.get(session_id)
        if not context:
            return
        
        while len(context.turns) > self.max_turns:
            removed = context.turns.pop(0)
            logger.info("turn_trimmed", session_id=session_id, turn_id=removed.turn_id)


# ── Global Manager ──

_manager: MultiTurnContextManager | None = None


def get_manager() -> MultiTurnContextManager:
    """Get the global multi-turn context manager."""
    global _manager
    if _manager is None:
        _manager = MultiTurnContextManager()
    return _manager


# ── Convenience Functions ──

def add_user_turn(session_id: str, content: str) -> Turn:
    """Add a user turn.
    
    Args:
        session_id: Session ID
        content: User message
    
    Returns:
        Added turn
    """
    manager = get_manager()
    return manager.add_turn(session_id, "user", content)


def add_assistant_turn(session_id: str, content: str) -> Turn:
    """Add an assistant turn.
    
    Args:
        session_id: Session ID
        content: Assistant message
    
    Returns:
        Added turn
    """
    manager = get_manager()
    return manager.add_turn(session_id, "assistant", content)


def get_conversation_history(session_id: str) -> list[dict[str, str]]:
    """Get conversation history for a session.
    
    Args:
        session_id: Session ID
    
    Returns:
        List of messages
    """
    manager = get_manager()
    return manager.get_context_for_llm(session_id)
