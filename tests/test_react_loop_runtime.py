# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from react_loop_runtime import ControlledReactLoop
from task_anchor import TaskAnchor


class FakeGovernor(object):
    def __init__(self, actions):
        self.actions = list(actions)
        self.calls = []

    def govern(self, anchor, step, tool_name, result, **kwargs):
        self.calls.append((step, tool_name, result))
        action = self.actions.pop(0)
        return {'action': action, 'injectable': action == 'allow',
                'decision': {'reason': 'test_%s' % action}}


class FakeObserver(object):
    def __init__(self):
        self.events = []

    def record(self, name, **kwargs):
        self.events.append((name, kwargs))


class ControlledReactLoopTest(unittest.TestCase):
    def setUp(self):
        self.anchor = TaskAnchor(task_id='task-1', metric='gmv',
                                 task_type='anomaly')

    def test_allow_stops_after_one_action(self):
        executed = []
        loop = ControlledReactLoop(lambda plan: executed.append(plan) or {'status': 'ok'},
                                   FakeGovernor(['allow']), max_steps=2)
        result = loop.run(self.anchor, {'metric': 'gmv'})
        self.assertEqual(1, result.steps)
        self.assertEqual('allow', result.terminal_action)
        self.assertFalse(result.exhausted)
        self.assertEqual(1, len(executed))

    def test_quarantine_stops_and_is_not_injectable(self):
        loop = ControlledReactLoop(lambda plan: {'status': 'ok'},
                                   FakeGovernor(['quarantine']), max_steps=2)
        result = loop.run(self.anchor, {'metric': 'gmv'})
        self.assertEqual('quarantine', result.terminal_action)
        self.assertEqual(1, result.steps)
        self.assertFalse(result.observations[0]['injectable'])

    def test_pivot_replans_once_then_allows(self):
        plans = []
        observer = FakeObserver()
        loop = ControlledReactLoop(lambda plan: plans.append(plan) or {'status': 'ok'},
                                   FakeGovernor(['pivot', 'allow']), max_steps=2,
                                   observer=observer)
        result = loop.run(self.anchor, {'metric': 'gmv', 'diagnostics': {}})
        self.assertEqual(2, result.steps)
        self.assertEqual('allow', result.terminal_action)
        self.assertEqual(1, len(result.replans))
        self.assertIsNone(plans[1]['diagnostics']['react_observation_ref'])
        self.assertEqual(['react_observation', 'react_replan', 'react_observation'],
                         [event[0] for event in observer.events])

    def test_pivot_budget_exhaustion_prevents_unbounded_loop(self):
        executed = []
        loop = ControlledReactLoop(lambda plan: executed.append(plan) or {'status': 'ok'},
                                   FakeGovernor(['pivot', 'pivot', 'pivot']), max_steps=2)
        result = loop.run(self.anchor, {'metric': 'gmv'})
        self.assertEqual(2, result.steps)
        self.assertTrue(result.exhausted)
        self.assertEqual('pivot', result.terminal_action)
        self.assertEqual(2, len(executed))


if __name__ == '__main__':
    unittest.main()
