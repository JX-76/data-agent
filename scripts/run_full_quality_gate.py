# -*- coding: utf-8 -*-
"""Full quality gate for Phase 1 baseline and regression control.

This runner intentionally aggregates a small set of existing gates rather than
adding new business logic. It is designed to be deterministic, product-facing,
and easy to replay in CI.

Execution reliability is part of the gate contract: every child gate has a
bounded timeout, incremental output forwarding, a retained output tail, and
best-effort subprocess-tree cleanup on timeout.  These controls do not change
the business pass/fail semantics of any child gate; they only make hangs and
infrastructure failures observable and recoverable.
"""
from __future__ import print_function

import json
import os
import subprocess
import sys
import threading
import time

try:  # Python 2/3 queue compatibility
    import Queue as queue_mod
except Exception:  # pragma: no cover
    import queue as queue_mod

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
PY = sys.executable or 'py'

DEFAULT_GATE_TIMEOUT_SECONDS = int(float(os.environ.get(
    'DATA_AGENT_FULL_GATE_TIMEOUT_SECONDS', '600') or 600))
OUTPUT_TAIL_LINES = int(os.environ.get('DATA_AGENT_FULL_GATE_OUTPUT_TAIL_LINES', '80') or 80)

CORE_PYTEST_ARGS = [
    '-m', 'pytest', '-q',
    'tests/test_contracts.py',
    'tests/test_agent_facade.py',
    'tests/test_report_generator.py',
    'tests/test_chart_spec.py',
    'tests/test_release_quality.py',
    'tests/test_strategy_evidence_p1.py',
    'tests/test_external_tool_governance_p1.py',
    'tests/test_permission_policy.py',
    'tests/test_masking_policy.py',
    'tests/test_release_api.py',
    'tests/test_production_runtime_readiness_s3.py',
    'tests/test_server_entrypoint_unification_p4.py',
    'tests/test_server_metadata_security_p5.py',
    'tests/test_server_legacy_ops_security_p7.py',
]

GATES = [
    {
        'name': 'core_pytest',
        'cmd': [PY] + CORE_PYTEST_ARGS,
        'cwd': ROOT,
        'timeout_seconds': int(os.environ.get('DATA_AGENT_CORE_PYTEST_TIMEOUT_SECONDS', DEFAULT_GATE_TIMEOUT_SECONDS) or DEFAULT_GATE_TIMEOUT_SECONDS),
    },
    {
        'name': 'release_v1_gate',
        'cmd': [PY, os.path.join(ROOT, 'scripts', 'run_release_v1_gate.py')],
        'cwd': ROOT,
        'timeout_seconds': DEFAULT_GATE_TIMEOUT_SECONDS,
    },
    {
        'name': 'agent_quality_gate',
        'cmd': [PY, os.path.join(ROOT, 'scripts', 'run_agent_quality_gate.py')],
        'cwd': ROOT,
        'timeout_seconds': DEFAULT_GATE_TIMEOUT_SECONDS,
    },
    {
        'name': 'case_control_plane_gate',
        'cmd': [PY, os.path.join(ROOT, 'scripts', 'run_case_control_plane_gate.py')],
        'cwd': ROOT,
        'timeout_seconds': DEFAULT_GATE_TIMEOUT_SECONDS,
    },
    {
        'name': 'external_tool_governance_gate',
        'cmd': [PY, os.path.join(ROOT, 'scripts', 'run_external_tool_governance_gate.py')],
        'cwd': ROOT,
        'timeout_seconds': DEFAULT_GATE_TIMEOUT_SECONDS,
    },
    {
        'name': 'multiturn_stress_50',
        'cmd': [PY, os.path.join(ROOT, 'scripts', 'run_multiturn_stress_50.py')],
        'cwd': ROOT,
        'timeout_seconds': DEFAULT_GATE_TIMEOUT_SECONDS,
    },
    {
        'name': 'release_100_gate',
        'cmd': [PY, os.path.join(ROOT, 'scripts', 'run_release_100_gate.py')],
        'cwd': ROOT,
        'timeout_seconds': DEFAULT_GATE_TIMEOUT_SECONDS,
    },
    {
        'name': 'production_control_plane_evaluation_gate',
        'cmd': [PY, os.path.join(ROOT, 'scripts', 'run_production_control_plane_evaluation_gate.py')],
        'cwd': ROOT,
        'timeout_seconds': DEFAULT_GATE_TIMEOUT_SECONDS,
    },
    {
        'name': 'analysis_control_plane_gate',
        'cmd': [PY, os.path.join(ROOT, 'scripts', 'run_analysis_control_plane_gate.py')],
        'cwd': ROOT,
        'timeout_seconds': DEFAULT_GATE_TIMEOUT_SECONDS,
    },
]


def _now_ms():
    return int(time.time() * 1000)


def _decode_output(value):
    if value is None:
        return ''
    if isinstance(value, bytes):
        return value.decode('utf-8', 'replace')
    return value


def _tail_lines(lines, limit=None):
    limit = OUTPUT_TAIL_LINES if limit is None else int(limit)
    if limit <= 0:
        return ''
    return ''.join(lines[-limit:])


def _reader_thread(stream, out_queue):
    try:
        while True:
            chunk = stream.readline()
            if not chunk:
                break
            out_queue.put(_decode_output(chunk))
    finally:
        try:
            stream.close()
        except Exception:
            pass


def _drain_queue(out_queue, output_lines, forward_output=True):
    drained = 0
    while True:
        try:
            text = out_queue.get_nowait()
        except queue_mod.Empty:
            break
        output_lines.append(text)
        drained += 1
        if forward_output:
            try:
                sys.stdout.write(text)
                sys.stdout.flush()
            except Exception:
                pass
    return drained


def _kill_process_tree(proc):
    """Best-effort subprocess-tree cleanup; returns a structured method record."""
    if proc is None or proc.poll() is not None:
        return {'method': 'already_exited', 'ok': True}
    pid = proc.pid
    if os.name == 'nt':
        cmd = ['taskkill', '/F', '/T', '/PID', str(pid)]
        try:
            killer = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            out = killer.communicate()[0]
            return {'method': 'taskkill_tree', 'ok': killer.returncode == 0,
                    'returncode': killer.returncode, 'output': _decode_output(out)}
        except Exception as exc:
            try:
                proc.kill()
            except Exception:
                pass
            return {'method': 'taskkill_tree_fallback_proc_kill', 'ok': False, 'error': str(exc)}
    try:
        os.killpg(proc.pid, 9)
        return {'method': 'killpg', 'ok': True}
    except Exception as exc:
        try:
            proc.kill()
            return {'method': 'proc_kill_fallback', 'ok': True, 'error': str(exc)}
        except Exception as exc2:
            return {'method': 'proc_kill_failed', 'ok': False, 'error': str(exc2)}


def _popen_kwargs(spec, env):
    kwargs = {
        'cwd': spec['cwd'],
        'env': env,
        'stdout': subprocess.PIPE,
        'stderr': subprocess.STDOUT,
    }
    if os.name == 'nt' and hasattr(subprocess, 'CREATE_NEW_PROCESS_GROUP'):
        kwargs['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP
    elif os.name != 'nt':
        try:
            kwargs['preexec_fn'] = os.setsid
        except Exception:
            pass
    return kwargs


def _run_gate(spec, timeout_seconds=None, forward_output=True):
    started = time.time()
    started_ms = _now_ms()
    env = os.environ.copy()
    env['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] = '1'
    if ROOT not in (env.get('PYTHONPATH') or ''):
        current = env.get('PYTHONPATH')
        src_path = os.path.join(ROOT, 'src')
        env['PYTHONPATH'] = src_path if not current else src_path + os.pathsep + current
    timeout_seconds = float(timeout_seconds if timeout_seconds is not None else spec.get('timeout_seconds', DEFAULT_GATE_TIMEOUT_SECONDS))
    out_queue = queue_mod.Queue()
    output_lines = []
    proc = None
    timed_out = False
    termination = None
    spawn_error = None

    if forward_output:
        print('FULL_QUALITY_GATE_CHILD_START name=%s timeout_seconds=%s cmd=%s' % (
            spec.get('name'), timeout_seconds, ' '.join([str(x) for x in spec.get('cmd') or []])))

    try:
        proc = subprocess.Popen(spec['cmd'], **_popen_kwargs(spec, env))
    except Exception as exc:
        spawn_error = str(exc)

    if spawn_error:
        elapsed_ms = int((time.time() - started) * 1000)
        return {
            'name': spec['name'], 'returncode': None, 'elapsed_ms': elapsed_ms,
            'started_ms': started_ms, 'ended_ms': _now_ms(), 'timeout_seconds': timeout_seconds,
            'timed_out': False, 'termination_method': None, 'output': '', 'last_output': '',
            'passed': False, 'failure_reason': 'spawn_failed', 'error': spawn_error,
        }

    reader = threading.Thread(target=_reader_thread, args=(proc.stdout, out_queue))
    reader.daemon = True
    reader.start()

    while True:
        _drain_queue(out_queue, output_lines, forward_output=forward_output)
        if proc.poll() is not None:
            break
        if timeout_seconds > 0 and (time.time() - started) >= timeout_seconds:
            timed_out = True
            termination = _kill_process_tree(proc)
            break
        time.sleep(0.05)

    # Allow buffered output to arrive after normal exit or process-tree cleanup.
    try:
        reader.join(1.0)
    except Exception:
        pass
    _drain_queue(out_queue, output_lines, forward_output=forward_output)

    if timed_out and proc.poll() is None:
        try:
            proc.wait()
        except Exception:
            pass

    elapsed_ms = int((time.time() - started) * 1000)
    output = ''.join(output_lines)
    failure_reason = None
    if timed_out:
        failure_reason = 'timeout'
    elif proc.returncode != 0:
        failure_reason = 'nonzero_exit'

    return {
        'name': spec['name'],
        'returncode': proc.returncode,
        'elapsed_ms': elapsed_ms,
        'started_ms': started_ms,
        'ended_ms': _now_ms(),
        'timeout_seconds': timeout_seconds,
        'timed_out': timed_out,
        'termination_method': termination,
        'output': output,
        'last_output': _tail_lines(output_lines),
        'passed': (not timed_out) and proc.returncode == 0,
        'failure_reason': failure_reason,
    }


def _extract_summary(text):
    lines = (text or '').splitlines()
    summary = None
    for line in lines:
        if (line.startswith('AGENT_QUALITY_GATE ') or line.startswith('CASE_CONTROL_PLANE_GATE ') or
                line.startswith('EXTERNAL_TOOL_GOVERNANCE_GATE ') or line.startswith('MULTITURN_STRESS ') or line.startswith('RELEASE_100_GATE ') or
                line.startswith('total=') or line.startswith('QUALITY_GATE_FAILED') or
                line.startswith('RELEASE_100_GATE_FAILED')):
            summary = line
    return summary or (lines[-1] if lines else '')


def _parse_args(argv):
    opts = {'only': None, 'timeout_seconds': None, 'forward_output': True}
    idx = 0
    while idx < len(argv):
        arg = argv[idx]
        if arg == '--only' and idx + 1 < len(argv):
            opts['only'] = argv[idx + 1]
            idx += 2
            continue
        if arg == '--timeout-seconds' and idx + 1 < len(argv):
            opts['timeout_seconds'] = float(argv[idx + 1])
            idx += 2
            continue
        if arg == '--no-forward-output':
            opts['forward_output'] = False
            idx += 1
            continue
        idx += 1
    return opts


def main(argv=None):
    opts = _parse_args(list(argv if argv is not None else sys.argv[1:]))
    gates = list(GATES)
    if opts.get('only'):
        wanted = set([x.strip() for x in opts['only'].split(',') if x.strip()])
        gates = [gate for gate in gates if gate.get('name') in wanted]

    results = []
    for gate in gates:
        results.append(_run_gate(gate, timeout_seconds=opts.get('timeout_seconds'),
                                 forward_output=opts.get('forward_output', True)))

    failed = [item for item in results if not item['passed']]
    report = {
        'suite': 'full_quality_gate',
        'timestamp_ms': int(time.time() * 1000),
        'total': len(results),
        'passed': len(results) - len(failed),
        'failed': len(failed),
        'results': [
            {
                'name': item['name'],
                'passed': item['passed'],
                'returncode': item['returncode'],
                'elapsed_ms': item['elapsed_ms'],
                'started_ms': item.get('started_ms'),
                'ended_ms': item.get('ended_ms'),
                'timeout_seconds': item.get('timeout_seconds'),
                'timed_out': item.get('timed_out', False),
                'failure_reason': item.get('failure_reason'),
                'termination_method': item.get('termination_method'),
                'summary': _extract_summary(item.get('output')),
                'last_output': item.get('last_output') or '',
            }
            for item in results
        ],
    }
    print('FULL_QUALITY_GATE')
    print('=' * 80)
    for item in report['results']:
        line = '[{status}] {name} returncode={code} elapsed_ms={elapsed_ms} timed_out={timed_out}'.format(
            status='passed' if item['passed'] else 'failed',
            name=item['name'],
            code=item['returncode'],
            elapsed_ms=item['elapsed_ms'],
            timed_out=item.get('timed_out'),
        )
        print(line)
        if item.get('failure_reason'):
            print('  failure_reason: %s' % item.get('failure_reason'))
        if item.get('summary'):
            print('  summary: %s' % item.get('summary'))
        if item.get('timed_out') and item.get('last_output'):
            print('  last_output: %s' % item.get('last_output')[-1000:])
    print('=' * 80)
    # Keep the machine-readable line portable to Windows consoles configured for GBK.
    print('FULL_QUALITY_GATE_REPORT %s' % json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
