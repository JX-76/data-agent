# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'src')
if SRC not in sys.path: sys.path.insert(0, SRC)
from workspace_control_plane import WorkspaceControlPlane, WorkspaceError


def _workspace():
    root = tempfile.mkdtemp()
    with open(os.path.join(root, 'report.txt'), 'w') as handle: handle.write('version one')
    with open(os.path.join(root, '.env'), 'w') as handle: handle.write('SECRET=no')
    os.mkdir(os.path.join(root, 'src'))
    return root, WorkspaceControlPlane(root, clock=lambda: 100.0)


def test_tree_preview_and_download_are_confined_and_hide_sensitive_files():
    root, plane = _workspace()
    tree = plane.tree()
    assert tree['contract'] == 'workspace_control_plane_v1'
    assert [entry['name'] for entry in tree['entries']] == ['src', 'report.txt']
    assert plane.file_view('report.txt')['content'] == 'version one'
    assert plane.download_path('report.txt')[0].startswith(root)
    for path in ('.env', '../report.txt', 'missing/../../report.txt'):
        try: plane.resolve(path)
        except WorkspaceError as exc: assert exc.code in ('sensitive_path_denied', 'workspace_path_denied')
        else: assert False, 'expected denied path'


def test_mode_activity_and_versioned_round_output_are_auditable_and_immutable():
    root, plane = _workspace()
    mode_event = plane.set_mode('session-a', 'auto', actor='user-a')
    assert mode_event['type'] == 'mode_change'
    assert plane.activities('session-a')['mode'] == 'auto'
    first = plane.begin_round('session-a', 'first change', 'trace-a')
    output = plane.add_round_output('session-a', first['round_id'], 'report.txt')
    with open(os.path.join(root, 'report.txt'), 'w') as handle: handle.write('version two')
    saved, name = plane.output_download_path('session-a', first['round_id'], output['output_id'])
    with open(saved) as handle: assert handle.read() == 'version one'
    assert name == output['version_name']
    assert plane.rounds('session-a')['rounds'][0]['outputs'][0]['sha256'] == output['sha256']


def test_invalid_mode_fails_closed():
    _, plane = _workspace()
    try: plane.set_mode('session-a', 'unsafe')
    except WorkspaceError as exc: assert exc.code == 'invalid_mode'
    else: assert False, 'expected invalid mode to fail'
