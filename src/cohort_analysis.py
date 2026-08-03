# -*- coding: utf-8 -*-
"""Deterministic aggregate cohort calculation; never emits entity identifiers."""
from __future__ import unicode_literals
from datetime import datetime

def _date(v): return datetime.strptime(str(v)[:10], "%Y-%m-%d").date()
def _bucket(day, grain):
    if grain == "week":
        monday = day.fromordinal(day.toordinal() - day.weekday()); return monday.isoformat()
    if grain == "month": return day.strftime("%Y-%m")
    return day.isoformat()
def _offset(start, current, grain):
    if grain == "day": return (current - start).days
    if grain == "week": return (current - start).days // 7
    return (current.year-start.year)*12 + current.month-start.month

def analyze_cohort_events(events, definition, horizons=None, tenant_id=None):
    """Compute acquisition-based retention from event records in memory for sandbox/tests."""
    definition = definition.to_dict() if hasattr(definition, "to_dict") else dict(definition or {})
    entity, acquisition, active, grain = definition.get("entity_key"), definition.get("acquisition_event"), definition.get("active_event"), definition.get("period_grain")
    if not entity or not acquisition or not active or grain not in ("day", "week", "month"):
        return {"status":"need_clarification", "matrix":[], "summary_facts":{}, "caveats":["缺少有效 cohort 定义、实体键或时间粒度。"]}
    if tenant_id is not None and not definition.get("tenant_column"):
        return {"status":"unsupported", "matrix":[], "summary_facts":{}, "caveats":["当前 cohort 数据模型不支持 tenant 隔离。"]}
    selected=[]
    for e in events or []:
        if not isinstance(e,dict) or not e.get(entity) or not e.get("event_date"): continue
        if tenant_id is not None and e.get(definition.get("tenant_column")) != tenant_id: continue
        selected.append(e)
    acquired={}
    for e in selected:
        if e.get("event_name") == acquisition:
            d=_date(e["event_date"]); key=e[entity]
            if key not in acquired or d < acquired[key]: acquired[key]=d
    horizons=list(horizons or definition.get("retention_horizons") or [])
    wanted=[]
    for h in horizons:
        try: wanted.append((h,int(h[1:])))
        except Exception: pass
    cohorts={}
    for key, start in acquired.items(): cohorts.setdefault(_bucket(start,grain), {"start":start,"entities":set()})["entities"].add(key)
    active_by={}
    for e in selected:
        if e.get("event_name") != active or e[entity] not in acquired: continue
        off=_offset(acquired[e[entity]], _date(e["event_date"]), grain)
        active_by.setdefault((_bucket(acquired[e[entity]],grain),off),set()).add(e[entity])
    matrix=[]
    for cohort, info in sorted(cohorts.items()):
        denominator=len(info["entities"])
        for label, off in wanted:
            active_users=len(active_by.get((cohort,off),set()) & info["entities"])
            matrix.append({"cohort":cohort,"period":label,"cohort_size":denominator,"active_users":active_users,"retention_rate":(float(active_users)/denominator if denominator else None)})
    return {"status":"ok" if matrix else "insufficient_data", "definition":definition, "matrix":matrix, "items":matrix, "summary_facts":{"cohort_count":len(cohorts),"row_count":len(matrix),"sample_size":sum(len(x["entities"]) for x in cohorts.values()),"horizons":horizons}, "caveats":list(definition.get("caveats") or [])+["用户标识仅用于去重计算，输出不包含 user_id 或用户轨迹。"]}

__all__=["analyze_cohort_events"]
