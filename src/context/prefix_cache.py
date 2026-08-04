# -*- coding: utf-8 -*-
"""Cacheable prompt-prefix primitive (Python 2.7 compatible)."""
from __future__ import unicode_literals

from .token_budget import estimate_tokens


class PrefixBlock(object):
    def __init__(self, content, cacheable=True, tokens=0):
        self.content = content
        self.cacheable = cacheable
        self.tokens = tokens or estimate_tokens(content)


class PrefixCacheManager(object):
    """Manage a stable cacheable prefix and a variable current-state suffix."""

    def __init__(self, system_template=''):
        self._template = system_template
        self._tools_desc = ''
        self._semantic_layer = ''
        self._cached_prefix = None
        self._cached_prefix_tokens = 0

    def set_tools(self, tools_desc):
        self._tools_desc = tools_desc
        self._invalidate()

    def set_semantic_layer(self, metrics, dimensions, models):
        self._semantic_layer = ('## Semantic Layer\n\nMetrics:\n%s\n\nDimensions:\n%s\n\nModels:\n%s' %
                                (metrics, dimensions, models))
        self._invalidate()

    def _invalidate(self):
        self._cached_prefix = None
        self._cached_prefix_tokens = 0

    @property
    def cacheable_prefix(self):
        if self._cached_prefix is None:
            self._cached_prefix = self._build_prefix()
            self._cached_prefix_tokens = estimate_tokens(self._cached_prefix)
        return self._cached_prefix

    @property
    def prefix_tokens(self):
        if self._cached_prefix is None:
            self.cacheable_prefix
        return self._cached_prefix_tokens

    def _build_prefix(self):
        """Build the legacy stable prefix without request-specific state.

        ``cacheable_prefix`` remains a public compatibility surface.  It must
        expose the durable rules/tool/semantic contract even before a caller
        supplies the request-specific data IDs to ``build_system_prompt``.
        """
        parts = [
            '## Rules\n\nFollow server policy, permission scope, and evidence requirements.',
        ]
        if self._tools_desc:
            parts.append('## Tools\n\n%s' % self._tools_desc)
        if self._semantic_layer:
            parts.append(self._semantic_layer)
        return '\n\n'.join(parts)

    def build_system_prompt(self, tools_desc, semantic_summary, current_dataids):
        from config import SEMANTIC_SUMMARY as semantic_summary_config
        from system_prompt_contract import build_tool_planning_prompt
        semantic = semantic_summary or semantic_summary_config
        prompt = build_tool_planning_prompt(tools_desc, semantic, current_dataids)
        self._cached_prefix = prompt
        self._cached_prefix_tokens = estimate_tokens(prompt)
        return prompt
