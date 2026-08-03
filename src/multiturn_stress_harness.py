# -*- coding: utf-8 -*-
"""Deterministic multi-turn stress harness for conversational AgentFacade flows.

This harness intentionally evaluates observable session invariants instead of
pretending that every scenario has a real connector or a trained forecast model.
A scenario passes only when every turn reaches an allowed safe terminal state,
never raises an exception, and contextual turns retain a parent-task link.
"""
from __future__ import unicode_literals

import time
import traceback

from agent_facade import AgentFacade
from answer_quality_evaluator import evaluate_turn, summarize_turn_scores


SAFE_TERMINAL = ('ok', 'degraded', 'need_clarification', 'unsupported',
                 'blocked', 'pending_human_review')


def _text(value):
    if value is None:
        return u''
    try:
        return value.decode('utf-8', 'ignore') if isinstance(value, bytes) else unicode(value)
    except NameError:
        return str(value)
    except Exception:
        return u''


def _as_dict(value):
    return dict(value) if isinstance(value, dict) else {}


class MultiturnStressHarness(object):
    """Run a case with one stable AgentFacade session per conversation."""

    def __init__(self, facade_factory=None):
        self.facade_factory = facade_factory or (lambda session_id: AgentFacade(session_id=session_id))

    def _make_facade(self, case):
        session_id = 'multiturn-stress-%s' % (case.get('id') or int(time.time() * 1000))
        try:
            return self.facade_factory(session_id)
        except TypeError:
            return self.facade_factory()

    def _check_turn(self, spec, result, turn_index):
        result = _as_dict(result)
        expected = _as_dict(spec.get('expected'))
        statuses = expected.get('allowed_statuses') or SAFE_TERMINAL
        errors = []
        status = result.get('status')
        if status not in statuses:
            errors.append('turn_%d: status expected one of=%s got=%s' % (turn_index, statuses, status))
        if status == 'error':
            errors.append('turn_%d: unexpected_error' % turn_index)
        if expected.get('require_parent_context'):
            followup = _as_dict(result.get('follow_up_context'))
            if not (result.get('parent_task_id') or followup.get('parent_task_id')):
                errors.append('turn_%d: missing_parent_context' % turn_index)
        text = _text(result)
        for signal in expected.get('must_contain') or []:
            if _text(signal).lower() not in text.lower():
                errors.append('turn_%d: missing_signal:%s' % (turn_index, signal))
        for signal in expected.get('must_not_contain') or []:
            if _text(signal).lower() in text.lower():
                errors.append('turn_%d: forbidden_signal:%s' % (turn_index, signal))
        return errors

    @staticmethod
    def _quality_expected(spec):
        """Accept the additive, per-answer quality expectations from a case."""
        expected = _as_dict(spec.get('expected'))
        return _as_dict(expected.get('quality') or expected)

    def run_case(self, case):
        case = _as_dict(case)
        facade = self._make_facade(case)
        turns = case.get('turns') or []
        turn_results, errors = [], []
        started = time.time()
        for index, spec in enumerate(turns):
            spec = _as_dict(spec)
            query = spec.get('query') or ''
            try:
                result = facade.ask(query) if index == 0 else facade.follow_up(query)
            except Exception:
                result = {'status': 'error', 'errors': [traceback.format_exc()]}
            turn_errors = self._check_turn(spec, result, index + 1)
            quality = evaluate_turn(query, result, self._quality_expected(spec))
            for issue in quality.get('issues') or []:
                turn_errors.append('turn_%d: quality_%s' % (index + 1, issue.get('code')))
            errors.extend(turn_errors)
            turn_results.append({
                'turn': index + 1, 'query': query, 'status': result.get('status'),
                'errors': turn_errors, 'parent_task_id': result.get('parent_task_id'),
                'follow_up_context': _as_dict(result.get('follow_up_context')),
                'answer': quality.get('answer_excerpt') or '', 'quality': quality,
                'trace_id': result.get('trace_id')
            })
        return {
            'id': case.get('id'), 'title': case.get('title'),
            'category': case.get('category'), 'tags': case.get('tags') or [],
            'passed': not errors, 'errors': errors, 'turns': turn_results,
            'duration_ms': int((time.time() - started) * 1000)
        }

    def run_cases(self, cases):
        return [self.run_case(case) for case in cases or []]

    @staticmethod
    def summarize(results):
        results = results or []
        category = {}
        hotspots = {}
        passed = 0
        for item in results:
            key = item.get('category') or 'uncategorized'
            bucket = category.setdefault(key, {'total': 0, 'passed': 0})
            bucket['total'] += 1
            if item.get('passed'):
                passed += 1
                bucket['passed'] += 1
            for error in item.get('errors') or []:
                code = error.split(': ', 1)[-1].split(':', 1)[0]
                hotspots[code] = hotspots.get(code, 0) + 1
        for bucket in category.values():
            bucket['pass_rate'] = round(bucket['passed'] / float(max(1, bucket['total'])), 4)
        turn_quality = []
        for item in results:
            turn_quality.extend([turn.get('quality') for turn in item.get('turns') or [] if turn.get('quality')])
        return {'suite': 'multiturn_stress_50', 'total': len(results), 'passed': passed,
                'failed': len(results) - passed,
                'pass_rate': round(passed / float(max(1, len(results))), 4),
                'category_breakdown': category, 'failure_hotspots': hotspots,
                'answer_quality': summarize_turn_scores(turn_quality)}


__all__ = ['MultiturnStressHarness', 'SAFE_TERMINAL']
