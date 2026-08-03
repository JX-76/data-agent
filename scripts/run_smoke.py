# -*- coding: utf-8 -*-
"""Production smoke runner for core Data Agent checks.

This runner intentionally does not require pytest. It executes a small set of
script-style tests and returns a non-zero exit code when any smoke case fails.
"""

from __future__ import print_function

import os
import subprocess
import sys
import time


try:
    unicode
except NameError:  # pragma: no cover - Python 3 compatibility
    unicode = str


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
SRC = os.path.join(ROOT, "src")


PY_COMPILE_TARGETS = [
    "src/contracts.py",
    "src/agent_facade.py",
    "src/execution_engine.py",
    "src/execution_strategies.py",
    "src/db_adapter.py",
    "src/db_factory.py",
    "src/schema_introspector.py",
    "src/semantic_registry.py",
    "src/eval_baseline.py",
    "src/report_generator.py",
    "src/chart_spec.py",
    "src/chart_policy.py",
    "src/result_explainer.py",
    "src/followup_policy.py",
    "src/session.py",
    "src/advanced_analysis.py",
    "src/task_capabilities.py",
    "src/analysis_strategies.py",
    "src/audit_logger.py",
    "src/governance.py",
    "src/identity.py",
    "src/secret_scan.py",
    "src/observability.py",
]

SCRIPT_CASES = [
    ("contracts", "tests/test_contracts.py"),
    ("agent_facade", "tests/test_agent_facade.py"),
    ("db_adapter", "tests/test_db_adapter.py"),
    ("db_factory", "tests/test_db_factory.py"),
    ("execution_retry", "tests/test_execution_retry.py"),
    ("semantic_registry", "tests/test_semantic_registry.py"),
    ("eval_baseline", "tests/test_eval_baseline.py"),
    ("report_generator", "tests/test_report_generator.py"),
    ("chart_spec", "tests/test_chart_spec.py"),
    ("multiturn_session", "tests/test_multiturn_session.py"),
    ("advanced_analysis", "tests/test_advanced_analysis.py"),
    ("task_capabilities", "tests/test_task_capabilities.py"),
    ("analysis_strategies", "tests/test_analysis_strategies.py"),
    ("audit_logger", "tests/test_audit_logger.py"),
    ("governance", "tests/test_governance.py"),
    ("production_gate", "tests/test_production_gate.py"),
    ("observability", "tests/test_observability.py"),
]


class SmokeResult(object):
    def __init__(self, case_name, status, elapsed_ms, failure_reason=""):
        self.case_name = case_name
        self.status = status
        self.elapsed_ms = elapsed_ms
        self.failure_reason = failure_reason

    def as_dict(self):
        return {
            "case_name": self.case_name,
            "status": self.status,
            "elapsed_ms": self.elapsed_ms,
            "failure_reason": self.failure_reason,
        }


def _run_command(case_name, args):
    start = time.time()
    try:
        proc = subprocess.Popen(
            args,
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
        )
        output, _ = proc.communicate()
        elapsed_ms = int((time.time() - start) * 1000)
        if proc.returncode == 0:
            return SmokeResult(case_name, "passed", elapsed_ms)
        return SmokeResult(case_name, "failed", elapsed_ms, output.strip())
    except Exception as exc:
        elapsed_ms = int((time.time() - start) * 1000)
        return SmokeResult(case_name, "failed", elapsed_ms, str(exc))


def run_py_compile():
    args = [sys.executable, "-m", "py_compile"] + PY_COMPILE_TARGETS
    return _run_command("py_compile_core", args)


def run_script_case(case_name, relpath):
    return _run_command(case_name, [sys.executable, relpath])


def run_smoke():
    results = [run_py_compile()]
    for case_name, relpath in SCRIPT_CASES:
        results.append(run_script_case(case_name, relpath))
    return results


def _to_unicode(value):
    if isinstance(value, unicode):
        return value
    try:
        return value.decode("utf-8", "replace")
    except AttributeError:
        return unicode(value)


def _safe_print(text):
    """Print without crashing on consoles that cannot encode the text.

    On Windows (GBK code page) failure output may contain characters the
    console encoding cannot represent, which raised IOError/UnicodeError and
    masked the real smoke result. Degrade to an ASCII-safe rendering instead.
    """
    text = _to_unicode(text)
    try:
        print(text)
    except (UnicodeError, IOError):
        encoding = getattr(sys.stdout, "encoding", None) or "ascii"
        safe = text.encode(encoding, "replace")
        try:
            print(safe)
        except (UnicodeError, IOError):
            sys.stdout.write(safe + "\n")


def print_summary(results):
    _safe_print("Data Agent smoke results")
    _safe_print("=" * 80)
    for item in results:
        line = "[{status}] {case_name} ({elapsed_ms} ms)".format(**item.as_dict())
        _safe_print(line)
        if item.failure_reason:
            _safe_print("  failure_reason:")
            _safe_print("  " + item.failure_reason.replace("\n", "\n  "))
    total = len(results)
    failed = len([r for r in results if r.status != "passed"])
    passed = total - failed
    _safe_print("=" * 80)
    _safe_print("total={0} passed={1} failed={2}".format(total, passed, failed))
    return failed


def main():
    failed = print_summary(run_smoke())
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
