import sys,os
sys.path.insert(0,os.path.join(os.path.dirname(__file__),"..","src"))
from metrics_export import MetricsRegistry
def test_count_status_latency():
 r=MetricsRegistry();r.record("a","ok",10,quality_score=.9);r.record("a","error",100,failure_stage="execution");s=r.summary("a");assert s["request_count"]==2 and s["terminal_status"]["ok"]==1 and s["p95_latency_ms"]==100
def test_failure_categories():
 r=MetricsRegistry();[r.record("a","error",1,failure_stage=x) for x in ("route","planning","execution","analysis","report")];assert len(r.summary("a")["failure_categories"])==5
def test_tenant_isolation():
 r=MetricsRegistry();r.record("a");r.record("b");assert r.summary("a")["request_count"]==1 and r.summary("b")["request_count"]==1
def test_cache_governance_review():
 r=MetricsRegistry();r.record("a",cache_hit=True,governance=True,human_review=True);s=r.summary("a");assert s["cache_hits"]==1 and s["governance_decisions"]==1 and s["human_reviews"]==1
def test_pii_and_optional_latency():
 r=MetricsRegistry();r.record("a",pii_block=True,external_latency_ms=3,datasource_latency_ms=4);s=r.summary("a");assert s["pii_block_correctness"]==1 and s["external_tool_latency_ms"]==[3.0]
def test_prometheus_safe_and_valid():
 r=MetricsRegistry();r.record("a");p=r.prometheus("a");assert "# TYPE" in p and "tenant" not in p and "sql" not in p
