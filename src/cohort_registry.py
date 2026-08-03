# -*- coding: utf-8 -*-
"""Declarative, privacy-safe cohort definitions for retention execution."""
from __future__ import unicode_literals

VALID_GRAINS = ("day", "week", "month")

class CohortDefinition(object):
    def __init__(self, cohort_id, entity_key, acquisition_event, active_event,
                 period_grain, retention_horizons, model, source=None,
                 timezone="UTC", capability_status="supported", methodology="",
                 caveats=None, tenant_column=None):
        self.cohort_id = cohort_id
        self.entity_key = entity_key
        self.acquisition_event = acquisition_event
        self.active_event = active_event
        self.period_grain = period_grain
        self.retention_horizons = list(retention_horizons or [])
        self.model = model
        self.source = source or model
        self.timezone = timezone
        self.capability_status = capability_status
        self.methodology = methodology
        self.caveats = list(caveats or [])
        self.tenant_column = tenant_column
    def to_dict(self):
        return dict((key, getattr(self, key)) for key in (
            "cohort_id", "entity_key", "acquisition_event", "active_event",
            "period_grain", "retention_horizons", "model", "source", "timezone",
            "capability_status", "methodology", "caveats", "tenant_column"))
    def validate(self, grain=None, horizons=None):
        errors = []
        if not self.cohort_id or not self.entity_key or not self.acquisition_event or not self.active_event or not self.model:
            errors.append("cohort_definition_incomplete")
        use_grain = grain or self.period_grain
        if use_grain not in VALID_GRAINS:
            errors.append("unsupported_cohort_grain")
        for horizon in horizons or self.retention_horizons:
            if horizon not in self.retention_horizons:
                errors.append("unsupported_retention_horizon:%s" % horizon)
        if self.capability_status != "supported":
            errors.append("cohort_capability_%s" % self.capability_status)
        return {"ok": not errors, "errors": errors, "definition": self.to_dict()}

class CohortRegistry(object):
    def __init__(self): self._items = {}
    def register(self, definition): self._items[definition.cohort_id] = definition; return definition
    def get(self, cohort_id): return self._items.get(cohort_id)
    def names(self): return sorted(self._items.keys())
    def resolve(self, cohort_id=None, grain=None, horizons=None):
        item = self.get(cohort_id)
        if item is None: return {"ok": False, "errors": ["cohort_definition_missing"], "definition": None}
        return item.validate(grain, horizons)

def build_default_cohort_registry():
    registry = CohortRegistry()
    registry.register(CohortDefinition("registration_daily", "user_id", "register", "active", "day", ["D1", "D7", "D30"], "user_events", methodology="以首次注册日为 cohort 起点；分母为 cohort 内去重用户数，分子为对应天数发生至少一次活跃事件的去重用户数。", caveats=["仅输出聚合 cohort，不输出用户标识或轨迹。", "迟到事件和时区变更可能导致历史值回算。"], tenant_column="tenant_id"))
    registry.register(CohortDefinition("first_purchase_weekly", "user_id", "purchase", "purchase", "week", ["W1", "W4"], "user_events", methodology="以首次购买周为 cohort 起点；复购定义为后续周至少一次去重购买。", caveats=["退款/撤销订单是否计入取决于 source 事件过滤。"], tenant_column="tenant_id"))
    return registry
DEFAULT_COHORT_REGISTRY = build_default_cohort_registry()
def get_cohort_registry(): return DEFAULT_COHORT_REGISTRY
__all__ = ["CohortDefinition", "CohortRegistry", "get_cohort_registry", "VALID_GRAINS"]
