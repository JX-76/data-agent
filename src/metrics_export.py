# -*- coding: utf-8 -*-
"""Tenant-isolated in-memory metrics and Prometheus text adapter."""
from __future__ import unicode_literals
class MetricsRegistry(object):
 def __init__(self): self.data={}
 def _d(self,t): return self.data.setdefault(t or "default",{"count":0,"status":{},"failure":{},"latencies":[],"quality":[],"governance":0,"human_review":0,"cache_hit":0,"cache_miss":0,"pii_block_total":0,"pii_block_correct":0,"external_latency":[],"datasource_latency":[]})
 def record(self,tenant_id="default",status="ok",latency_ms=0,failure_stage=None,quality_score=None,governance=False,human_review=False,cache_hit=None,pii_block=None,external_latency_ms=None,datasource_latency_ms=None):
  d=self._d(tenant_id);d["count"]+=1;d["status"][status]=d["status"].get(status,0)+1;d["latencies"].append(float(latency_ms or 0))
  if failure_stage:d["failure"][failure_stage]=d["failure"].get(failure_stage,0)+1
  if quality_score is not None:d["quality"].append(float(quality_score))
  if governance:d["governance"]+=1
  if human_review:d["human_review"]+=1
  if cache_hit is True:d["cache_hit"]+=1
  if cache_hit is False:d["cache_miss"]+=1
  if pii_block is not None:d["pii_block_total"]+=1;d["pii_block_correct"]+=int(bool(pii_block))
  if external_latency_ms is not None:d["external_latency"].append(float(external_latency_ms))
  if datasource_latency_ms is not None:d["datasource_latency"].append(float(datasource_latency_ms))
 def summary(self,tenant_id="default"):
  d=self._d(tenant_id);n=max(1,d["count"]);l=sorted(d["latencies"]);p95=l[int(round((len(l)-1)*.95))] if l else 0
  return {"contract":"metrics_summary_v1","request_count":d["count"],"terminal_status":dict(d["status"]),"failure_categories":dict(d["failure"]),"p95_latency_ms":p95,"success_rate":round(d["status"].get("ok",0)/float(n),4),"quality_gate_score":round(sum(d["quality"])/float(max(1,len(d["quality"]))),4),"governance_decisions":d["governance"],"human_reviews":d["human_review"],"cache_hits":d["cache_hit"],"cache_misses":d["cache_miss"],"pii_block_correctness":round(d["pii_block_correct"]/float(max(1,d["pii_block_total"])),4),"external_tool_latency_ms":d["external_latency"],"datasource_latency_ms":d["datasource_latency"]}
 def prometheus(self,tenant_id="default"):
  s=self.summary(tenant_id);lines=["# TYPE data_agent_requests_total counter","data_agent_requests_total "+str(s["request_count"]),"# TYPE data_agent_latency_p95_ms gauge","data_agent_latency_p95_ms "+str(s["p95_latency_ms"]),"data_agent_quality_gate_score "+str(s["quality_gate_score"])]
  for k,v in s["terminal_status"].items():lines.append('data_agent_terminal_status_total{status="%s"} %s'%(k,v))
  return "\n".join(lines)+"\n"
_GLOBAL=MetricsRegistry()
def get_metrics_registry(): return _GLOBAL
__all__=["MetricsRegistry","get_metrics_registry"]
