# -*- coding: utf-8 -*-
"""Executable P0 final-output evidence-boundary gate.

Runs deterministic unit/contract tests and emits one machine-readable summary.
No external services or live data sources are required.
"""
from __future__ import print_function

import json
import os
import subprocess
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
TESTS = [
    os.path.join(ROOT, "tests", "test_final_output_evidence_gate.py"),
    os.path.join(ROOT, "tests", "test_final_output_evidence_gate_entrypoints.py"),
    os.path.join(ROOT, "tests", "test_release_api_m6_ecommerce_graph.py"),
]


def main():
    env = dict(os.environ)
    src = os.path.join(ROOT, "src")
    env["PYTHONPATH"] = src + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    cmd = [sys.executable, "-m", "pytest", "-p", "no:asyncio", "-q"] + TESTS
    started = time.time()
    try:
        proc = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, universal_newlines=True)
        output, _ = proc.communicate()
        returncode = proc.returncode
    except Exception as exc:
        output = "gate_launch_error: %s" % exc
        returncode = 1
    report = {
        "contract": "final_output_evidence_gate_report_v1",
        "gate": "final_output_evidence_gate",
        "passed": returncode == 0,
        "returncode": returncode,
        "elapsed_ms": int((time.time() - started) * 1000),
        "tests": [os.path.relpath(path, ROOT).replace("\\", "/") for path in TESTS],
        "output_tail": output[-4000:],
        "scope": ["release_api", "ecommerce_graph", "report_chart_wrapper", "legacy_fact_demotion"],
    }
    print("FINAL_OUTPUT_EVIDENCE_GATE %s" % json.dumps(report, ensure_ascii=True, sort_keys=True))
    return returncode


if __name__ == "__main__":
    sys.exit(main())
