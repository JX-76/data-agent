#!/usr/bin/env python3
"""LLM Router evaluation runner.

Runs tests from evals/llm_router_tests.yaml. Tests validate structure
(non-deterministic LLM output) rather than exact values.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[0] / "src"))
from mvp_agent import run as _run, load_semantic_layer as _load_sem
from llm_router import llm_route_and_plan as _llm_plan

try:
    import yaml
except ImportError:
    print("PyYAML required")
    sys.exit(1)

BASE = Path(__file__).resolve().parents[0]


def eval_llm_router(path):
    with open(BASE / path, "r") as f:
        data = yaml.safe_load(f)

    failures = []
    total = 0
    passed = 0

    for case in data["cases"]:
        cid = case["id"]
        mode = case.get("mode", "llm_only")
        check = case["check"]
        errors = []

        if mode == "llm_only":
            plan = _llm_plan(case["query"])
            _check_plan(cid, plan, check, errors)
        elif mode == "llm_db":
            out = _run(case["query"], use_llm=True, use_db=True)
            plan = out["plan"]
            _check_plan(f"{cid}.plan", plan, check, errors)
            if check.get("has_results"):
                results = out.get("results", [])
                if not results:
                    errors.append("expected results, got empty")
                if check.get("results_has_key"):
                    for r in results:
                        for k in check["results_has_key"]:
                            if k not in r:
                                errors.append(f"result missing key: {k}")
            if check.get("valid_ok") and not out.get("valid", {}).get("ok"):
                errors.append(f"sql invalid: {out.get('valid')}")

        if errors:
            failures.append((cid, errors))
        else:
            print(f"PASS {cid}")
            passed += 1
        total += 1

    if failures:
        print("\nFAILURES:")
        for cid, errors in failures:
            print(f"  {cid}: {errors}")
        return 1
    print(f"\nALL PASSED: {passed}/{total} cases")
    return 0


def _check_plan(cid, plan, check, errors):
    for k in check.get("has_keys", []):
        if k not in plan:
            errors.append(f"missing key: {k}")
    for k in ["status", "intent", "metric"]:
        if k in check and plan.get(k) != check[k]:
            errors.append(f"{k}: expected {check[k]}, got {plan.get(k)}")
    dc = check.get("dimensions_contains", [])
    if dc:
        dims = plan.get("dimensions", [])
        for d in dc:
            if d not in dims:
                errors.append(f"dimensions missing: {d}")


if __name__ == "__main__":
    p = sys.argv[1] if len(sys.argv) > 1 else "evals/llm_router_tests.yaml"
    sys.exit(eval_llm_router(p))
