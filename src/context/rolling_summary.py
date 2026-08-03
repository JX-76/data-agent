# -*- coding: utf-8 -*-
"""Rolling conversation summary primitive."""
from __future__ import unicode_literals

from .token_budget import estimate_tokens


class RollingSummarizer(object):
    """Maintain a short, injectable conversation summary."""

    def __init__(self, max_summary_tokens=200):
        self.max_summary_tokens = max_summary_tokens
        self.summary = ''
        self.key_findings = []
        self.topic_chain = []

    def add_turn(self, query, result):
        status = result.get('status', 'error')
        model = result.get('model', '')
        metric = result.get('metric', '')
        insight = ''
        if isinstance(result.get('insight'), dict):
            insight = result['insight'].get('insight', '')

        parts = []
        if query:
            parts.append('问「%s」' % query[:30])

        if status == 'ok':
            if metric:
                parts.append('指标=%s' % metric)
            if model:
                parts.append('模型=%s' % model)
            if insight:
                parts.append('结论：%s' % insight[:60])
        elif status == 'blocked':
            parts.append('被拦截')
        elif status == 'clarification_needed':
            parts.append('需要澄清')

        turn_text = '；'.join(parts)

        topic = metric or model or query[:15]
        if topic and (not self.topic_chain or topic != self.topic_chain[-1]):
            self.topic_chain.append(topic)
            if len(self.topic_chain) > 5:
                self.topic_chain.pop(0)

        if insight and len(insight) > 10:
            self.key_findings.append(insight[:80])
            if len(self.key_findings) > 3:
                self.key_findings.pop(0)

        lines = []
        if self.topic_chain:
            lines.append('对话脉络：%s' % ' → '.join(self.topic_chain))
        if self.key_findings:
            lines.append('关键发现：%s' % ' | '.join(self.key_findings))
        lines.append(turn_text)

        self.summary = '\n'.join(lines)

        tokens = estimate_tokens(self.summary)
        if tokens > self.max_summary_tokens:
            self.summary = '\n'.join(lines[:1] + [lines[-1]])

    def get_injectable_context(self):
        if not self.summary:
            return ''
        return '[对话上下文]\n%s' % self.summary
