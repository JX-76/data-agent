# -*- coding: utf-8 -*-
"""Traceable conversation compaction primitive.

The compactor does not write agent traces itself.  Callers can request compact
trace records with ``return_trace=True`` and append them to their own trace.
"""
from __future__ import unicode_literals

import json
import re

from .token_budget import estimate_messages_tokens


class _FallbackLogger(object):
    def warning(self, *args, **kwargs):
        pass


logger = _FallbackLogger()

_EVIDENCE_RE = re.compile(r'evidence_id\s*[:=]\s*([^\s,\]\n]+)', re.I)
_DATAID_RE = re.compile(r'dataid\s*[:=]\s*([^\s,\]\n]+)', re.I)
_ROW_COUNT_RE = re.compile(r'(?:row_count|rows)\s*[:=]\s*(\d+)', re.I)
_SUBTASK_RE = re.compile(r'subtask_summary\s*[:=]\s*([^\n]+)', re.I)


class MessageCompactor(object):
    """Compact messages while preserving evidence and task-summary references."""

    def __init__(self, max_tokens=8000):
        self.max_tokens = max_tokens

    def compact(self, messages, system_tokens=0, return_trace=False):
        """Return compacted messages, optionally with audit-ready trace records.

        The legacy return type remains a list.  With ``return_trace=True``, the
        return value is ``(messages, trace_records)``.
        """
        compacted = [dict(message) for message in (messages or [])]
        trace_records = []
        if system_tokens + estimate_messages_tokens(compacted) <= self.max_tokens:
            return self._return(compacted, trace_records, return_trace)

        budget = max(0, self.max_tokens - system_tokens - 500)
        self._trim_large_observations(compacted, trace_records)
        if system_tokens + estimate_messages_tokens(compacted) <= budget:
            return self._return(compacted, trace_records, return_trace)

        # Replace only an explicitly paired historical action + observation.
        # User/system anchors without a paired observation are never deleted.
        while len(compacted) > 6:
            replacement = self._find_pair_replacement(compacted)
            if replacement is None:
                break
            index, summary, record = replacement
            compacted[index:index + 2] = [{
                'role': 'system',
                'content': summary,
            }]
            trace_records.append(record)
            if system_tokens + estimate_messages_tokens(compacted) <= budget:
                break

        return self._return(compacted, trace_records, return_trace)

    def _return(self, compacted, trace_records, return_trace):
        if return_trace:
            return compacted, trace_records
        return compacted

    def _trim_large_observations(self, messages, trace_records):
        for index in range(len(messages) - 4, -1, -1):
            message = messages[index]
            content = message.get('content', '')
            if message.get('role') != 'user' or not content.startswith('OBSERVATION:'):
                continue
            if len(content) <= 200:
                continue

            metadata = self._extract_metadata(content)
            summary = self._observation_summary(content, metadata)
            message['content'] = summary
            trace_records.append(self._trace_record(
                'observation_trim', index, metadata,
                metadata.get('subtask_summary') or 'observation trimmed',
            ))

    def _find_pair_replacement(self, messages):
        for index in range(len(messages) - 1):
            action_message = messages[index]
            observation_message = messages[index + 1]
            observation = observation_message.get('content', '')
            if action_message.get('role') != 'assistant':
                continue
            if observation_message.get('role') != 'user' or 'OBSERVATION:' not in observation:
                continue

            action = self._parse_action(action_message.get('content', ''))
            metadata = self._extract_metadata(observation)
            subtask_summary = (metadata.get('subtask_summary') or
                               action.get('subtask_summary') or
                               action.get('reason') or
                               action.get('tool') or
                               self._first_line(observation))
            summary = self._pair_summary(action, metadata, subtask_summary)
            record = self._trace_record('pair_soft_delete', index, metadata, subtask_summary)
            return index, summary, record
        return None

    def _parse_action(self, content):
        if not content:
            return {}
        try:
            action = json.loads(content)
            if isinstance(action, dict):
                return action
        except Exception as exc:
            logger.warning('context_action_parse_failed', error=str(exc))
        return {}

    def _extract_metadata(self, content):
        evidence = _EVIDENCE_RE.search(content)
        dataid = _DATAID_RE.search(content)
        row_count = _ROW_COUNT_RE.search(content)
        subtask = _SUBTASK_RE.search(content)
        return {
            'evidence_id': evidence.group(1) if evidence else None,
            'dataid': dataid.group(1) if dataid else None,
            'row_count': row_count.group(1) if row_count else None,
            'subtask_summary': subtask.group(1).strip() if subtask else None,
        }

    def _observation_summary(self, content, metadata):
        brief = [
            'OBSERVATION: [trimmed for context budget]',
            'OBSERVATION_REF evidence_id: %s' % (metadata.get('evidence_id') or 'null'),
            'dataid: %s' % (metadata.get('dataid') or 'null'),
            'row_count: %s' % (metadata.get('row_count') or 'null'),
            'subtask_summary: %s' % (metadata.get('subtask_summary') or self._first_line(content)),
            '[Full observation trimmed for context budget]',
        ]
        return '\n'.join(brief)

    def _pair_summary(self, action, metadata, subtask_summary):
        tool_name = action.get('tool') or 'unknown'
        return ('CONTEXT_COMPACTION_REF event: pair_soft_delete; tool: %s; '
                'evidence_id: %s; dataid: %s; row_count: %s; subtask_summary: %s' % (
                    tool_name,
                    metadata.get('evidence_id') or 'null',
                    metadata.get('dataid') or 'null',
                    metadata.get('row_count') or 'null',
                    subtask_summary or 'historical observation compacted',
                ))

    def _trace_record(self, action, message_index, metadata, subtask_summary):
        return {
            'event': 'context_compacted',
            'action': action,
            'message_index': message_index,
            'evidence_id': metadata.get('evidence_id'),
            'dataid': metadata.get('dataid'),
            'row_count': metadata.get('row_count'),
            'subtask_summary': subtask_summary or '',
            'reason': 'context_budget',
        }

    def _first_line(self, content):
        for line in content.splitlines():
            line = line.strip()
            if line and line != 'OBSERVATION:':
                return line[:160]
        return 'observation compacted'
