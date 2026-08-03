from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import dag_agent
import dag_integrated
import dag_routing
from dag_runtime import DAGAgentRuntime


def test_dag_runtime_import_target_matches_facade():
    assert dag_agent.DAGAgentRuntime is DAGAgentRuntime
    assert dag_agent.AgentRuntime is DAGAgentRuntime


def test_dag_routing_compat_module_exports_facade_functions():
    assert dag_routing.route_and_plan is dag_agent.route_and_plan
    assert dag_routing.route_node is dag_agent.route_node


def test_dag_integrated_compat_module_exports_facade_classes():
    assert dag_integrated.IntegratedDataAgent is dag_agent.IntegratedDataAgent
    assert dag_integrated.get_integrated_agent is dag_agent.get_integrated_agent
