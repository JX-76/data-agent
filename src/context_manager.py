"""Context management compatibility layer.

The implementation has been split into the `context/` package. This module
re-exports the legacy symbols so existing imports keep working.
"""

from context import (
    ContextBudget,
    MessageCompactor,
    PrefixBlock,
    PrefixCacheManager,
    RollingSummarizer,
    ResultTrimmer,
    estimate_messages_tokens,
    estimate_tokens,
)

__all__ = [
    "ContextBudget",
    "MessageCompactor",
    "PrefixBlock",
    "PrefixCacheManager",
    "RollingSummarizer",
    "ResultTrimmer",
    "estimate_messages_tokens",
    "estimate_tokens",
]
