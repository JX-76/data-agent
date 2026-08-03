import os, sys
ROOT=os.path.abspath(os.path.join(os.path.dirname(__file__),os.pardir,'src'))
if ROOT not in sys.path: sys.path.insert(0,ROOT)

from dag_routing import route_and_plan
from phase3a_runtime import build_route_decision, build_tool_invocation_plan, build_controlled_dag_plan, build_dag_trace_event, annotate_plan_with_phase3a
from router_core import normalize_analysis_plan


def test_route_decision_and_dag_contracts_descriptive():
 plan = route_and_plan('最近7天GMV', use_llm=False)
 enriched = annotate_plan_with_phase3a(plan, query='最近7天GMV')
 rd = enriched['route_decision']
 assert rd['contract'] == 'route_decision_v1'
 assert rd['execution_mode'] == 'plan_act'
 assert rd['tooling_required'] is False
 assert rd['dag_required'] is False
 assert enriched['controlled_dag']['contract'] == 'controlled_dag_plan_v1'
 assert 'route' in enriched['controlled_dag']['nodes']


def test_route_decision_for_react_and_tool_plan():
 plan = route_and_plan('昨天GMV异常下钻分析', use_llm=False)
 enriched = annotate_plan_with_phase3a(plan, query='昨天GMV异常下钻分析')
 rd = enriched['route_decision']
 assert rd['execution_mode'] == 'react'
 assert rd['tooling_required'] is True
 assert rd['dag_required'] is True
 tool_plan = build_tool_invocation_plan('warehouse.query_sql', {'sql': 'SELECT 1'}, {'risk_level': 'low', 'allowed_intents': ['anomaly']}, {'trace_id': 't1'}, {'allowed': True})
 assert tool_plan['contract'] == 'tool_invocation_plan_v1'
 assert tool_plan['tool_id'] == 'warehouse.query_sql'
 assert tool_plan['trace_id'] == 't1'


def test_dag_trace_and_terminal_status_fallback():
 dag = build_controlled_dag_plan({'status': 'ok'}, include_tool_call=False)
 assert 'tool_call' not in dag['nodes']
 event = build_dag_trace_event('route', status='blocked', reason='policy')
 assert event['status'] == 'blocked'
 assert event['reason'] == 'policy'
 normalized = normalize_analysis_plan({'status': 'need_clarification', 'clarification': {'reason': 'x'}}, query='q')
 assert normalized.status == 'need_clarification'
