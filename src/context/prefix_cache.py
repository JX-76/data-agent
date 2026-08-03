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
        return '''## Rules

1. Always start with switch(model_id) to enter a semantic view.
2. After switch, always preview(dataid, 5) to see what the data looks like.
3. Then filter + aggregate as needed.
4. Only use merge when you need to compare TWO metrics on the SAME dimension.
5. If results are empty or unexpected, try adjusting: different time range, different dimension, different metric.
6. When you have meaningful results, call done() with a brief summary.

## Output Format
For each step, output EXACTLY ONE JSON action:
Action with tool: {"action": "tool", "tool": "tool_name", "args": {...}, "reasoning": "..."}
When finished: {"action": "done", "summary": "final analysis summary in Chinese"}

## Important
- Do not hallucinate dataids: only use dataids you have observed.
- Only one action per response.
- Strict JSON output only, no surrounding text.'''

    def build_system_prompt(self, tools_desc, semantic_summary, current_dataids):
        import json as _json
        from config import SEMANTIC_SUMMARY as semantic_summary_config

        cacheable = ('%s\n\n## Available Tools\n%s\n\n## Semantic Layer\nMetrics:\n%s\n\nDimensions:\n%s\n\nModels:\n%s' % (
            self.cacheable_prefix,
            tools_desc,
            _json.dumps(semantic_summary_config['metrics'], ensure_ascii=False, indent=2),
            _json.dumps(semantic_summary_config['dimensions'], ensure_ascii=False, indent=2),
            _json.dumps(semantic_summary_config['models'], ensure_ascii=False, indent=2),
        ))
        variable = ('## Current State\nDataID Reference: %s\n- After each tool call, you receive a new dataid.\n- Use the dataid from the LAST tool result in the LAST observation as input.' %
                    _json.dumps(current_dataids))
        return cacheable + '\n\n' + variable
