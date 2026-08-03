# -*- coding: utf-8 -*-
"""Bounded and governed ReAct action-observation runtime.

The runtime is deliberately independent from AgentFacade.  It owns only the
safe loop semantics: execute an already-approved action, govern its observation,
and either stop, quarantine, or perform one audited pivot/replan.  Planning,
SQL execution, and user-facing response normalization remain owned by the
existing facade/runtime layers.

Python 2.7 compatible.
"""
from __future__ import unicode_literals

from task_anchor import DECISION_ALLOW, DECISION_PIVOT, DECISION_QUARANTINE


class ReactLoopResult(object):
    def __init__(self, result=None, observations=None, steps=0,
                 terminal_action=None, exhausted=False, replans=None):
        self.result = result
        self.observations = list(observations or [])
        self.steps = int(steps or 0)
        self.terminal_action = terminal_action
        self.exhausted = bool(exhausted)
        self.replans = list(replans or [])

    def to_dict(self):
        return {
            'result': self.result,
            'observations': list(self.observations),
            'steps': self.steps,
            'terminal_action': self.terminal_action,
            'exhausted': self.exhausted,
            'replans': list(self.replans),
        }


class ControlledReactLoop(object):
    """Run bounded action-observation cycles under an observation governor."""

    def __init__(self, executor, governor, max_steps=2, observer=None):
        if not callable(executor):
            raise ValueError('executor must be callable')
        if governor is None or not hasattr(governor, 'govern'):
            raise ValueError('governor must provide govern()')
        self.executor = executor
        self.governor = governor
        self.max_steps = max(1, int(max_steps or 1))
        self.observer = observer

    def _record(self, name, trace_id, task_id, session_id, status, metadata):
        if self.observer is None:
            return
        try:
            self.observer.record(name, trace_id=trace_id, task_id=task_id,
                                 session_id=session_id, status=status,
                                 metadata=metadata)
        except Exception:
            pass

    def _next_plan(self, plan, outcome, step_index, replan):
        if replan is not None:
            return replan(plan, outcome, step_index)
        data = plan.to_dict() if hasattr(plan, 'to_dict') else dict(plan or {})
        data = dict(data)
        diagnostics = dict(data.get('diagnostics') or {})
        diagnostics['react_pivot_from_step'] = step_index
        diagnostics['react_pivot_reason'] = (outcome.get('decision') or {}).get('reason')
        # A pivot must never inherit raw or implicitly injectable observation data.
        diagnostics['react_observation_ref'] = None
        data['diagnostics'] = diagnostics
        return data

    def run(self, task_anchor, plan, trace_id=None, task_id=None, session_id=None,
            tool_name='sql_query', replan=None):
        active_plan = plan
        outcomes = []
        replans = []
        last_result = None
        terminal_action = None

        for step_index in range(self.max_steps):
            last_result = self.executor(active_plan)
            outcome = self.governor.govern(
                task_anchor, step_index, tool_name, last_result,
                trace_id=trace_id, task_id=task_id, session_id=session_id)
            outcomes.append(outcome)
            action = outcome.get('action') if isinstance(outcome, dict) else DECISION_QUARANTINE
            terminal_action = action
            self._record('react_observation', trace_id, task_id, session_id,
                         action, {'step': step_index, 'action': action,
                                  'injectable': bool(outcome.get('injectable'))
                                  if isinstance(outcome, dict) else False})

            if action != DECISION_PIVOT:
                return ReactLoopResult(last_result, outcomes, step_index + 1,
                                       terminal_action, False, replans)
            if step_index + 1 >= self.max_steps:
                return ReactLoopResult(last_result, outcomes, step_index + 1,
                                       terminal_action, True, replans)

            active_plan = self._next_plan(active_plan, outcome, step_index, replan)
            replans.append({'from_step': step_index, 'to_step': step_index + 1,
                            'reason': (outcome.get('decision') or {}).get('reason')})
            self._record('react_replan', trace_id, task_id, session_id, 'pivot',
                         dict(replans[-1]))

        return ReactLoopResult(last_result, outcomes, self.max_steps,
                               terminal_action, True, replans)


__all__ = ['ControlledReactLoop', 'ReactLoopResult', 'DECISION_ALLOW',
           'DECISION_PIVOT', 'DECISION_QUARANTINE']
