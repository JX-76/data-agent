# -*- coding: utf-8 -*-
"""Privacy-safe aggregate A/B analysis entry point."""
from __future__ import unicode_literals
from experiment_strategies import get_experiment_strategy_registry
from strategy_evidence import build_need_more_data_analysis
 
def analyze_experiment(definition, rows, tenant_id=None):
    d=definition.to_dict() if hasattr(definition,"to_dict") else dict(definition or {})
    if not rows:
        return build_need_more_data_analysis("experiment", {"task_type":"experiment","metric":d.get("metric")}, {"status":"ok","results":[]}, {"row_count":0,"reasons":["experiment_rows_missing"],"evidence_ids":[]})
    required=["experiment_id","metric","metric_kind","randomization_field","group_field","control_group","treatment_group"]
    missing=[x for x in required if not d.get(x)]
    if missing: return {"status":"need_clarification","task_type":"experiment","diagnostics":{"errors":["missing_experiment_definition:%s"%x for x in missing]},"results":[]}
    if d.get("tenant_id") and tenant_id and str(d["tenant_id"])!=str(tenant_id): return {"status":"blocked","task_type":"experiment","diagnostics":{"errors":["experiment_tenant_mismatch"]},"results":[]}
    uid,group,metric=d["randomization_field"],d["group_field"],d["metric"]
    errors=[]; assignments={}; values={d["control_group"]:[],d["treatment_group"]:[]}
    for row in rows or []:
        if uid not in row or group not in row or metric not in row: errors.append("experiment_required_field_missing"); continue
        key=row[uid]; value=row[group]
        if key in assignments and assignments[key]!=value: errors.append("experiment_group_contamination")
        assignments[key]=value
        if value in values:
            try: values[value].append(float(row[metric]))
            except Exception: errors.append("experiment_metric_not_numeric")
    n0,n1=len(values[d["control_group"]]),len(values[d["treatment_group"]])
    caveats=[]
    if len(assignments) < len(rows or []): caveats.append("输入含重复随机化单位；统计前已按输入行聚合，建议上游去重。")
    if errors or n0 < d.get("minimum_group_size",30) or n1 < d.get("minimum_group_size",30):
        errors += ["experiment_minimum_group_size_not_met"] if n0 < d.get("minimum_group_size",30) or n1 < d.get("minimum_group_size",30) else []
        return {"status":"need_more_data","task_type":"experiment","results":[],"diagnostics":{"errors":sorted(set(errors)),"group_sizes":{"control":n0,"treatment":n1},"quality":{"empty_result":True}},"caveats":caveats,"evidence_assessment":{"ok":False,"status":"need_more_data","reasons":sorted(set(errors)),"row_count":len(rows or [])}}
    strategy=get_experiment_strategy_registry().get(d["metric_kind"])
    if not strategy: return {"status":"need_clarification","task_type":"experiment","results":[],"diagnostics":{"errors":["experiment_metric_kind_not_supported"]}}
    output=strategy.analyze(values[d["control_group"]],values[d["treatment_group"]])
    randomized=bool(d.get("randomized"))
    if not randomized:
        output["p_value"]=None; output["confidence_interval"]=None; output["significance"]=None
        caveats.append("该比较缺少可信随机分组元数据，仅为观察性描述对比，不能推断因果效果或统计显著性。")
    else:
        output["significance"]="significant" if output["p_value"] < .05 else "not_significant"
        caveats.append("统计结果存在抽样不确定性；显著性不代表业务必然提升。")
    output.update({"control_n":n0,"treatment_n":n1,"guardrail":list(d.get("guardrails") or []),"metric_kind":d["metric_kind"]})
    return {"status":"ok","task_type":"experiment","analysis":{"summary_facts":output,"definition":{"experiment_id":d["experiment_id"],"metric":metric,"metric_kind":d["metric_kind"],"method":output["method"]}},"results":[output],"diagnostics":{"quality":{"aggregate_only":True,"row_count":2},"experiment": {"group_sizes":{"control":n0,"treatment":n1},"method":output["method"]}},"caveats":caveats,"evidence_assessment":{"ok":True,"status":"ok","reasons":[],"row_count":2}}
__all__=["analyze_experiment"]
