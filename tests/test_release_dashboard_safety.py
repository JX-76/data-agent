import sys,os
sys.path.insert(0,os.path.join(os.path.dirname(__file__),"..","src"))
from release_api import ask_release,release_history,release_metrics,release_slo_status,release_alerts,release_quality_trend,release_approval_summary
def test_history_safe_no_query_sql_rows():
 ask_release("最近7天GMV",session_id="r32safe",use_llm=False);x=release_history();assert all("query" not in i and "sql" not in i and "rows" not in i for i in x["items"])
def test_metrics_contract():assert release_metrics()["contract"]=="metrics_summary_v1"
def test_slo_and_alert_api():assert release_slo_status()["contract"]=="slo_status_v1" and release_alerts()["contract"]=="alert_evaluation_v1"
def test_trend_safe():
 x=release_quality_trend();assert x["contract"]=="quality_trend_v1" and all("query" not in i for i in x["items"])
def test_approval_summary_safe():
 x=release_approval_summary();assert x["contract"]=="approval_summary_v1" and "query" not in x
def test_history_tenant_filter():assert release_history(tenant_id="not-present")["items"]==[]
