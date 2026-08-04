# -*- coding: utf-8 -*-
"""Run an isolated real-HTTP smoke and cached-load evaluation for src/server.py.

The server is started as a subprocess, polled through /health, then tested with
one uncached request and staged cached /query requests.  The runner is designed
to be safe for local/CI use:

- staged plan is configurable via DATA_AGENT_HTTP_EVAL_STAGES;
- each HTTP request has a bounded timeout;
- the full run has a bounded deadline;
- server stdout/stderr are written to temp files to avoid pipe-buffer deadlock;
- the spawned server process tree is cleaned up on exit;
- progress and final console output are ASCII-safe for Windows cmd/PowerShell.

Default stages retain the historical 1,000-request evaluation.  Smaller stages
should be used for smoke tests and must not be reported as capacity evidence.
"""
from __future__ import print_function

import concurrent.futures
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time

try:
    from urllib import request as urllib_request
except ImportError:
    import urllib2 as urllib_request

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
REPORT = os.path.join(ROOT, "harness", "reports", "http_load_eval_report.json")
PORT = int(os.environ.get("DATA_AGENT_HTTP_EVAL_PORT", "18080"))
BASE_URL = "http://127.0.0.1:%s" % PORT
REQUEST_TIMEOUT_SECONDS = float(os.environ.get("DATA_AGENT_HTTP_EVAL_REQUEST_TIMEOUT", "30"))
TOTAL_TIMEOUT_SECONDS = float(os.environ.get("DATA_AGENT_HTTP_EVAL_TOTAL_TIMEOUT", "900"))
HEALTH_TIMEOUT_SECONDS = float(os.environ.get("DATA_AGENT_HTTP_EVAL_HEALTH_TIMEOUT", "45"))


def _stage_plan():
    """Return an environment-configurable staged load plan.

    Defaults retain the historical 1,000-request evaluation.  Smaller plans let
    CI and local smoke validation detect server-path regressions without claiming
    capacity results.  Format: ``count:concurrency,count:concurrency``.
    """
    raw = os.environ.get("DATA_AGENT_HTTP_EVAL_STAGES", "100:1,300:5,600:20")
    stages = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        count, concurrency = item.split(":", 1)
        count, concurrency = int(count), int(concurrency)
        if count < 1 or concurrency < 1:
            raise ValueError("each HTTP evaluation stage must be positive: %s" % item)
        stages.append((count, concurrency))
    if not stages:
        raise ValueError("DATA_AGENT_HTTP_EVAL_STAGES produced no stages")
    return stages


def _now_ms():
    return int(time.time() * 1000)


def _progress(event, **fields):
    payload = {"event": event, "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
    payload.update(fields)
    print(json.dumps(payload, ensure_ascii=True, sort_keys=True))
    try:
        sys.stdout.flush()
    except Exception:
        pass


def _tail_file(path, limit):
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - limit), os.SEEK_SET)
            return handle.read().decode("utf-8", "replace")
    except Exception as exc:
        return "<failed to read %s: %s>" % (path, exc)


def _terminate_process_tree(process):
    """Terminate child server and descendants; never raise from cleanup."""
    if process is None:
        return
    try:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=8)
            except Exception:
                pass
    except Exception:
        pass
    try:
        if process.poll() is None and os.name == "nt":
            subprocess.call(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif process.poll() is None:
            process.kill()
    except Exception:
        pass
    try:
        if process.poll() is None:
            process.wait(timeout=5)
    except Exception:
        pass


def percentile(values, p):
    if not values:
        return 0.0
    values = sorted(values)
    return round(values[int(round((len(values) - 1) * p / 100.0))], 3)


def latency(values):
    return {
        "count": len(values),
        "avg_ms": round(statistics.mean(values), 3) if values else 0.0,
        "p50_ms": percentile(values, 50),
        "p95_ms": percentile(values, 95),
        "p99_ms": percentile(values, 99),
        "max_ms": round(max(values), 3) if values else 0.0,
    }


def request_json(path, payload=None, timeout=None):
    timeout = timeout or REQUEST_TIMEOUT_SECONDS
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib_request.Request(BASE_URL + path, data=data, headers={"Content-Type": "application/json"})
    start = time.time()
    try:
        resp = urllib_request.urlopen(req, timeout=timeout)
        raw = resp.read().decode("utf-8")
        return {"ok": True, "status_code": resp.getcode(), "body": json.loads(raw), "latency_ms": (time.time() - start) * 1000}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:300], "latency_ms": (time.time() - start) * 1000}


def wait_for_health(deadline_at):
    deadline = min(time.time() + HEALTH_TIMEOUT_SECONDS, deadline_at)
    last = None
    while time.time() < deadline:
        last = request_json("/health", timeout=min(2.0, max(0.1, deadline - time.time())))
        if last.get("ok") and last.get("body", {}).get("status") == "healthy":
            return last
        time.sleep(0.25)
    raise RuntimeError("server did not become healthy: %s" % last)


def _assert_deadline(deadline_at, stage_name):
    if time.time() >= deadline_at:
        raise RuntimeError("http load eval timed out before %s after %.1fs" % (stage_name, TOTAL_TIMEOUT_SECONDS))


def run_stage(request_count, concurrency, deadline_at):
    _assert_deadline(deadline_at, "stage_%s_%s" % (request_count, concurrency))
    started = time.time()

    def one(_):
        remaining = max(0.1, deadline_at - time.time())
        timeout = min(REQUEST_TIMEOUT_SECONDS, remaining)
        return request_json("/query", {"query": "昨天GMV是多少", "use_llm": False, "cache_ttl": 300}, timeout=timeout)

    _progress("http_stage_start", request_count=request_count, concurrency=concurrency)
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
        rows = list(pool.map(one, range(request_count)))
    elapsed = time.time() - started
    good = [row for row in rows if row.get("ok") and row.get("status_code") == 200 and row.get("body", {}).get("status") == "ok"]
    stage = {
        "request_count": request_count,
        "concurrency": concurrency,
        "success_count": len(good),
        "success_rate": round(len(good) / float(request_count), 4),
        "elapsed_seconds": round(elapsed, 3),
        "throughput_rps": round(request_count / elapsed, 3) if elapsed else 0.0,
        "latency": latency([row["latency_ms"] for row in rows]),
        "failures": [{"error": row.get("error"), "status_code": row.get("status_code"), "body_status": row.get("body", {}).get("status")} for row in rows if row not in good][:20],
    }
    _progress("http_stage_complete", request_count=request_count, concurrency=concurrency,
              success_rate=stage["success_rate"], p95_ms=stage["latency"].get("p95_ms"),
              throughput_rps=stage["throughput_rps"])
    return stage


def main():
    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    env = dict(os.environ)
    env.update({"DATA_AGENT_PROFILE": "dev", "DATA_AGENT_AUTH": "false", "PYTHONPATH": os.path.join(ROOT, "src")})
    command = [sys.executable, "-m", "uvicorn", "server:app", "--app-dir", os.path.join(ROOT, "src"), "--host", "127.0.0.1", "--port", str(PORT), "--log-level", "warning"]
    plan = _stage_plan()
    deadline_at = time.time() + TOTAL_TIMEOUT_SECONDS
    stdout_path = os.path.join(tempfile.gettempdir(), "data_agent_http_eval_%s_stdout.log" % PORT)
    stderr_path = os.path.join(tempfile.gettempdir(), "data_agent_http_eval_%s_stderr.log" % PORT)
    stdout_handle = open(stdout_path, "wb")
    stderr_handle = open(stderr_path, "wb")
    process = None
    report = {
        "contract": "http_load_eval_report_v2",
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "mode": "real_http_server_load",
        "server_command": command,
        "target": BASE_URL,
        "requested_total": sum(item[0] for item in plan),
        "stage_plan": [{"request_count": item[0], "concurrency": item[1]} for item in plan],
        "timeouts": {"request_seconds": REQUEST_TIMEOUT_SECONDS, "total_seconds": TOTAL_TIMEOUT_SECONDS, "health_seconds": HEALTH_TIMEOUT_SECONDS},
        "server_log_files": {"stdout": stdout_path, "stderr": stderr_path},
    }
    started_ms = _now_ms()
    try:
        _progress("http_eval_start", target=BASE_URL, requested_total=report["requested_total"], total_timeout_seconds=TOTAL_TIMEOUT_SECONDS)
        process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=stdout_handle, stderr=stderr_handle)
        report["server_pid"] = process.pid
        report["health"] = wait_for_health(deadline_at)
        _assert_deadline(deadline_at, "uncached_smoke")
        report["uncached_smoke"] = request_json("/query", {"query": "昨天GMV是多少", "use_llm": False, "cache_ttl": 300}, timeout=min(REQUEST_TIMEOUT_SECONDS, max(0.1, deadline_at - time.time())))
        report["stages"] = [run_stage(request_count, concurrency, deadline_at) for request_count, concurrency in plan]
        report["status"] = "executed"
    except Exception as exc:
        report["status"] = "failed"
        report["error"] = str(exc)[:500]
    finally:
        report["elapsed_ms"] = _now_ms() - started_ms
        _terminate_process_tree(process)
        try:
            stdout_handle.close()
            stderr_handle.close()
        except Exception:
            pass
        report["server_exit_code"] = process.returncode if process is not None else None
        report["server_stderr_tail"] = _tail_file(stderr_path, 4000)
        report["server_stdout_tail"] = _tail_file(stdout_path, 4000)
    with open(REPORT, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, sort_keys=True)
    print(json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True))
    return 0 if report.get("status") == "executed" else 1


if __name__ == "__main__":
    sys.exit(main())
