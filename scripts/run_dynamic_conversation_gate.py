# -*- coding: utf-8 -*-
"""Run response-aware conversations from initial-only seed cases."""
from __future__ import print_function, unicode_literals
import argparse
import codecs
import json
import os
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from dynamic_conversation_dataset import load_initial_cases, summarize_initial_cases
from dynamic_conversation_runner import DynamicConversationRunner, evaluate_dynamic_runs, summarize_runs
import release_api

DEFAULT_CASES = os.path.join(ROOT, "evaluation", "conversation_cases", "ecommerce_dynamic_initial_questions_100.jsonl")
DEFAULT_OUTPUT = os.path.join(ROOT, "evaluation", "conversation_runs", "latest")


def write_json(path, value):
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    with codecs.open(path, "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)


def write_transcript(path, runs):
    with codecs.open(path, "w", encoding="utf-8") as handle:
        for run in runs:
            for turn in run.get("transcript") or []:
                handle.write(json.dumps(turn, ensure_ascii=False, sort_keys=True) + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", default=DEFAULT_CASES)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT)
    parser.add_argument("--limit", type=int, default=0, help="0 means all cases")
    parser.add_argument("--max-turns", type=int, default=10)
    parser.add_argument("--use-llm", action="store_true", help="Enable optional presentation assist")
    parser.add_argument("--provider", choices=("ollama", "deepseek"), default=None,
                        help="Override optional presentation provider for this process")
    parser.add_argument("--provider-retries", type=int, default=1)
    args = parser.parse_args(argv)
    if args.provider:
        # The adapter loads .env lazily; only provider selection needs to be
        # fixed before the first release call. No secret is exported.
        release_api._DEFAULT_LLM_PROVIDER = args.provider

    cases = load_initial_cases(args.cases)
    if args.limit:
        cases = cases[:max(0, args.limit)]
    for row in cases:
        row["max_turns"] = min(int(row.get("max_turns") or 10), max(1, min(args.max_turns, 10)))
    dataset = summarize_initial_cases(load_initial_cases(args.cases))
    runner = DynamicConversationRunner(release_api.ask_release, release_api.followup_release, resume_fn=release_api.resume_release,
                                       use_llm=args.use_llm,
                                       max_provider_retries=args.provider_retries)
    access = {"user_id": "dynamic-eval-user", "tenant_id": "dynamic-eval-tenant", "role": "analyst"}
    runs = [runner.run_case(row, access_context=access) for row in cases]
    report = summarize_runs(runs)
    quality = evaluate_dynamic_runs(cases, runs)
    report.update({"generated_at": time.time(), "dataset": dataset, "executed_case_count": len(cases),
                    "use_llm": bool(args.use_llm), "provider": release_api._DEFAULT_LLM_PROVIDER,
                    "max_turns": args.max_turns, "quality": quality,
                   "gate_passed": dataset.get("valid") and report["provider_unresolved_count"] == 0 and quality["passed"]})
    if not os.path.isdir(args.output_dir):
        os.makedirs(args.output_dir)
    write_json(os.path.join(args.output_dir, "runs.json"), runs)
    write_json(os.path.join(args.output_dir, "report.json"), report)
    write_json(os.path.join(args.output_dir, "quality_report.json"), quality)
    write_json(os.path.join(args.output_dir, "quality_badcases.json"), quality["badcases"])
    write_transcript(os.path.join(args.output_dir, "transcripts.jsonl"), runs)
    print("DYNAMIC_CONVERSATION_REPORT " + json.dumps({
        "contract": report["contract"], "executed_case_count": len(cases),
        "completed_count": report["completed_count"],
        "provider_unresolved_count": report["provider_unresolved_count"],
        "quality_passed": quality["passed"], "quality_failed_count": quality["failed_count"],
        "output_dir": args.output_dir, "gate_passed": report["gate_passed"]}, sort_keys=True))
    return 0 if report["gate_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
