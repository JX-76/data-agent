# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import os, sys
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__))); SRC=os.path.join(ROOT,'src')
if SRC not in sys.path: sys.path.insert(0,SRC)
from durable_task_control_plane import DurableTaskControlPlane, InMemoryTaskRepository, TaskRecord
from async_event_runtime import TaskStatusAdapter, InMemoryTaskEventStream, AsyncRuntimeMetrics


def _plane(): return DurableTaskControlPlane(repository=InMemoryTaskRepository(), clock=lambda: 100.0)


def test_status_api_is_snapshot_over_durable_task_not_connection_lifecycle():
    plane=_plane(); record,_=plane.submit(TaskRecord(task_id='task-1', case_id='case-1', task_type='long', budget={'tenant_id':'t1'}))
    adapter=TaskStatusAdapter(plane)
    snap=adapter.get('task-1', {'tenant_id':'t1'})
    assert snap['contract']=='task_status_api_v1' and snap['found'] is True
    assert snap['status']['contract']=='durable_task_status_v1' and snap['status']['state']=='queued'
    denied=adapter.get('task-1', {'tenant_id':'other'})
    assert denied['found'] is False and denied['denied']=='tenant_scope'


def test_event_stream_supports_last_event_id_replay_and_heartbeat():
    stream=InMemoryTaskEventStream(clock=lambda: 1.0)
    e1=stream.publish('task-1','queued',{'state':'queued'}); e2=stream.publish('task-1','running',{'state':'running'}); stream.publish('task-2','queued',{})
    replay=stream.observe('task-1', last_event_id=e1['event_id'], heartbeat=True, now=2)
    assert [e['event'] for e in replay] == ['running','heartbeat']
    assert replay[0]['event_id']==e2['event_id'] and replay[-1]['event_id'] is None
    formatted=stream.format_sse(e2)
    assert 'id: 2' in formatted and 'event: running' in formatted and 'data:' in formatted


def test_disconnect_does_not_cancel_task_and_status_remains_available():
    plane=_plane(); plane.submit(TaskRecord(task_id='task-2', case_id='case-1'))
    stream=InMemoryTaskEventStream(); stream.publish('task-2','queued',{})
    first=stream.observe('task-2', heartbeat=True)
    # Simulate client disconnect by simply not consuming stream; no call to cancel.
    after=TaskStatusAdapter(plane).get('task-2')
    assert first[-1]['event']=='heartbeat'
    assert after['status']['state']=='queued'


def test_cancel_is_independent_authorized_audited_api():
    plane=_plane(); plane.submit(TaskRecord(task_id='task-3', case_id='case-1'))
    adapter=TaskStatusAdapter(plane)
    denied=adapter.cancel('task-3', {'authorized_cancel':False})
    assert denied['allowed'] is False
    ok=adapter.cancel('task-3', {'authorized_cancel':True}, now=101)
    assert ok['allowed'] is True and ok['state']=='cancelled' and ok['audit_required'] is True
    assert adapter.get('task-3')['status']['state']=='cancelled'


def test_async_runtime_metrics_are_contract_only_not_live_cluster_claims():
    metrics=AsyncRuntimeMetrics().snapshot(queued=5, running=2, worker_concurrency=3, rejected=1)
    assert metrics['contract']=='async_runtime_metrics_v1'
    assert metrics['task_queue_depth']==5 and metrics['worker_concurrency']==3
    assert metrics['autoscaling_source']=='contract_only_not_live_cluster_metric'
