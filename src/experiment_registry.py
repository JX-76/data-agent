# -*- coding: utf-8 -*-
"""Declarative, tenant-scoped definitions for controlled A/B analysis."""
from __future__ import unicode_literals

class ExperimentDefinition(object):
    def __init__(self, experiment_id, metric, metric_kind, randomization_field,
                 group_field="variant", control_group="A", treatment_group="B",
                 tenant_id=None, time_range=None, metric_definition="", guardrails=None,
                 minimum_group_size=30, randomized=True):
        self.experiment_id, self.metric, self.metric_kind = experiment_id, metric, metric_kind
        self.randomization_field, self.group_field = randomization_field, group_field
        self.control_group, self.treatment_group = control_group, treatment_group
        self.tenant_id, self.time_range = tenant_id, time_range
        self.metric_definition, self.guardrails = metric_definition, list(guardrails or [])
        self.minimum_group_size, self.randomized = int(minimum_group_size), bool(randomized)
    def to_dict(self):
        return {"experiment_id": self.experiment_id, "metric": self.metric, "metric_kind": self.metric_kind,
                "randomization_field": self.randomization_field, "group_field": self.group_field,
                "control_group": self.control_group, "treatment_group": self.treatment_group,
                "tenant_id": self.tenant_id, "time_range": self.time_range,
                "metric_definition": self.metric_definition, "guardrails": list(self.guardrails),
                "minimum_group_size": self.minimum_group_size, "randomized": self.randomized,
                "capability_status": "mvp"}

class ExperimentRegistry(object):
    def __init__(self): self._items = {}
    def register(self, definition):
        if not isinstance(definition, ExperimentDefinition): raise TypeError("definition must be ExperimentDefinition")
        self._items[definition.experiment_id] = definition; return definition
    def get(self, experiment_id): return self._items.get(experiment_id)
    def resolve(self, experiment_id, tenant_id=None):
        item = self.get(experiment_id); errors=[]
        if not item: errors.append("experiment_definition_not_found")
        elif item.tenant_id and tenant_id and str(item.tenant_id) != str(tenant_id): errors.append("experiment_tenant_mismatch")
        elif not item.randomization_field or not item.group_field: errors.append("experiment_randomization_metadata_missing")
        elif item.metric_kind not in ("binary_conversion", "continuous"): errors.append("experiment_metric_kind_not_supported")
        return {"ok": not errors, "definition": item.to_dict() if item else None, "errors": errors}
    def names(self): return sorted(self._items)

def build_default_experiment_registry(): return ExperimentRegistry()
DEFAULT_EXPERIMENT_REGISTRY = build_default_experiment_registry()
def get_experiment_registry(): return DEFAULT_EXPERIMENT_REGISTRY
__all__=["ExperimentDefinition","ExperimentRegistry","get_experiment_registry"]
