from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import dag_agent


def test_dag_agent_runtime_alias_and_inheritance():
    assert dag_agent.AgentRuntime is dag_agent.DAGAgentRuntime
    rt = dag_agent.AgentRuntime()
    assert rt.counter == 0
    assert isinstance(rt, dag_agent.DAGAgentRuntime)
