"""Compatibility tests for dag_agent public exports."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import dag_agent
from dag_status import DAG_AGENT_STATUS, get_entrypoint_status


def test_dag_agent_public_exports_exist():
    expected = {
        "AgentRuntime",
        "Dataset",
        "IntegratedDataAgent",
        "get_integrated_agent",
        "load_semantic_layer",
        "route_and_plan",
        "route_node",
        "validate_sql",
    }
    assert expected.issubset(set(dag_agent.__all__))


def test_dag_agent_runtime_and_sql_validation():
    rt = dag_agent.AgentRuntime()
    assert rt.counter == 0

    ok, reason = dag_agent.validate_sql("WITH d1 AS (SELECT 1) SELECT * FROM d1")
    assert ok is True
    assert reason == "ok"

    ok, reason = dag_agent.validate_sql("DELETE FROM fct_orders")
    assert ok is False
    assert "CTE" in reason or "forbidden" in reason


def test_dag_status_marks_dag_agent_experimental():
    assert DAG_AGENT_STATUS["graph_agent"]["status"] == "stable"
    assert get_entrypoint_status("dag_agent")["status"] == "experimental"
    assert get_entrypoint_status("dag_agent")["recommended"] is False
