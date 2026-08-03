"""Nucleus — A lightweight, zero-dependency DAG orchestration framework
for data agents.

Inspired by LangGraph's StateGraph + Pregel model, but purpose-built for
data agent workflows. No dependency on LangChain or any external framework.

Core concepts:
- State: Typed, serializable state object passed between nodes
- Node: A function that takes State → returns State (or raises Interrupt)
- Edge: Normal (always) or Conditional (switch on state field)
- Graph: Nodes + Edges → compiled to an Executor
- Executor: Runs the graph, supports streaming, interrupts, and retry

Key design decisions:
1. State is a plain dict with schema validation (not Pydantic — keep it light)
2. Nodes are pure functions: State → State
3. Conditional edges enable branching (like LangGraph's add_conditional_edges)
4. Interrupt mechanism for human-in-the-loop (clarification flow)
5. Compile step validates the graph before execution
6. Trace is built-in for observability
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple, Protocol, runtime_checkable
from enum import Enum
import copy
import json
import time
import structlog

logger = structlog.get_logger("nucleus")


@runtime_checkable
class TracingObserver(Protocol):
    """Observer protocol for tracing graph execution.
    
    Implement this to integrate with Langfuse, Phoenix, or any other
    observability backend without coupling Nucleus to a specific vendor.
    """
    def on_trace_start(self, trace_id: str, graph_name: str, initial_state: dict) -> None: ...
    def on_trace_end(self, trace_id: str, final_state: dict) -> None: ...
    def on_node_start(self, trace_id: str, node_name: str, state: dict, step: int) -> None: ...
    def on_node_end(self, trace_id: str, node_name: str, state: dict, step: int, status: str) -> None: ...
    def on_node_error(self, trace_id: str, node_name: str, error: str, step: int) -> None: ...
    def on_interrupt(self, trace_id: str, node_name: str, payload: Any) -> None: ...


class EdgeType(Enum):
    NORMAL = "normal"
    CONDITIONAL = "conditional"


@dataclass
class Edge:
    """An edge in the DAG."""
    from_node: str
    to_node: str | None = None  # None for conditional (resolved at runtime)
    type: EdgeType = EdgeType.NORMAL
    condition: Callable | None = None  # State → str (next node name)
    label: str = ""


@dataclass
class NodeSpec:
    """A node in the graph."""
    name: str
    fn: Callable  # State → State
    description: str = ""
    retry: int = 0  # Max retries on failure
    timeout_seconds: int = 0  # 0 = no timeout


@dataclass
class StepResult:
    """Result of executing one node."""
    node: str
    status: str  # "ok" | "error" | "interrupt"
    state_before: dict
    state_after: dict | None = None
    error: str | None = None
    interrupt: dict | None = None  # Interrupt payload for human-in-the-loop


class Interrupt(Exception):
    """Raised by a node to pause execution and wait for human input."""
    def __init__(self, payload: dict):
        self.payload = payload
        super().__init__(str(payload))


class NodeError(Exception):
    """Raised when a node fails (after exhausting retries)."""
    def __init__(self, node_name: str, original_error: Exception):
        self.node_name = node_name
        self.original_error = original_error
        super().__init__(f"Node '{node_name}' failed: {original_error}")


class Graph:
    """A directed graph of nodes and edges.

    Usage:
        graph = Graph("data_agent")

        @graph.node("switch")
        def switch_node(state):
            state["model"] = "order_detail"
            return state

        @graph.node("filter")
        def filter_node(state):
            state["filtered"] = True
            return state

        graph.edge("switch", "filter")
        graph.set_entry("switch")
        graph.set_finish("filter")

        executor = graph.compile()
        result = executor.run({"query": "GMV?"})
    """

    def __init__(self, name: str = "graph"):
        self.name = name
        self._nodes: Dict[str, NodeSpec] = {}
        self._edges: List[Edge] = []
        self._entry: str | None = None
        self._finish: str | None = None

    def node(self, name: str = None, retry: int = 0, timeout: int = 0, description: str = ""):
        """Decorator to register a node function.

        Can be used as @graph.node() or @graph.node("name").
        """
        def decorator(fn: Callable):
            node_name = name or fn.__name__
            self._nodes[node_name] = NodeSpec(
                name=node_name,
                fn=fn,
                description=description or fn.__doc__ or "",
                retry=retry,
                timeout_seconds=timeout,
            )
            return fn
        return decorator

    def add_node(self, name: str, fn: Callable, retry: int = 0, timeout: int = 0, description: str = ""):
        """Register a node function by name."""
        self._nodes[name] = NodeSpec(
            name=name,
            fn=fn,
            description=description,
            retry=retry,
            timeout_seconds=timeout,
        )

    def edge(self, from_node: str, to_node: str, label: str = ""):
        """Add a normal (unconditional) edge."""
        self._edges.append(Edge(from_node=from_node, to_node=to_node, type=EdgeType.NORMAL, label=label))

    def conditional_edge(self, from_node: str, condition: Callable[[dict], str], label: str = ""):
        """Add a conditional edge: the condition function maps state → next node name.

        Example:
            def route(state):
                if state.get("intent") == "merge":
                    return "merge_aggregate"
                return "filter"
            graph.conditional_edge("switch", route)
        """
        self._edges.append(Edge(from_node=from_node, type=EdgeType.CONDITIONAL, condition=condition, label=label))

    def set_entry(self, node_name: str):
        """Set the entry point node."""
        self._entry = node_name

    def set_finish(self, node_name: str):
        """Set the finish/terminal node."""
        self._finish = node_name

    def compile(self, max_steps: int = 200, observer: "TracingObserver | None" = None) -> "Executor":
        """Validate and compile the graph into an Executor."""
        self._validate()
        return Executor(self, max_steps=max_steps, observer=observer)

    def _validate(self):
        """Validate the graph structure."""
        if not self._entry:
            raise ValueError("Entry node not set. Call set_entry().")
        if self._entry not in self._nodes:
            raise ValueError(f"Entry node '{self._entry}' not registered.")
        if self._finish and self._finish not in self._nodes:
            raise ValueError(f"Finish node '{self._finish}' not registered.")

        node_names = set(self._nodes.keys())
        for edge in self._edges:
            if edge.from_node not in node_names:
                raise ValueError(f"Edge from unknown node '{edge.from_node}'")
            if edge.type == EdgeType.NORMAL and edge.to_node not in node_names:
                raise ValueError(f"Edge to unknown node '{edge.to_node}'")

        # Ensure all non-finish nodes have outgoing edges (skip interrupt-only nodes)
        interrupt_nodes = {"clarify", "ask"}  # Nodes that raise Interrupt — resume handles them
        for name in node_names:
            if name == self._finish or name in interrupt_nodes:
                continue
            has_outgoing = any(e.from_node == name for e in self._edges)
            if not has_outgoing:
                raise ValueError(f"Node '{name}' has no outgoing edges. Add an edge or set it as finish.")


class Executor:
    """Compiled graph executor with streaming and interrupt support."""

    def __init__(self, graph: Graph, max_steps: int = 200, observer: TracingObserver | None = None, max_api_calls: int = 50):
        self.graph = graph
        self.trace: List[StepResult] = []
        self.max_steps = max_steps
        self.observer = observer
        # 优化点3：API调用计数器，防止死循环
        self.max_api_calls = max_api_calls
        self.api_call_count = 0

    def _resolve_next(self, current_node: str, state: dict) -> str | None:
        """Find the next node by checking edges from current_node."""
        for edge in self.graph._edges:
            if edge.from_node != current_node:
                continue
            if edge.type == EdgeType.NORMAL:
                return edge.to_node
            elif edge.type == EdgeType.CONDITIONAL and edge.condition:
                try:
                    next_node = edge.condition(state)
                except Exception as e:
                    raise ValueError(
                        f"Conditional edge from '{current_node}' raised {type(e).__name__}: {e}"
                    ) from e
                if next_node is None:
                    return None  # Conditional edge chose to stop
                if next_node == current_node:
                    raise ValueError(
                        f"Conditional edge from '{current_node}' returned self — "
                        f"this would create an infinite loop"
                    )
                if next_node not in self.graph._nodes:
                    raise ValueError(f"Conditional edge from '{current_node}' returned unknown node '{next_node}'")
                return next_node
        return None  # No outgoing edges — terminal

    def run(self, initial_state: dict) -> dict:
        """Run the graph from entry to finish (or interrupt).

        Args:
            initial_state: Starting state dict

        Returns:
            Final state dict. If an interrupt was raised, state["__interrupt__"] contains the payload.

        Raises:
            RuntimeError: if max_steps is exceeded (guard against infinite loops)
        """
        state = copy.deepcopy(initial_state)
        state["__graph__"] = self.graph.name
        state["__current_node__"] = None
        state["__interrupt__"] = None
        state["__step__"] = 0
        state["__trace_id__"] = state.get("__trace_id__", self.graph.name)
        # 优化点3：重置API调用计数
        self.api_call_count = 0

        # Notify observer: trace start
        if self.observer:
            self.observer.on_trace_start(state["__trace_id__"], self.graph.name, initial_state)

        current = self.graph._entry
        if not current:
            if self.observer:
                self.observer.on_trace_end(state["__trace_id__"], state)
            return state

        while current:
            state["__current_node__"] = current
            node_spec = self.graph._nodes[current]
            state_before = copy.deepcopy(state)
            step = state["__step__"]

            # Notify observer: node start
            if self.observer:
                self.observer.on_node_start(state["__trace_id__"], current, state, step)

            # 优化点3：API调用计数检查
            self.api_call_count += 1
            if self.api_call_count > self.max_api_calls:
                raise RuntimeError(
                    f"API调用超限: {self.api_call_count} > {self.max_api_calls}。"
                    f"可能死循环，最后节点: {current}"
                )

            # Execute with retry
            last_error = None
            for attempt in range(node_spec.retry + 1):
                try:
                    result = node_spec.fn(state)
                    if result is not None:
                        state = result
                    state["__current_node__"] = current
                    last_error = None
                    break
                except Interrupt as e:
                    state["__interrupt__"] = e.payload

                    self.trace.append(StepResult(
                        node=current,
                        status="interrupt",
                        state_before=state_before,
                        state_after=copy.deepcopy(state),
                        interrupt=e.payload,
                    ))
                    if self.observer:
                        self.observer.on_interrupt(state["__trace_id__"], current, e.payload)
                    return state
                except Exception as e:
                    last_error = e
                    if attempt < node_spec.retry:
                        state["__last_error__"] = str(e)
                        continue

            if last_error:
                self.trace.append(StepResult(
                    node=current,
                    status="error",
                    state_before=state_before,
                    error=str(last_error),
                ))
                if self.observer:
                    self.observer.on_node_error(state["__trace_id__"], current, str(last_error), step)
                raise NodeError(current, last_error)

            self.trace.append(StepResult(
                node=current,
                status="ok",
                state_before=state_before,
                state_after=copy.deepcopy(state),
            ))

            # Notify observer: node end
            if self.observer:
                self.observer.on_node_end(state["__trace_id__"], current, state, step, "ok")

            # Terminal?
            if current == self.graph._finish:
                break

            # Guard against infinite loops
            state["__step__"] += 1
            if state["__step__"] > self.max_steps:
                raise RuntimeError(
                    f"Graph execution exceeded max_steps ({self.max_steps}). "
                    f"Possible infinite loop. Last node: {current}"
                )


            # Find next node
            current = self._resolve_next(current, state)

        state["__current_node__"] = None
        if self.observer:
            self.observer.on_trace_end(state["__trace_id__"], state)
        return state

    def resume(self, state: dict, resume_payload: dict) -> dict:
        """Resume execution after an interrupt.

        Args:
            state: The state returned by run() (with __interrupt__)
            resume_payload: User's response to the interrupt

        Returns:
            Final state after resuming.

        Raises:
            RuntimeError: if max_steps is exceeded
        """
        state = copy.deepcopy(state)
        state["__interrupt__"] = None
        state["__resume_payload__"] = resume_payload
        if "__step__" not in state:
            state["__step__"] = 0

        # Continue from the node that was interrupted (skip it — it already raised Interrupt)
        current = state.get("__current_node__")
        if not current:
            raise ValueError("Cannot resume: no __current_node__ in state")

        # Skip the node that interrupted: find the next node from it
        current = self._resolve_next(current, state)

        while current:
            state["__current_node__"] = current
            node_spec = self.graph._nodes[current]
            state_before = copy.deepcopy(state)

            # 优化点3：resume时也检查API调用计数
            self.api_call_count += 1
            if self.api_call_count > self.max_api_calls:
                raise RuntimeError(
                    f"API调用超限: {self.api_call_count} > {self.max_api_calls}。"
                    f"可能死循环，最后节点: {current}"
                )

            # Execute with retry
            last_error = None
            for attempt in range(node_spec.retry + 1):
                try:
                    result = node_spec.fn(state)
                    if result is not None:
                        state = result
                    state["__current_node__"] = current
                    last_error = None
                    break
                except Interrupt as e:
                    state["__interrupt__"] = e.payload
                    self.trace.append(StepResult(
                        node=current,
                        status="interrupt",
                        state_before=state_before,
                        state_after=copy.deepcopy(state),
                        interrupt=e.payload,
                    ))
                    return state
                except Exception as e:
                    last_error = e
                    if attempt < node_spec.retry:
                        state["__last_error__"] = str(e)
                        continue

            if last_error:
                self.trace.append(StepResult(
                    node=current,
                    status="error",
                    state_before=state_before,
                    error=str(last_error),
                ))
                raise NodeError(current, last_error)

            self.trace.append(StepResult(
                node=current,
                status="ok",
                state_before=state_before,
                state_after=copy.deepcopy(state),
            ))

            if current == self.graph._finish:
                break

            # Guard against infinite loops
            state["__step__"] += 1
            if state["__step__"] > self.max_steps:
                raise RuntimeError(
                    f"Graph execution exceeded max_steps ({self.max_steps}). "
                    f"Possible infinite loop. Last node: {current}"
                )

            current = self._resolve_next(current, state)

        state["__current_node__"] = None
        return state

    def to_mermaid(self) -> str:
        """Generate a Mermaid flowchart of the graph."""
        lines = ["graph TD"]
        for name, spec in self.graph._nodes.items():
            lines.append(f'    {name}["{name}<br/><small>{spec.description}</small>"]')
        for edge in self.graph._edges:
            if edge.type == EdgeType.NORMAL:
                lines.append(f'    {edge.from_node} --> {edge.to_node}')
            elif edge.type == EdgeType.CONDITIONAL:
                lines.append(f'    {edge.from_node} -->|condition| ?')
        if self.graph._entry:
            lines.append(f'    START(( )) --> {self.graph._entry}')
        if self.graph._finish:
            lines.append(f'    {self.graph._finish} --> END(( ))')
        return "\n".join(lines)
