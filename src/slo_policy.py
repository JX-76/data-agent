# -*- coding: utf-8 -*-
"""Configurable SLO policy and structured, vendor-neutral alert evaluation."""
from __future__ import unicode_literals
DEFAULT_SLO_POLICY={"success_rate_min":.95,"p95_latency_ms_max":3000,"pii_block_correctness_min":.99,"quality_gate_score_min":.80}
def evaluate_slo(metrics,policy=None):
 p=dict(DEFAULT_SLO_POLICY);p.update(policy or {});m=metrics or {}
 values={"success_rate":float(m.get("success_rate",0)),"p95_latency_ms":float(m.get("p95_latency_ms",0)),"pii_block_correctness":float(m.get("pii_block_correctness",1)),"quality_gate_score":float(m.get("quality_gate_score",1))}
 checks=[("success_rate","min",values["success_rate"],p["success_rate_min"]),("p95_latency_ms","max",values["p95_latency_ms"],p["p95_latency_ms_max"]),("pii_block_correctness","min",values["pii_block_correctness"],p["pii_block_correctness_min"]),("quality_gate_score","min",values["quality_gate_score"],p["quality_gate_score_min"])]
 items=[{"name":n,"operator":o,"current":v,"threshold":t,"passed":v>=t if o=="min" else v<=t} for n,o,v,t in checks]
 return {"contract":"slo_status_v1","passed":all(x["passed"] for x in items),"policy":p,"objectives":items}
def evaluate_alerts(slo_status):
 alerts=[]
 for o in (slo_status or {}).get("objectives",[]):
  if not o["passed"]: alerts.append({"id":"slo_"+o["name"],"severity":"critical" if o["name"] in ("pii_block_correctness","success_rate") else "warning","condition":o["name"]+" "+o["operator"]+" threshold breached","current":o["current"],"threshold":o["threshold"],"message":"SLO breach: "+o["name"]})
 return {"contract":"alert_evaluation_v1","status":"alerting" if alerts else "ok","alerts":alerts}
__all__=["DEFAULT_SLO_POLICY","evaluate_slo","evaluate_alerts"]
