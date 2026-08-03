# -*- coding: utf-8 -*-
"""Token-estimation primitives for context management.

This module intentionally stays Python 2.7 compatible because it is used by
both the legacy Plan-Act path and the controlled ReAct context path.
"""
from __future__ import unicode_literals

try:
    import tiktoken
except ImportError:  # pragma: no cover - optional dependency
    tiktoken = None

try:
    string_types = (basestring,)
except NameError:  # pragma: no cover - Python 3 runtime
    string_types = (str,)

ENCODING = None


class _FallbackLogger(object):
    def warning(self, *args, **kwargs):
        pass


logger = _FallbackLogger()


def _get_encoding():
    global ENCODING
    if ENCODING is None:
        if tiktoken is None:
            return None
        try:
            ENCODING = tiktoken.get_encoding('cl100k_base')
        except Exception as exc:
            logger.warning('bare_exception_caught', error=str(exc))
            ENCODING = None
    return ENCODING


def estimate_tokens(text):
    """Return a conservative estimate without requiring tiktoken."""
    if text is None:
        text = ''
    enc = _get_encoding()
    if enc:
        return len(enc.encode(text))
    return len(text) // 2


def estimate_messages_tokens(messages):
    """Estimate token usage for OpenAI-style message dictionaries."""
    total = 0
    for message in messages or []:
        role_tokens = 4
        content = message.get('content', '')
        if isinstance(content, string_types):
            role_tokens += estimate_tokens(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and 'text' in part:
                    role_tokens += estimate_tokens(part['text'])
        total += role_tokens
    return total + 2
