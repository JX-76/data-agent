import sys,os
sys.path.insert(0,os.path.join(os.path.dirname(__file__),"..","src"))
from slo_policy import evaluate_slo,DEFAULT_SLO_POLICY
def good():return {"success_rate":.99,"p95_latency_ms":100,"pii_block_correctness":1,"quality_gate_score":.9}
def test_default_policy_passes():assert evaluate_slo(good())["passed"]
def test_success_rate_fails():assert not evaluate_slo(dict(good(),success_rate=.5))["passed"]
def test_p95_fails():assert not evaluate_slo(dict(good(),p95_latency_ms=5000))["passed"]
def test_pii_fails():assert not evaluate_slo(dict(good(),pii_block_correctness=.5))["passed"]
def test_quality_fails():assert not evaluate_slo(dict(good(),quality_gate_score=.5))["passed"]
def test_custom_policy():assert evaluate_slo(dict(good(),success_rate=.5),{"success_rate_min":.4})["passed"]
def test_stable_contract():
 x=evaluate_slo(good());assert x["contract"]=="slo_status_v1" and len(x["objectives"])==4
