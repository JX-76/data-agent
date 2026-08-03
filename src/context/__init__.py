"""Context utilities package.

This package contains split-out context management components. The legacy
`context_manager` module re-exports these symbols for backwards compatibility.
"""

from .token_budget import estimate_tokens, estimate_messages_tokens
from .prefix_cache import PrefixBlock, PrefixCacheManager
from .result_trimmer import ResultTrimmer
from .message_compactor import MessageCompactor
from .context_budget import ContextBudget
from .rolling_summary import RollingSummarizer
from .session_context import SessionContextCompressor

__all__ = [
    "estimate_tokens",
    "estimate_messages_tokens",
    "PrefixBlock",
    "PrefixCacheManager",
    "ResultTrimmer",
    "MessageCompactor",
    "ContextBudget",
    "RollingSummarizer",
    "SessionContextCompressor",
]
