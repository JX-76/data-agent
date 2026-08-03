# -*- coding: utf-8 -*-
"""Context-window budget primitive (Python 2.7 compatible)."""
from __future__ import unicode_literals


class ContextBudget(object):
    """Track token consumption without making compaction decisions."""

    def __init__(self, max_total_tokens=8000, reserve_for_response=512,
                 system_prompt_tokens=0, current_tokens=0):
        self.max_total_tokens = max_total_tokens
        self.reserve_for_response = reserve_for_response
        self.system_prompt_tokens = system_prompt_tokens
        self.current_tokens = current_tokens

    @property
    def available(self):
        return self.max_total_tokens - self.current_tokens - self.reserve_for_response

    @property
    def usage_ratio(self):
        if not self.max_total_tokens:
            return 1.0
        return float(self.current_tokens) / self.max_total_tokens

    @property
    def is_near_limit(self):
        return self.usage_ratio > 0.8

    @property
    def is_critical(self):
        return self.usage_ratio > 0.95

    def add(self, tokens):
        self.current_tokens += tokens

    def reset(self):
        self.current_tokens = self.system_prompt_tokens
