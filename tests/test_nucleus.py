"""Unit tests for Nucleus DAG framework."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest
from nucleus import Graph, Interrupt, Executor, NodeError, StepResult


class TestGraphConstruction:
    """Test Graph building and validation."""

    def test_basic_graph(self):
        g = Graph("test")

        @g.node("a")
        def a(s): s["x"] = 1; return s

        @g.node("b")
        def b(s): s["y"] = 2; return s

        g.edge("a", "b")
        g.set_entry("a")
        g.set_finish("b")
        executor = g.compile()
        assert isinstance(executor, Executor)

    def test_missing_entry(self):
        g = Graph("test")

        @g.node("a")
        def a(s): return s

        with pytest.raises(ValueError, match="Entry node not set"):
            g.compile()

    def test_unknown_entry(self):
        g = Graph("test")
        g.set_entry("nonexistent")
        with pytest.raises(ValueError, match="Entry node.*not registered"):
            g.compile()

    def test_node_without_outgoing_edge(self):
        g = Graph("test")

        @g.node("a")
        def a(s): return s

        @g.node("b")
        def b(s): return s

        g.edge("a", "b")
        g.set_entry("a")
        g.set_finish("b")

        @g.node("orphan")
        def orphan(s): return s

        with pytest.raises(ValueError, match="has no outgoing edges"):
            g.compile()

    def test_conditional_edge(self):
        g = Graph("test")

        @g.node("route")
        def route(s): s["path"] = "x"; return s

        @g.node("x")
        def x(s): s["result"] = "X"; return s

        @g.node("y")
        def y(s): s["result"] = "Y"; return s

        g.conditional_edge("route", lambda s: s["path"])
        g.edge("x", "y")
        g.set_entry("route")
        g.set_finish("y")
        ex = g.compile()
        r = ex.run({})
        assert r["result"] == "Y"


class TestExecutor:
    """Test graph execution."""

    def test_simple_execution(self):
        g = Graph("test")

        @g.node("a")
        def a(s): s["a_val"] = 1; return s

        @g.node("b")
        def b(s): s["b_val"] = 2; return s

        g.edge("a", "b")
        g.set_entry("a")
        g.set_finish("b")
        ex = g.compile()
        result = ex.run({"init": True})
        assert result["init"] is True
        assert result["a_val"] == 1
        assert result["b_val"] == 2
        assert len(ex.trace) == 2
        assert ex.trace[0].status == "ok"
        assert ex.trace[1].status == "ok"

    def test_interrupt_and_resume(self):
        g = Graph("test")
        count = {"calls": 0}

        @g.node("ask")
        def ask(s):
            count["calls"] += 1
            raise Interrupt({"question": "yes/no?"})

        @g.node("process")
        def process(s):
            payload = s.get("__resume_payload__", {})
            s["answer"] = payload.get("answer", "none")
            return s

        g.conditional_edge("ask", lambda s: "process")
        g.set_entry("ask")
        g.set_finish("process")
        ex = g.compile()

        # Run should interrupt
        r = ex.run({"q": "test"})
        assert r["__interrupt__"] == {"question": "yes/no?"}

        # Resume
        r2 = ex.resume(r, {"answer": "yes"})
        assert r2["answer"] == "yes"
        assert count["calls"] == 1  # ask was skipped during resume

    def test_retry_on_failure(self):
        g = Graph("test")
        attempts = {"count": 0}

        @g.node("a", retry=2)
        def a(s):
            attempts["count"] += 1
            if attempts["count"] < 3:
                raise ValueError(f"attempt {attempts['count']} failed")
            s["ok"] = True
            return s

        g.set_entry("a")
        g.set_finish("a")
        ex = g.compile()
        r = ex.run({})
        assert r["ok"] is True
        assert attempts["count"] == 3

    def test_retry_exhausted(self):
        g = Graph("test")

        @g.node("a", retry=1)
        def a(s):
            raise ValueError("always fails")

        g.set_entry("a")
        g.set_finish("a")
        ex = g.compile()
        with pytest.raises(NodeError, match="Node 'a' failed"):
            ex.run({})


class TestStability:
    """Tests for stability guards."""

    def test_self_loop_rejected(self):
        g = Graph("test")

        @g.node("r")
        def r(s): return s

        def loop_condition(state):
            return "r"  # returns self

        g.conditional_edge("r", loop_condition)
        g.set_entry("r")
        ex = g.compile()
        with pytest.raises(ValueError, match="infinite loop"):
            ex.run({})

    def test_max_steps_guard(self):
        g = Graph("test")

        @g.node("x")
        def x(s): return s

        @g.node("y")
        def y(s): return s

        g.conditional_edge("x", lambda s: "y")
        g.conditional_edge("y", lambda s: "x")
        g.set_entry("x")
        ex = g.compile(max_steps=5)
        with pytest.raises(RuntimeError, match="exceeded max_steps"):
            ex.run({})

    def test_condition_exception_propagated(self):
        g = Graph("test")

        @g.node("a")
        def a(s): return s

        def bad_condition(state):
            raise RuntimeError("condition crashed")

        g.conditional_edge("a", bad_condition)
        g.set_entry("a")
        ex = g.compile()
        with pytest.raises(ValueError, match="RuntimeError: condition crashed"):
            ex.run({})

    def test_unknown_node_from_condition(self):
        g = Graph("test")

        @g.node("a")
        def a(s): return s

        g.conditional_edge("a", lambda s: "nonexistent")
        g.set_entry("a")
        ex = g.compile()
        with pytest.raises(ValueError, match="unknown node"):
            ex.run({})

    def test_condition_returns_none_stops(self):
        g = Graph("test")

        @g.node("a")
        def a(s): return s

        @g.node("b")
        def b(s): return s

        g.conditional_edge("a", lambda s: None)
        g.set_entry("a")
        g.set_finish("b")
        ex = g.compile()
        r = ex.run({})
        assert "__current_node__" in r
        assert len(ex.trace) == 1  # Only 'a' executed, stopped at conditional

    def test_mermaid_output(self):
        g = Graph("test")

        @g.node("a")
        def a(s): return s

        @g.node("b")
        def b(s): return s

        g.edge("a", "b")
        g.set_entry("a")
        g.set_finish("b")
        ex = g.compile()
        mermaid = ex.to_mermaid()
        assert "graph TD" in mermaid
        assert "a" in mermaid
        assert "b" in mermaid
        assert "-->" in mermaid

    def test_trace_on_error(self):
        g = Graph("test")

        @g.node("a")
        def a(s): raise ValueError("boom")

        g.set_entry("a")
        g.set_finish("a")
        ex = g.compile()
        try:
            ex.run({})
        except NodeError:
            pass
        assert len(ex.trace) == 1
        assert ex.trace[0].status == "error"
        assert "boom" in ex.trace[0].error
