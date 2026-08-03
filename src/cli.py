#!/usr/bin/env python3
"""Data Agent CLI — natural language SQL analytics agent.

Usage:
    data-agent query "昨天 GMV 是多少？"
    data-agent serve            # Start API server
    data-agent check            # Health + config validation
    data-agent mermaid          # Export DAG as Mermaid diagram
    data-agent session list     # List active sessions
    data-agent session create   # Create a new session
    data-agent session run SID "query"  # Run in session
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def cmd_query(args):
    """Execute a single natural language query."""
    from graph_agent import run_graph

    t0 = time.time()
    result = run_graph(args.query, use_db=True, use_llm=args.llm)
    dt = (time.time() - t0) * 1000

    # Diagnosis
    diagnosis = result.get("diagnosis", {})
    sev = diagnosis.get("overall_severity", "unknown") if diagnosis else "unknown"

    # Output
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        status = result.get("status", "error")
        print(f"\n{'█' * 60}")
        print(f"  Query:  {args.query}")
        print(f"  Status: {status}  |  Intent: {result.get('intent', '?')}  |  Severity: {sev}")
        print(f"  Model:  {result.get('model', '?')}  |  Metric: {result.get('metric', '?')}")
        if result.get("sql"):
            print(f"  SQL:    {result['sql'][:120]}")
        print(f"  Time:   {dt:.0f}ms")

        if result.get("status") == "clarification_needed":
            interrupt = result.get("interrupt", {})
            print(f"\n  ⚠️  Clarification needed: {interrupt.get('question', '')}")
            for opt in interrupt.get("options", []):
                print(f"     [{opt['id']}] {opt['label']}: {opt.get('description', '')}")
        elif result.get("status") == "blocked":
            print(f"\n  🛑  Blocked: {result.get('reason', '')}")
        elif result.get("insight"):
            ins = result["insight"]
            print(f"\n  💡 {ins.get('insight', '')[:200]}")
            if ins.get("chart", {}).get("type") != "none":
                print(f"  📊 Chart: {ins['chart'].get('type', '?')}")

        # Diagnosis details
        if diagnosis:
            diags = diagnosis.get("diagnoses", [])
            if diags:
                print(f"\n  📋 Diagnoses ({len(diags)}):")
                for d in diags:
                    icon = {"critical": "🔴", "warning": "🟡", "info": "🔵"}.get(d.get("severity", ""), "⚪")
                    print(f"     {icon} [{d['severity']}] {d['label']}: {d.get('evidence', '')[:80]}")


def cmd_serve(args):
    """Start the API server."""
    from server import app
    import uvicorn

    port = int(os.environ.get("PORT", args.port))
    host = args.host
    print(f"🚀 Starting Data Agent API on http://{host}:{port}")
    print(f"   Health: http://{host}:{port}/health")
    print(f"   Docs:   http://{host}:{port}/docs")
    uvicorn.run(app, host=host, port=port, log_level=args.log_level)


def cmd_check(args):
    """Health check — validates config, semantic layer, DB."""
    issues = []

    # Check semantic layer
    from dag_agent import load_semantic_layer
    try:
        sl = load_semantic_layer()
        models = list(sl.get("models", {}).keys()) or []
        metrics = list(sl.get("metrics", {}).keys()) or []
        dims = list(sl.get("dimensions", {}).keys()) or []
        print(f"✅ Semantic layer: {len(models)} models, {len(metrics)} metrics, {len(dims)} dimensions")
        print(f"   Models: {models}")
        print(f"   Metrics: {metrics}")
        print(f"   Dimensions: {dims}")
    except Exception as e:
        issues.append(f"❌ Semantic layer failed: {e}")

    # Check DB
    try:
        from db_executor import get_db
        db = get_db()
        tables = db.get_tables()
        print(f"✅ Database: {len(tables)} tables")
        for t in tables[:5]:
            print(f"   - {t}")
    except Exception as e:
        print(f"⚠️  Database not available (mock mode): {e}")

    # Check LLM config
    from config import DEEPSEEK_KEY, DEEPSEEK_BASE
    if DEEPSEEK_KEY:
        print(f"✅ LLM: DeepSeek configured ({DEEPSEEK_BASE})")
    else:
        print(f"⚠️  LLM: not configured (regex routing only)")

    # Quick smoke test
    try:
        from graph_agent import run_graph
        r = run_graph("1+1", use_db=True, use_llm=False)
        if r.get("status") == "blocked":
            print("✅ Smoke test: blocked query handled correctly")
        elif r.get("status") == "ok":
            print("✅ Smoke test: query executed")
        else:
            print(f"⚠️  Smoke test: unexpected status '{r.get('status')}'")
    except Exception as e:
        issues.append(f"❌ Smoke test failed: {e}")

    if issues:
        print(f"\n❌ {len(issues)} issues found:")
        for i in issues:
            print(f"   {i}")
        sys.exit(1)
    else:
        print("\n✅ All checks passed")


def cmd_mermaid(args):
    """Export DAG graph as Mermaid diagram."""
    from graph_agent import build_data_agent_graph
    g = build_data_agent_graph()
    mermaid = g.compile().to_mermaid()
    if args.output:
        Path(args.output).write_text(mermaid)
        print(f"✅ Mermaid diagram saved to {args.output}")
    else:
        print(mermaid)


def cmd_session(args):
    """Session management subcommands."""
    from session import SessionManager

    sm = SessionManager()

    if args.action == "create":
        sid = sm.create()
        print(f"✅ Created session: {sid}")

    elif args.action == "list":
        if not sm.sessions:
            print("No active sessions.")
        for sid, s in sm.sessions.items():
            print(f"  {sid}  ({len(s.turns)} turns, last: {s.last_status()})")

    elif args.action == "run":
        if not args.session_id:
            print("❌ --session-id required")
            sys.exit(1)
        if args.session_id not in sm.sessions:
            sm.create(args.session_id)
        result = sm.run(args.session_id, args.query)
        status = result.get("status", "?")
        print(f"  [{status}] {result.get('intent', '?')}")
        if result.get("insight", {}).get("insight"):
            print(f"  💡 {result['insight']['insight'][:200]}")

    elif args.action == "resume":
        if not args.session_id or not args.choice_id:
            print("❌ --session-id and --choice-id required")
            sys.exit(1)
        result = sm.resume(args.session_id, args.choice_id)
        print(f"  [{result.get('status', '?')}] resumed with choice={args.choice_id}")

    elif args.action == "history":
        if not args.session_id:
            print("❌ --session-id required")
            sys.exit(1)
        for turn in sm.history(args.session_id):
            print(f"  [{turn['status']}] {turn['query'][:60]}")

    elif args.action == "stats":
        if not args.session_id:
            print("❌ --session-id required")
            sys.exit(1)
        stats = sm.stats(args.session_id)
        print(json.dumps(stats, ensure_ascii=False, indent=2))

    elif args.action == "delete":
        if not args.session_id:
            print("❌ --session-id required")
            sys.exit(1)
        if args.session_id in sm.sessions:
            del sm.sessions[args.session_id]
            print(f"✅ Deleted session: {args.session_id}")
        else:
            print(f"⚠️  Session not found: {args.session_id}")


def main():
    parser = argparse.ArgumentParser(description="Data Agent CLI")
    sub = parser.add_subparsers(dest="command")

    # query
    p = sub.add_parser("query", help="Execute a query")
    p.add_argument("query", help="Natural language query")
    p.add_argument("--llm", action="store_true", help="Use LLM routing")
    p.add_argument("--format", choices=["text", "json"], default="text")
    p.set_defaults(func=cmd_query)

    # serve
    p = sub.add_parser("serve", help="Start API server")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--log-level", default="info", choices=["debug", "info", "warning", "error"])
    p.set_defaults(func=cmd_serve)

    # check
    p = sub.add_parser("check", help="Health check + config validation")
    p.set_defaults(func=cmd_check)

    # mermaid
    p = sub.add_parser("mermaid", help="Export DAG as Mermaid")
    p.add_argument("--output", "-o", help="Output file path")
    p.set_defaults(func=cmd_mermaid)

    # session
    p = sub.add_parser("session", help="Session management")
    p.add_argument("action", choices=["create", "list", "run", "resume", "history", "stats", "delete"])
    p.add_argument("--session-id", "-s", help="Session ID")
    p.add_argument("--query", "-q", help="Query text")
    p.add_argument("--choice-id", "-c", help="Clarification choice ID")
    p.set_defaults(func=cmd_session)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
