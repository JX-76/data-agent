# -*- coding: utf-8 -*-
"""Extension points for future multi-agent orchestration.

This module intentionally stays thin: it defines contracts and hook helpers so
we can evolve toward planner/executor/verifier style orchestration without
hard-coding those roles into the current execution path.
"""


class TaskStep(object):
    def __init__(self, name, purpose="", inputs=None, outputs=None, status="pending"):
        self.name = name
        self.purpose = purpose
        self.inputs = inputs or {}
        self.outputs = outputs or {}
        self.status = status


class TaskPlan(object):
    def __init__(self, query, steps=None, policy=None, notes=""):
        self.query = query
        self.steps = steps or []
        self.policy = policy or {}
        self.notes = notes


def build_task_plan(query, steps=None, **policy):
    task_steps = [TaskStep(**s) for s in (steps or [])]
    return TaskPlan(query=query, steps=task_steps, policy=dict(policy))


__all__ = ["TaskStep", "TaskPlan", "build_task_plan"]
