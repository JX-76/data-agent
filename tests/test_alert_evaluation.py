import sys,os
sys.path.insert(0,os.path.join(os.path.dirname(__file__),"..","src"))
from slo_policy import evaluate_slo,evaluate_alerts
def test_no_alert_when_passing():assert evaluate_alerts(evaluate_slo({"success_rate":1,"p95_latency_ms":1,"pii_block_correctness":1,"quality_gate_score":1}))["alerts"]==[]
def test_alert_for_success_failure():
 a=evaluate_alerts(evaluate_slo({"success_rate":0,"p95_latency_ms":1,"pii_block_correctness":1,"quality_gate_score":1}));assert a["status"]=="alerting" and a["alerts"][0]["severity"]=="critical"
def test_multiple_alerts():assert len(evaluate_alerts(evaluate_slo({"success_rate":0,"p95_latency_ms":9999,"pii_block_correctness":0,"quality_gate_score":0}))["alerts"])>=2
def test_alert_schema():
 a=evaluate_alerts(evaluate_slo({}))["alerts"][0];assert set(["id","severity","condition","current","threshold","message"]).issubset(a)
def test_latency_is_warning():
 a=evaluate_alerts(evaluate_slo({"success_rate":1,"p95_latency_ms":9999,"pii_block_correctness":1,"quality_gate_score":1}));assert a["alerts"][0]["severity"]=="warning"
def test_contract():assert evaluate_alerts({})["contract"]=="alert_evaluation_v1"
