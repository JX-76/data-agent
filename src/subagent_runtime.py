# -*- coding: utf-8 -*-
"""SubAgent runtime: isolated execution contracts for child agents.

This module provides the abstraction layer for running sub-agents in
sandboxed environments (Docker/K8s). The current implementation is a
local stub that enforces contract boundaries without real container
isolation. Production deployments should swap in DockerSandboxRunner
or KubernetesJobSandboxRunner.

Python 2.7 compatible.
"""

from __future__ import unicode_literals

import time


class SubAgentTask(object):
    """Describes a task to be dispatched to a sub-agent."""

    def __init__(self, subagent_type, task_id=None, parent_task_id=None,
                 prompt_id=None, prompt_version=None, input_contract=None,
                 allowed_tools=None, memory_policy='isolated',
                 sandbox_backend='docker'):
        self.subagent_type = subagent_type
        self.task_id = task_id
        self.parent_task_id = parent_task_id
        self.prompt_id = prompt_id
        self.prompt_version = prompt_version
        self.input_contract = input_contract or {}
        self.allowed_tools = allowed_tools or []
        self.memory_policy = memory_policy
        self.sandbox_backend = sandbox_backend

    def to_dict(self):
        return {
            'subagent_type': self.subagent_type,
            'task_id': self.task_id,
            'parent_task_id': self.parent_task_id,
            'prompt_id': self.prompt_id,
            'prompt_version': self.prompt_version,
            'input_contract': dict(self.input_contract),
            'allowed_tools': list(self.allowed_tools),
            'memory_policy': self.memory_policy,
            'sandbox_backend': self.sandbox_backend,
        }


class SubAgentResult(object):
    """Result returned by a sub-agent execution."""

    def __init__(self, status='ok', output=None, findings=None,
                 risk_level='low', requires_human_review=False,
                 duration_ms=0, sandbox_meta=None):
        self.status = status
        self.output = output or {}
        self.findings = findings or []
        self.risk_level = risk_level
        self.requires_human_review = requires_human_review
        self.duration_ms = duration_ms
        self.sandbox_meta = sandbox_meta or {}

    def to_dict(self):
        return {
            'status': self.status,
            'output': self.output,
            'findings': list(self.findings),
            'risk_level': self.risk_level,
            'requires_human_review': self.requires_human_review,
            'duration_ms': self.duration_ms,
            'sandbox_meta': dict(self.sandbox_meta),
        }


class SandboxPolicy(object):
    """Describes the security constraints for a sandbox execution."""

    DOCKER_DEFAULTS = {
        'network': 'none',
        'read_only_rootfs': True,
        'cap_drop': ['ALL'],
        'no_new_privileges': True,
        'memory_limit': '256m',
        'cpus': '0.5',
        'pids_limit': 64,
        'user': '1000:1000',
    }

    K8S_DEFAULTS = {
        'run_as_non_root': True,
        'run_as_user': 1000,
        'allow_privilege_escalation': False,
        'read_only_root_filesystem': True,
        'cap_drop': ['ALL'],
        'network_policy': 'deny-all',
        'active_deadline_seconds': 60,
        'ttl_seconds_after_finished': 120,
    }

    def __init__(self, backend='docker'):
        self.backend = backend

    def policy(self):
        if self.backend == 'kubernetes':
            return dict(self.K8S_DEFAULTS)
        return dict(self.DOCKER_DEFAULTS)


class SubAgentRuntime(object):
    """Orchestrates sub-agent dispatch with contract and sandbox enforcement.

    In local/test mode this is a stub that returns contract metadata without
    launching real containers. In production, DockerSandboxRunner or
    KubernetesJobSandboxRunner would replace the run() implementation.
    """

    def __init__(self, backend='docker'):
        self.backend = backend
        self.sandbox_policy = SandboxPolicy(backend)

    def describe(self, subagent_type, task_id=None, parent_task_id=None):
        """Return sandbox metadata for harness validation."""
        return {
            'subagent_type': subagent_type,
            'task_id': task_id,
            'parent_task_id': parent_task_id,
            'sandbox_backend': self.backend,
            'sandbox_policy': self.sandbox_policy.policy(),
            'memory_policy': 'isolated',
            'context_inherited': False,
        }

    def run(self, task):
        """Execute a sub-agent task (stub: returns immediately).

        In production this would:
        1. Serialize task.input_contract to JSON
        2. Launch container with sandbox_policy constraints
        3. Mount input as read-only, collect output
        4. Validate output against expected contract
        5. Return SubAgentResult
        """
        t0 = time.time()
        # Stub: no real execution, just return success with metadata
        duration = int((time.time() - t0) * 1000)
        return SubAgentResult(
            status='ok',
            output={},
            findings=[],
            risk_level='low',
            requires_human_review=False,
            duration_ms=duration,
            sandbox_meta=self.describe(task.subagent_type, task.task_id, task.parent_task_id),
        )


def get_subagent_runtime(backend='docker'):
    return SubAgentRuntime(backend=backend)


__all__ = [
    'SubAgentTask', 'SubAgentResult', 'SubAgentRuntime',
    'SandboxPolicy', 'get_subagent_runtime',
]
