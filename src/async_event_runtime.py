# -*- coding: utf-8 -*-
"""P5 task-status and resumable event-stream reference adapter.

SSE is an observation transport only: records remain owned by the P1 durable
control plane.  This in-memory adapter models event IDs, replay, heartbeat and
status snapshots; it is not a production broker or HTTP server.
"""
from __future__ import unicode_literals
import json
import time

TASK_EVENT_CONTRACT = 'task_event_v1'
TASK_STATUS_API_CONTRACT = 'task_status_api_v1'
ASYNC_METRICS_CONTRACT = 'async_runtime_metrics_v1'


def _now(value=None): return float(time.time() if value is None else value)


class TaskStatusAdapter(object):
    def __init__(self, control_plane): self.control_plane = control_plane
    def get(self, task_id, requester=None):
        status = self.control_plane.status_snapshot(task_id)
        if status is None:
            return {'contract': TASK_STATUS_API_CONTRACT, 'task_id': task_id, 'found': False, 'status': None}
        req = requester or {}
        tenant = req.get('tenant_id'); expected_tenant = (status.get('budget') or {}).get('tenant_id')
        if tenant and expected_tenant and tenant != expected_tenant:
            return {'contract': TASK_STATUS_API_CONTRACT, 'task_id': task_id, 'found': False, 'status': None, 'denied': 'tenant_scope'}
        return {'contract': TASK_STATUS_API_CONTRACT, 'task_id': task_id, 'found': True, 'status': status}
    def cancel(self, task_id, requester=None, now=None):
        req = requester or {}
        if not req.get('authorized_cancel'):
            return {'allowed': False, 'reason': 'cancel_not_authorized', 'task_id': task_id}
        record = self.control_plane.cancel(task_id, now=now)
        return {'allowed': True, 'task_id': task_id, 'state': record.state, 'audit_required': True}


class InMemoryTaskEventStream(object):
    """Append-only event log with Last-Event-ID replay semantics."""
    def __init__(self, clock=None): self.clock = clock or time.time; self.events = []; self.sequence = 0
    def publish(self, task_id, event_type, data=None, now=None):
        self.sequence += 1
        event = {'contract': TASK_EVENT_CONTRACT, 'event_id': str(self.sequence), 'task_id': task_id,
                 'event': event_type, 'data': dict(data or {}), 'created_at': _now(now if now is not None else self.clock())}
        self.events.append(event); return dict(event)
    def events_after(self, task_id, last_event_id=None):
        try: last = int(last_event_id or 0)
        except (TypeError, ValueError): last = 0
        return [dict(event) for event in self.events if event['task_id'] == task_id and int(event['event_id']) > last]
    def observe(self, task_id, last_event_id=None, heartbeat=True, now=None):
        output = self.events_after(task_id, last_event_id)
        if heartbeat:
            output.append({'contract': TASK_EVENT_CONTRACT, 'event_id': None, 'task_id': task_id, 'event': 'heartbeat', 'data': {}, 'created_at': _now(now if now is not None else self.clock())})
        return output
    def format_sse(self, event):
        lines = []
        if event.get('event_id') is not None: lines.append('id: %s' % event['event_id'])
        lines.append('event: %s' % event['event'])
        lines.append('data: %s' % json.dumps(event.get('data') or {}, sort_keys=True, ensure_ascii=True))
        return '\n'.join(lines) + '\n\n'


class AsyncRuntimeMetrics(object):
    def snapshot(self, queued=0, running=0, worker_concurrency=0, rejected=0):
        return {'contract': ASYNC_METRICS_CONTRACT, 'task_queue_depth': int(queued), 'running_tasks': int(running),
                'worker_concurrency': int(worker_concurrency), 'backpressure_rejections': int(rejected),
                'autoscaling_source': 'contract_only_not_live_cluster_metric'}


__all__ = ['TaskStatusAdapter', 'InMemoryTaskEventStream', 'AsyncRuntimeMetrics', 'TASK_EVENT_CONTRACT', 'TASK_STATUS_API_CONTRACT', 'ASYNC_METRICS_CONTRACT']
