# -*- coding: utf-8 -*-
"""Promotion, quarantine and compact-context policy for agent evidence."""
from __future__ import unicode_literals

from memory_contracts import (MEMORY_STATE_QUARANTINED, MEMORY_STATE_VERIFIED,
                              MEMORY_STATE_WORKING, AUTHORITY_VERIFIED)
from task_anchor import DECISION_ALLOW


class MemoryPolicy(object):
    def __init__(self, max_cards=8, max_summary_chars=240):
        self.max_cards = max_cards
        self.max_summary_chars = max_summary_chars

    def apply(self, anchor, card):
        decision = anchor.assess(card.to_dict() if hasattr(card, 'to_dict') else card)
        if decision.action == DECISION_ALLOW:
            card.state = MEMORY_STATE_VERIFIED if card.authority == AUTHORITY_VERIFIED else MEMORY_STATE_WORKING
            card.relevance = decision.relevance
        else:
            card.state = MEMORY_STATE_QUARANTINED
            card.relevance = decision.relevance
            card.metadata['quarantine_reason'] = decision.reason
            card.metadata['anchor_conflicts'] = list(decision.conflicts)
        return card, decision

    def injectable_cards(self, cards):
        allowed = []
        seen = set()
        for card in cards or []:
            data = card.to_dict() if hasattr(card, 'to_dict') else dict(card)
            if data.get('state') == MEMORY_STATE_QUARANTINED:
                continue
            signature = '%s|%s|%s' % (data.get('dataid'), data.get('metric'), data.get('summary'))
            if signature in seen:
                continue
            seen.add(signature)
            allowed.append(data)
        allowed.sort(key=lambda item: (item.get('authority') == AUTHORITY_VERIFIED,
                                       item.get('relevance', 0), item.get('confidence', 0)), reverse=True)
        return allowed[:self.max_cards]

    def compact_context(self, cards):
        result = []
        for data in self.injectable_cards(cards):
            summary = data.get('summary') or ''
            if len(summary) > self.max_summary_chars:
                summary = summary[:self.max_summary_chars - 3] + '...'
            result.append({
                'evidence_id': data.get('evidence_id'), 'dataid': data.get('dataid'),
                'summary': summary, 'metric': data.get('metric'),
                'dimensions': data.get('dimensions') or [],
                'authority': data.get('authority'), 'relevance': data.get('relevance'),
            })
        return result


__all__ = ['MemoryPolicy']
