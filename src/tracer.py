"""Langfuse tracing integration for Nucleus Data Agent.

Implements the TracingObserver protocol to provide automatic Langfuse
trace generation for every graph execution — zero code changes to nodes.

Usage:
    from tracer import LangfuseTracer

    # Auto-detects LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY from env
    tracer = LangfuseTracer()

    # Or explicit:
    tracer = LangfuseTracer(public_key="pk-...", secret_key="sk-...")

    # Pass to run_graph:
    result = run_graph(query, tracer=tracer)

    # Without tracer (no-op):
    result = run_graph(query)  # no tracing, works fine
"""

from __future__ import annotations

import os
import uuid
import time
import json
from typing import Any, Optional

from nucleus import TracingObserver


class LangfuseTracer:
    """Langfuse-based tracer implementing TracingObserver.

    Creates a Langfuse Trace for each graph execution, with nested
    Observations for each node in the DAG. Scores can be attached
    via log_score().

    Gracefully degrades to no-op when Langfuse is not configured.
    """

    def __init__(
        self,
        public_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        host: Optional[str] = None,
        enabled: bool = True,
    ):
        self.enabled = enabled
        self._client = None
        self._active_spans: dict[str, Any] = {}  # trace_id → {node_name: span}

        if not self.enabled:
            return

        public_key = public_key or os.getenv("LANGFUSE_PUBLIC_KEY")
        secret_key = secret_key or os.getenv("LANGFUSE_SECRET_KEY")
        host = host or os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com")

        if not public_key or not secret_key:
            self.enabled = False
            return

        try:
            from langfuse import Langfuse
            self._client = Langfuse(
                public_key=public_key,
                secret_key=secret_key,
                host=host,
            )
        except ImportError:
            self.enabled = False

    def _ensure_client(self):
        if self._client is None and self.enabled:
            try:
                from langfuse import Langfuse
                self._client = Langfuse()
            except Exception as e:
                logger.warning("bare_exception_caught", error=str(e))
                self.enabled = False

    # ── TracingObserver implementation ──

    def on_trace_start(self, trace_id: str, graph_name: str, initial_state: dict) -> None:
        if not self.enabled or not self._client:
            return
        self._active_spans[trace_id] = {}
        # The root "agent" span is created on first node_start,
        # or we create a synthetic one here for the trace container.
        try:
            query = initial_state.get("query", "")
            self._active_spans[trace_id]["__root__"] = self._client.start_observation(
                trace_context={"trace_id": trace_id},
                name=graph_name,
                as_type="agent",
                input={"query": query, "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S")},
                metadata={"graph": graph_name, "initial_state_keys": list(initial_state.keys())},
            )
        except Exception as e:
            logger.warning("bare_exception_caught", error=str(e))
            pass

    def on_trace_end(self, trace_id: str, final_state: dict) -> None:
        if not self.enabled or not self._client:
            return
        try:
            spans = self._active_spans.pop(trace_id, {})
            root = spans.get("__root__")
            if root:
                output = final_state.get("output", {})
                root.update(
                    output={
                        "status": output.get("status", "ok"),
                        "model": output.get("model"),
                        "intent": output.get("intent"),
                        "trace_steps": len(output.get("trace", [])),
                    },
                    metadata={
                        "total_steps": final_state.get("__step__", 0),
                        "has_sql": bool(final_state.get("sql")),
                        "valid": final_state.get("valid"),
                    },
                )
            # End any remaining spans
            for span in spans.values():
                try:
                    if hasattr(span, 'end'):
                        span.end()
                except Exception as e:
                    logger.warning("bare_exception_caught", error=str(e))
                    pass
            self._client.flush()
        except Exception as e:
            logger.warning("bare_exception_caught", error=str(e))
            pass

    def on_node_start(self, trace_id: str, node_name: str, state: dict, step: int) -> None:
        if not self.enabled or not self._client:
            return
        try:
            spans = self._active_spans.setdefault(trace_id, {})
            if node_name in spans and node_name != "__root__":
                return  # Already started

            # Determine observation type from node name
            node_type = self._infer_node_type(node_name, state)

            # Build input context (safe subset of state)
            safe_input = {
                "node": node_name,
                "step": step,
                "intent": state.get("intent"),
                "model": state.get("model"),
                "metric": state.get("metric"),
                "dimensions": state.get("dimensions"),
            }
            # Trim None values
            safe_input = {k: v for k, v in safe_input.items() if v is not None}

            # Get parent span for nested tracking
            parent_id = None
            root = spans.get("__root__")
            if root:
                parent_id = root.id

            # Create span with detailed metadata
            span = self._client.start_observation(
                trace_context={
                    "trace_id": trace_id,
                    "parent_observation_id": parent_id,
                },
                name=node_name,
                as_type=node_type,
                input=safe_input,
                metadata={
                    "step": step,
                    "node_type": node_type,
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "has_sql": bool(state.get("sql")),
                    "has_plan": bool(state.get("plan")),
                },
            )
            spans[node_name] = span
            
            # Log node start
            logger.debug("node_started", trace_id=trace_id, node=node_name, step=step)
        except Exception as e:
            logger.warning("bare_exception_caught", error=str(e))
            pass

    def on_node_end(self, trace_id: str, node_name: str, state: dict, step: int, status: str) -> None:
        if not self.enabled or not self._client:
            return
        try:
            spans = self._active_spans.get(trace_id, {})
            span = spans.get(node_name)
            if span is None:
                return

            # Build safe output
            safe_output = {
                "status": status,
                "step": step,
            }
            if node_name == "route":
                safe_output["plan"] = {
                    "model": state.get("model"),
                    "intent": state.get("intent"),
                    "metric": state.get("metric"),
                    "dimensions": state.get("dimensions"),
                }
            elif node_name == "analyze":
                valid = state.get("valid")
                safe_output["valid"] = valid
                if state.get("sql"):
                    safe_output["sql_preview"] = state["sql"][:200]
            elif node_name == "output":
                out = state.get("output", {})
                safe_output["result_status"] = out.get("status")
                safe_output["has_results"] = bool(out.get("results"))
                safe_output["has_insight"] = bool(out.get("insight"))

            span.update(output=safe_output)

            # End the span
            if hasattr(span, 'end'):
                span.end()
            del spans[node_name]
        except Exception as e:
            logger.warning("bare_exception_caught", error=str(e))
            pass

    def on_node_error(self, trace_id: str, node_name: str, error: str, step: int) -> None:
        if not self.enabled or not self._client:
            return
        try:
            spans = self._active_spans.get(trace_id, {})
            span = spans.get(node_name)
            if span:
                span.update(
                    level="ERROR",
                    status_message=error[:500],
                    output={"error": error[:500], "step": step},
                )
                if hasattr(span, 'end'):
                    span.end()
                del spans[node_name]
        except Exception as e:
            logger.warning("bare_exception_caught", error=str(e))
            pass

    def on_interrupt(self, trace_id: str, node_name: str, payload: Any) -> None:
        if not self.enabled or not self._client:
            return
        try:
            spans = self._active_spans.get(trace_id, {})
            span = spans.get(node_name)
            if span:
                span.update(
                    output={"interrupt": str(payload)[:500]},
                )
                if hasattr(span, 'end'):
                    span.end()
                del spans[node_name]
        except Exception as e:
            logger.warning("bare_exception_caught", error=str(e))
            pass

    # ── Scoring API (called externally after trace) ──

    def log_score(
        self,
        trace_id: str,
        name: str,
        value: float | str,
        data_type: str = "NUMERIC",
        comment: str = "",
    ) -> None:
        """Attach a score to a trace. Safe to call before trace completes."""
        if not self.enabled or not self._client:
            return
        try:
            self._client.create_score(
                trace_id=trace_id,
                name=name,
                value=value,
                data_type=data_type,
                comment=comment,
            )
        except Exception as e:
            logger.warning("bare_exception_caught", error=str(e))
            pass

    # ── Helpers ──

    def _infer_node_type(self, node_name: str, state: dict) -> str:
        """Infer the Langfuse observation type from node name and state."""
        type_map = {
            "route": "chain",
            "switch": "chain",
            "preview": "span",
            "filter_node": "chain",
            "aggregate": "chain",
            "sort_or_top": "chain",
            "merge_dual": "chain",
            "compare_periods": "chain",
            "analyze": "evaluator",
            "output": "chain",
            "blocked": "guardrail",
            "clarify": "span",
        }
        return type_map.get(node_name, "span")

    def flush(self):
        """Force flush pending traces."""
        if self._client:
            try:
                self._client.flush()
            except Exception as e:
                logger.warning("bare_exception_caught", error=str(e))
                pass
