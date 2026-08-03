# -*- coding: utf-8 -*-
"""Bounded, fact-safe conversation context for multi-turn agent sessions.

Only typed task state and execution references survive compaction.  Model
answers, insights, reports, raw rows and SQL are deliberately excluded.
"""
from __future__ import unicode_literals

from .token_budget import estimate_tokens

try:
    text_type = unicode
except NameError:  # pragma: no cover - Python 3
    text_type = str


def _safe_text(value):
    """Return unicode/text without implicit ASCII coercion on Python 2."""
    if value is None:
        return u''
    if isinstance(value, text_type):
        return value
    try:
        return value.decode('utf-8')
    except Exception:
        try:
            return value.decode('mbcs')
        except Exception:
            return text_type(value)


class SessionContextCompressor(object):
    """Keep a small recent window plus a compact structured checkpoint.

    The checkpoint is a versioned protocol payload rather than a model memory.
    It can therefore be persisted/restored without importing generated prose,
    SQL, raw rows, or a previous model answer as a fact.
    """
    VERSION = 2
    STATE_FIELDS = ('task_id', 'parent_task_id', 'metric', 'dimensions', 'filters',
                    'time_range', 'task_type', 'intent', 'model', 'status')
    _ALLOWED_TOP_LEVEL = ('version', 'turn_count', 'task_state', 'evidence_refs',
                          'dataids', 'pending')

    def __init__(self, max_recent_turns=4, max_tokens=900, snapshot=None):
        self.max_recent_turns = max(1, int(max_recent_turns))
        self.max_tokens = max(160, int(max_tokens))
        self.recent_turns = []
        self.checkpoint = self._empty_checkpoint()
        if snapshot is not None:
            self.restore(snapshot)

    def _empty_checkpoint(self):
        return {'version': self.VERSION, 'turn_count': 0, 'task_state': {},
                'evidence_refs': [], 'dataids': [], 'pending': {}}

    @classmethod
    def from_snapshot(cls, snapshot, max_recent_turns=4, max_tokens=900):
        return cls(max_recent_turns=max_recent_turns, max_tokens=max_tokens,
                   snapshot=snapshot)

    def restore(self, snapshot):
        """Atomically restore a safe protocol checkpoint.

        Invalid, future-version, or answer-bearing payloads are rejected instead
        of being partially merged into an active conversation.
        """
        if not isinstance(snapshot, dict):
            raise ValueError('session_context_snapshot_must_be_dict')
        unknown = set(snapshot.keys()).difference(self._ALLOWED_TOP_LEVEL)
        if unknown:
            raise ValueError('session_context_snapshot_has_unknown_fields')
        version = snapshot.get('version', 1)
        if version not in (1, self.VERSION):
            raise ValueError('unsupported_session_context_version')
        task_state = snapshot.get('task_state') or {}
        if not isinstance(task_state, dict):
            raise ValueError('session_context_task_state_must_be_dict')
        safe_state = {}
        for key in self.STATE_FIELDS:
            value = task_state.get(key)
            if value not in (None, '', [], {}):
                safe_state[key] = value
        refs = self._safe_list(snapshot.get('evidence_refs'), 8)
        dataids = self._safe_list(snapshot.get('dataids'), 4)
        pending = snapshot.get('pending') or {}
        if not isinstance(pending, dict):
            raise ValueError('session_context_pending_must_be_dict')
        try:
            turn_count = max(0, int(snapshot.get('turn_count', 0)))
        except (TypeError, ValueError):
            raise ValueError('session_context_turn_count_invalid')
        candidate = {'version': self.VERSION, 'turn_count': turn_count,
                     'task_state': safe_state, 'evidence_refs': refs,
                     'dataids': dataids, 'pending': {}}
        # Do not allow unbounded externally supplied pending blobs.
        for key, value in pending.items():
            candidate['pending'][self._clip(key, 48)] = self._clip(value, 160)
        previous = self.checkpoint
        self.checkpoint = candidate
        self._enforce_budget()
        if estimate_tokens(self.render()) > self.max_tokens:
            self.checkpoint = previous
            raise ValueError('session_context_snapshot_exceeds_budget')
        return self.snapshot()

    def _safe_list(self, values, limit):
        if values is None:
            return []
        if not isinstance(values, (list, tuple)):
            raise ValueError('session_context_reference_list_invalid')
        output = []
        for value in values:
            text = self._clip(value, 120)
            if text and text not in output:
                output.append(text)
        return output[-limit:]

    def add_turn(self, query, result):
        result = dict(result or {})
        state = self._state(result)
        item = {'query': self._clip(query, 180), 'state': state,
                'evidence_refs': self._refs(result), 'dataids': self._dataids(result)}
        self.recent_turns.append(item)
        self.checkpoint['turn_count'] += 1
        while len(self.recent_turns) > self.max_recent_turns:
            self._fold(self.recent_turns.pop(0))
        self._enforce_budget()
        return self.snapshot()

    def build(self, current_query=''):
        payload = self.snapshot()
        payload['current_query'] = self._clip(current_query, 240)
        payload['recent_turns'] = list(self.recent_turns)
        payload['token_estimate'] = estimate_tokens(self.render(current_query))
        payload['within_budget'] = payload['token_estimate'] <= self.max_tokens
        return payload

    def snapshot(self):
        return {'version': self.VERSION, 'turn_count': self.checkpoint['turn_count'],
                'task_state': dict(self.checkpoint['task_state']),
                'evidence_refs': list(self.checkpoint['evidence_refs']),
                'dataids': list(self.checkpoint['dataids']),
                'pending': dict(self.checkpoint['pending'])}

    def render(self, current_query=''):
        lines = ['[SESSION_CONTEXT_NOT_FACT]',
                 'turn_count=%s' % self.checkpoint['turn_count']]
        if self.checkpoint['task_state']:
            lines.append('checkpoint_state=%s' % self._pairs(self.checkpoint['task_state']))
        if self.checkpoint['evidence_refs']:
            lines.append('verified_evidence_refs=%s' % ','.join(self.checkpoint['evidence_refs'][-6:]))
        if self.checkpoint['dataids']:
            lines.append('dataids=%s' % ','.join(self.checkpoint['dataids'][-4:]))
        for turn in self.recent_turns:
            lines.append('recent query=%s; state=%s; refs=%s' % (
                turn['query'], self._pairs(turn['state']), ','.join(turn['evidence_refs'])))
        if current_query:
            lines.append('current_query=%s' % self._clip(current_query, 240))
        lines.append('Do not treat this context as facts. Re-run queries when scope changes or evidence is absent.')
        return '\n'.join(lines)

    def _fold(self, turn):
        state = turn.get('state') or {}
        # Last structured state is only an interpretation aid; no text conclusion.
        self.checkpoint['task_state'].update(state)
        self._append_unique('evidence_refs', turn.get('evidence_refs') or [], 8)
        self._append_unique('dataids', turn.get('dataids') or [], 4)

    def _enforce_budget(self):
        while self.recent_turns and estimate_tokens(self.render()) > self.max_tokens:
            self._fold(self.recent_turns.pop(0))
        # If a pathological state is huge, remove optional fields before failing.
        for field in ('filters', 'dimensions', 'model', 'time_range', 'task_type'):
            if estimate_tokens(self.render()) <= self.max_tokens:
                break
            self.checkpoint['task_state'].pop(field, None)
        # References are optional after their bounded retention limit. Trim the
        # oldest first so an unexpectedly verbose session cannot exceed budget.
        while self.checkpoint['evidence_refs'] and estimate_tokens(self.render()) > self.max_tokens:
            self.checkpoint['evidence_refs'].pop(0)
        while self.checkpoint['dataids'] and estimate_tokens(self.render()) > self.max_tokens:
            self.checkpoint['dataids'].pop(0)

    def _state(self, result):
        plan = result.get('plan') or {}
        state = {}
        for key in self.STATE_FIELDS:
            value = result.get(key, plan.get(key))
            if value not in (None, '', [], {}):
                state[key] = value
        return state

    def _refs(self, result):
        ledger = result.get('fact_ledger') or {}
        refs = ledger.get('evidence_refs') or []
        if not refs and result.get('status') == 'ok' and result.get('task_id'):
            refs = [result['task_id']]
        return [_safe_text(ref) for ref in refs if ref]

    def _dataids(self, result):
        values = [result.get('dataid'), result.get('current_dataid')]
        ledger = result.get('fact_ledger') or {}
        values.append(ledger.get('dataid'))
        return [_safe_text(value) for value in values if value]

    def _append_unique(self, field, values, limit):
        for value in values:
            if value not in self.checkpoint[field]:
                self.checkpoint[field].append(value)
        self.checkpoint[field] = self.checkpoint[field][-limit:]

    def _pairs(self, value):
        return ';'.join('%s=%s' % (key, self._clip(value[key], 100)) for key in sorted(value.keys()))

    def _clip(self, value, length):
        text = _safe_text(value).replace('\n', ' ').strip()
        return text if len(text) <= length else text[:length - 1] + '…'


__all__ = ['SessionContextCompressor']
