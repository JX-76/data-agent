"""Data Agent built on Nucleus DAG framework.

Status: 🧪 experimental / extension-heavy DAG aggregator.

This module defines the canonical Data Agent graph:
1. Route node → Switch node → Preview node → Filter node → Aggregate node → Sort/Top node → Analyze node → Output node

With conditional routing for:
- Clarification (interrupt → resume)
- Merge (two parallel Aggregate → Merge → Sort)
- Compare periods (Compare node)
- Filter by value (FilterValue node after Filter)

Implementation note:
- Stable production path should prefer `graph_agent.py`.
- This module currently aggregates plan/execution/error-recovery/advanced features and should be treated as experimental until split into smaller submodules.
"""

import datetime as dt
import json
from pathlib import Path
from typing import Any
from dataclasses import dataclass

import structlog

logger = structlog.get_logger("dag_agent")

from runtime_core import Dataset
from semantic_utils import DANGEROUS, SENSITIVE, load_semantic_layer, yaml
from nucleus import Graph, Interrupt, Executor

from db_executor import get_db as _get_db
from config import SEMANTIC_SUMMARY, DEEPSEEK_KEY, DEEPSEEK_BASE, ROUTER_MODEL, ANALYSIS_MODEL

__all__ = [
    "AgentRuntime",
    "Dataset",
    "IntegratedDataAgent",
    "get_integrated_agent",
    "load_semantic_layer",
    "route_and_plan",
    "route_node",
    "validate_sql",
]

# ── Phase 1: Plan-then-Execute imports ──
try:
    from plan_executor import PlanExecutor, generate_plan, validate_plan
    from feasibility import assess_feasibility
    from structured_output import TextTypeParser, StructuredPrompt, build_plan_schema
    PLAN_EXECUTE_AVAILABLE = True
except ImportError:
    PLAN_EXECUTE_AVAILABLE = False

# ── Phase 2: Error Recovery imports ──
try:
    from sql_retry import SQLRetryHandler
    from evidence_recall import EvidenceRecall
    from table_relation import TableRelationAnalyzer
    from python_retry import PythonRetryHandler
    ERROR_RECOVERY_AVAILABLE = True
except ImportError:
    ERROR_RECOVERY_AVAILABLE = False

# ── Phase 3-4: Advanced Features imports ──
try:
    from report_generator import ReportGenerator
    from query_enhance import QueryEnhancer
    from python_sandbox import PythonSandbox
    from streaming_sse import StreamingSSE
    from human_feedback import HumanFeedbackNode
    from reward_steps import RewardCalculator
    from log_metric_query import LogMetricQuery
    from regulation_parser import RegulationParser
    from file_locator import FileLocator
    from code_fix import CodeFixer
    from test_gen import TestGenerator
    from docker_execute import DockerExecutor
    ADVANCED_FEATURES_AVAILABLE = True
except ImportError:
    ADVANCED_FEATURES_AVAILABLE = False

# ── Semantic Layer Loading ──

SEM = load_semantic_layer() if yaml else None

from dag_routing import route_node, route_and_plan

from dag_runtime import DAGAgentRuntime


# Backward-compatible public runtime export.
AgentRuntime = DAGAgentRuntime


def validate_sql(sql):
    low = sql.lower()
    if not low.strip().startswith("with"):
        return False, "Runtime SQL must be generated as CTE chain"
    if any(x in low for x in ["delete", "update", "insert", "drop", "alter", "truncate"]):
        return False, "DDL/DML is forbidden"
    if any(x in low for x in SENSITIVE):
        return False, "Sensitive field is forbidden"
    return True, "ok"


from dag_integrated import IntegratedDataAgent, get_integrated_agent
