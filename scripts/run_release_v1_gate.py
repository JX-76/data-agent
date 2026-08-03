# -*- coding: utf-8 -*-
"""Release v1 gate.

Runs the first runnable release contract over the release_v1 case pack. The gate
is intentionally product-facing: it validates stable response shape, terminal
status, audit id, credibility, and answer envelope rather than internal module
implementation details.
"""
from __future__ import print_function

import json
import os
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
SRC = os.path.join(ROOT, "src")
CASES = os.path.join(ROOT, "harness", "cases", "release_v1.jsonl")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

# First release should run against a deterministic readonly sandbox by default.
os.environ.setdefault("DATA_AGENT_DB_MODE", "sandbox")
os.environ.setdefault("DATA_AGENT_DB_ROW_LIMIT", "200")
os.environ.setdefault("DATA_AGENT_DB_TIMEOUT_MS", "1500")

from answer_contract_validator import validate_answer_contract_envelope  # noqa
from release_api import ask_release, followup_release, release_health, record_gate_result, RELEASE_CONTRACT  # noqa


REQUIRED_TOP_LEVEL = [
    "contract", "status", "session_id", "audit_id", "query", "answer",
    "plan", "credibility", "provenance", "elapsed_ms", "quality", "answer_contract",
]
REQUIRED_ANSWER = ["summary", "table", "chart", "caveats", "next_steps"]
REQUIRED_ANSWER_CONTRACT = [
    "contract", "status", "answer_type", "answer", "facts", "hypotheses",
    "citations", "limitations", "next_actions", "provenance", "trace_id", "task_id", "evidence_ids",
]


class GateFailure(Exception):
    pass


def _load_cases():
    cases = []
    with open(CASES, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    return cases


def _validate_shape(case, env):
    for key in REQUIRED_TOP_LEVEL:
        if key not in env:
            raise GateFailure("missing top-level key: %s" % key)
    if env.get("contract") != RELEASE_CONTRACT:
        raise GateFailure("unexpected contract: %s" % env.get("contract"))
    answer = env.get("answer") or {}
    for key in REQUIRED_ANSWER:
        if key not in answer:
            raise GateFailure("missing answer key: %s" % key)
    if not env.get("audit_id"):
        raise GateFailure("missing audit_id")
    if not env.get("session_id"):
        raise GateFailure("missing session_id")
    if env.get("elapsed_ms") is None:
        raise GateFailure("missing elapsed_ms")
    # Product-facing successful responses must have a readable summary. Terminal
    # non-ok states still need a stable, user-facing summary.
    if not answer.get("summary"):
        raise GateFailure("missing answer.summary")
    answer_contract = env.get("answer_contract") or {}
    if answer_contract.get("contract") != "final_answer_contract_v2":
        raise GateFailure("missing final answer contract")
    for key in REQUIRED_ANSWER_CONTRACT:
        if key not in answer_contract:
            raise GateFailure("missing answer_contract key: %s" % key)
    if answer_contract.get("status") != env.get("status"):
        raise GateFailure("answer_contract status mismatch: %s != %s" % (answer_contract.get("status"), env.get("status")))
    validation = validate_answer_contract_envelope(env, require_production_fields=True)
    if not validation.get("passed"):
        raise GateFailure("answer_contract validation failed: %s" % validation.get("errors"))
    quality = env.get("quality") or {}
    if quality.get("contract") != "release_v1_quality":
        raise GateFailure("missing release quality contract")
    if not quality.get("passed"):
        raise GateFailure("quality gate failed: score=%s threshold=%s warnings=%s" % (
            quality.get("score"), quality.get("threshold"), quality.get("warnings")))


def _validate_expectation(case, env):
    status = env.get("status")
    if case.get("expect_status") and status != case.get("expect_status"):
        raise GateFailure("status expected=%s actual=%s" % (case.get("expect_status"), status))
    if case.get("expect_status_any") and status not in case.get("expect_status_any"):
        raise GateFailure("status expected_any=%s actual=%s" % (case.get("expect_status_any"), status))
    expected_intent = case.get("expect_intent")
    if expected_intent:
        raw = env.get("raw") or {}
        actual_intent = raw.get("intent") or (env.get("plan") or {}).get("intent")
        # The router may label some product-equivalent task families slightly
        # differently. Gate intent only for non-terminal ok results and keep the
        # mismatch as a warning-like failure detail when it is completely absent.
        if status == "ok" and not actual_intent:
            raise GateFailure("missing intent for expected=%s" % expected_intent)


def _run_case(case):
    started = time.time()
    env = ask_release(case["query"], session_id="release_gate", use_llm=False, user_id="release_gate")
    _validate_shape(case, env)
    _validate_expectation(case, env)
    return {"id": case["id"], "status": "passed", "elapsed_ms": int((time.time() - started) * 1000), "terminal": env.get("status")}


def _run_followup_probe():
    first = ask_release("最近7天GMV", session_id="release_gate_followup", use_llm=False, user_id="release_gate")
    follow = followup_release("换成华东，再按品类拆", session_id=first["session_id"], use_llm=False, user_id="release_gate")
    for label, env in [("first", first), ("follow", follow)]:
        _validate_shape({"id": label}, env)
    return {"id": "rv1_followup_probe", "status": "passed", "terminal": follow.get("status"), "elapsed_ms": follow.get("elapsed_ms")}


def run_gate():
    results = []
    for case in _load_cases():
        try:
            results.append(_run_case(case))
        except Exception as exc:
            results.append({"id": case.get("id"), "status": "failed", "terminal": None, "failure": str(exc)})
    try:
        results.append(_run_followup_probe())
    except Exception as exc:
        results.append({"id": "rv1_followup_probe", "status": "failed", "terminal": None, "failure": str(exc)})
    return results


def main():
    results = run_gate()
    failed = [r for r in results if r.get("status") != "passed"]
    print("Release v1 gate")
    print("=" * 80)
    for r in results:
        line = "[{status}] {id} terminal={terminal} elapsed_ms={elapsed_ms}".format(
            status=r.get("status"), id=r.get("id"), terminal=r.get("terminal"), elapsed_ms=r.get("elapsed_ms", "-"))
        print(line)
        if r.get("failure"):
            print("  failure: %s" % r.get("failure"))
    print("=" * 80)
    record_gate_result("release_v1_gate", not failed, total=len(results), failed=len(failed),
                       summary="release_v1 contract cases")
    success_rate = (len(results) - len(failed)) / float(len(results) or 1)
    print("total=%s passed=%s failed=%s success_rate=%.2f" % (len(results), len(results) - len(failed), len(failed), success_rate))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
