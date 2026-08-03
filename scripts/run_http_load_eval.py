# -*- coding: utf-8 -*-
"""Run an isolated real-HTTP smoke and cached-load evaluation for src/server.py.

The server is started as a subprocess, polled through /health, then tested with
one uncached request and 1,000 cached /query requests at staged concurrency.
The subprocess is always terminated and the report is written to harness/reports.
"""
from __future__ import print_function

import concurrent.futures
import json
import os
import statistics
import subprocess
import sys
import time

try:
    from urllib import request as urllib_request
except ImportError:
    import urllib2 as urllib_request

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
REPORT = os.path.join(ROOT, "harness", "reports", "http_load_eval_report.json")
PORT = int(os.environ.get("DATA_AGENT_HTTP_EVAL_PORT", "18080"))
BASE_URL = "http://127.0.0.1:%s" % PORT


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


def request_json(path, payload=None, timeout=30):
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib_request.Request(BASE_URL + path, data=data, headers={"Content-Type": "application/json"})
    start = time.time()
    try:
        resp = urllib_request.urlopen(req, timeout=timeout)
        raw = resp.read().decode("utf-8")
        return {"ok": True, "status_code": resp.getcode(), "body": json.loads(raw), "latency_ms": (time.time() - start) * 1000}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:300], "latency_ms": (time.time() - start) * 1000}


def wait_for_health():
    deadline = time.time() + 25
    last = None
    while time.time() < deadline:
        last = request_json("/health", timeout=2)
        if last.get("ok") and last.get("body", {}).get("status") == "healthy":
            return last
        time.sleep(0.25)
    raise RuntimeError("server did not become healthy: %s" % last)


def run_stage(request_count, concurrency):
    started = time.time()
    def one(_):
        return request_json("/query", {"query": "昨天GMV是多少", "use_llm": False, "cache_ttl": 300}, timeout=30)
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
        rows = list(pool.map(one, range(request_count)))
    elapsed = time.time() - started
    good = [row for row in rows if row.get("ok") and row.get("status_code") == 200 and row.get("body", {}).get("status") == "ok"]
    return {
        "request_count": request_count,
        "concurrency": concurrency,
        "success_count": len(good),
        "success_rate": round(len(good) / float(request_count), 4),
        "elapsed_seconds": round(elapsed, 3),
        "throughput_rps": round(request_count / elapsed, 3) if elapsed else 0.0,
        "latency": latency([row["latency_ms"] for row in rows]),
        "failures": [{"error": row.get("error"), "status_code": row.get("status_code"), "body_status": row.get("body", {}).get("status")} for row in rows if row not in good][:20],
    }


def main():
    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    env = dict(os.environ)
    env.update({"DATA_AGENT_PROFILE": "dev", "DATA_AGENT_AUTH": "false", "PYTHONPATH": os.path.join(ROOT, "src")})
    command = [sys.executable, "-m", "uvicorn", "server:app", "--app-dir", os.path.join(ROOT, "src"), "--host", "127.0.0.1", "--port", str(PORT), "--log-level", "warning"]
    process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    report = {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "mode": "real_http_server_load", "server_command": command, "target": BASE_URL, "requested_total": 1000}
    try:
        report["health"] = wait_for_health()
        report["uncached_smoke"] = request_json("/query", {"query": "昨天GMV是多少", "use_llm": False, "cache_ttl": 300})
        report["stages"] = [run_stage(100, 1), run_stage(300, 5), run_stage(600, 20)]
        report["status"] = "executed"
    except Exception as exc:
        report["status"] = "failed"
        report["error"] = str(exc)[:500]
    finally:
        process.terminate()
        try:
            out, err = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            out, err = process.communicate()
        report["server_exit_code"] = process.returncode
        report["server_stderr_tail"] = err.decode("utf-8", "replace")[-2000:]
        report["server_stdout_tail"] = out.decode("utf-8", "replace")[-1000:]
    with open(REPORT, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, sort_keys=True)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report.get("status") == "executed" else 1


if __name__ == "__main__":
    sys.exit(main())
