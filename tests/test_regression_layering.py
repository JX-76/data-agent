# -*- coding: utf-8 -*-
"""Regression layering tests.

This file exists to define the layered regression strategy explicitly:
- contract
- integration
- golden/regression
- perf/security markers stay available for broader suites
"""

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, "src"))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def test_contract_layer_contracts_module():
    from contracts import normalize_result

    result = normalize_result({"status": "ok"}, query="q")
    assert result["query"] == "q"
    assert result["status"] == "ok"


def test_integration_layer_agent_facade():
    from agent_facade import AgentFacade

    facade = AgentFacade(session_id="layer-test")
    result = facade.ask("昨天GMV是多少？")

    assert result["session_id"] == "layer-test"
    assert "trace_id" in result
    assert "execution" in result
    assert "report" in result


def test_golden_layer_router_core_defaults():
    from router_core import ensure_plan_defaults

    plan = ensure_plan_defaults({}, "show gmv")
    assert plan["status"] == "ok"
    assert plan["metric"] == "gmv"


if __name__ == "__main__":
    test_contract_layer_contracts_module()
    test_integration_layer_agent_facade()
    test_golden_layer_router_core_defaults()
    print("All regression layering tests passed!")
