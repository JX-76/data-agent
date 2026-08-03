# -*- coding: utf-8 -*-
"""Stable product-facing chart spec schema."""
from __future__ import unicode_literals
class ChartSpec(object):
 def __init__(self,type="none",title="",x=None,y=None,series=None,data=None,annotations=None,reason="",explanation="",policy_id=""):
  self.type,self.title,self.x,self.y,self.series=type or "none",title or "",x,y,series; self.data=data or [];self.annotations=annotations or [];self.reason=reason or "";self.explanation=explanation or "";self.policy_id=policy_id or ""
 def to_dict(self): return {"type":self.type,"title":self.title,"x":self.x,"y":self.y,"series":self.series,"data":list(self.data),"annotations":list(self.annotations),"reason":self.reason,"explanation":self.explanation,"policy_id":self.policy_id}
_POLICY_EXPLANATIONS={"comparison":("comparison_by_dimension","使用对比图展示不同周期或分组之间的指标差异，便于识别增减变化。"),"anomaly timeline":("anomaly_timeline","使用带异常标记的趋势图展示时间变化，并突出需要优先复核的异常点。"),"driver contribution":("attribution_waterfall","使用瀑布图拆解各驱动因素对总体变化的正负贡献。"),"funnel conversion":("funnel_conversion","使用漏斗图展示各阶段转化量与流失位置。"),"cohort retention":("retention_cohort","使用热力图查看不同 cohort 在各观察周期的留存表现。"),"forecast trend overlay":("forecast_trend_overlay","使用趋势叠加图区分历史值、预测值和可用的置信区间。"),"trend analysis":("time_trend","使用折线图展示指标随时间的连续变化趋势。"),"dimension breakdown":("dimension_breakdown","使用柱状图比较不同业务维度的指标规模。"),"multi-dimension breakdown":("multi_dimension_breakdown","使用分组柱状图同时比较两个业务维度的指标差异。"),"non-final result":("non_final","当前请求未进入可展示结果阶段，因此不生成图表。"),"no chart generated":("no_chart","当前结果缺少可可靠展示的时间或维度结构，因此不推荐图表。"),"error no chart":("error_no_chart","当前请求执行失败，无法生成图表。"),"empty result no chart":("empty_result_no_chart","查询结果为空，无可展示数据，不生成图表。"),"fallback no chart":("fallback_no_chart","当前请求已进入降级路径，暂不生成图表。"),"pending review no chart":("pending_review_no_chart","当前请求待人工审核，暂不生成图表。")}
def _chart_explanation(reason,explanation,policy_id):
 rule=_POLICY_EXPLANATIONS.get(reason)
 if rule:return explanation or rule[1],policy_id or rule[0]
 return (explanation or "",policy_id or ("external_or_unspecified" if not explanation else ""))
def make_chart_spec(type="none",title="",x=None,y=None,series=None,data=None,annotations=None,reason="",explanation="",policy_id=""):
 explanation,policy_id=_chart_explanation(reason,explanation,policy_id);return ChartSpec(type,title,x,y,series,data,annotations,reason,explanation,policy_id).to_dict()
def normalize_chart_spec(chart):
 if chart is None:return make_chart_spec(reason="no chart generated")
 if hasattr(chart,"to_dict"):chart=chart.to_dict()
 chart=dict(chart or {});return make_chart_spec(chart.get("type") or "none",chart.get("title") or "",chart.get("x"),chart.get("y"),chart.get("series"),chart.get("data") or [],chart.get("annotations") or [],chart.get("reason") or "",chart.get("explanation") or "",chart.get("policy_id") or "")
TASK_TYPE_CHART_POLICY={"descriptive":("bar","dimension breakdown","descriptive_default"),"experiment":("grouped_bar","comparison","experiment_default"),"comparison":("grouped_bar","comparison","comparison_default"),"attribution":("waterfall","driver contribution","attribution_default"),"anomaly":("line_with_anomaly","anomaly timeline","anomaly_default"),"funnel":("funnel","funnel conversion","funnel_default"),"retention":("heatmap","cohort retention","retention_default"),"forecast":("forecast_trend_overlay","forecast trend overlay","forecast_default"),"trend":("line","trend analysis","trend_default")}
DEGRADATION_CHART_POLICY={"blocked":("none","non-final result","blocked_no_chart"),"need_clarification":("none","non-final result","clarification_no_chart"),"clarification_needed":("none","non-final result","clarification_no_chart"),"error":("none","error no chart","error_no_chart"),"fallback":("none","fallback no chart","fallback_no_chart"),"pending_human_review":("none","pending review no chart","pending_review_no_chart"),"empty_result":("none","empty result no chart","empty_result_no_chart")}
def recommend_chart_for_task_type(task_type,status=None,empty_result=False):
 if status in DEGRADATION_CHART_POLICY: p=DEGRADATION_CHART_POLICY[status]
 elif empty_result:p=DEGRADATION_CHART_POLICY["empty_result"]
 else:p=TASK_TYPE_CHART_POLICY.get(task_type or "descriptive")
 return make_chart_spec(type=p[0],reason=p[1],policy_id=p[2]) if p else make_chart_spec(reason="no chart generated")
__all__=["ChartSpec","make_chart_spec","normalize_chart_spec","TASK_TYPE_CHART_POLICY","DEGRADATION_CHART_POLICY","recommend_chart_for_task_type"]
