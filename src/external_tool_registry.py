# -*- coding: utf-8 -*-
"""External tool registry for Data Agent.

Defines a small, governed set of tools that interact with systems outside the
agent runtime. Python 2.7 compatible and dependency-light.
"""
from __future__ import unicode_literals

import codecs
import os

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEFAULT_EXTERNAL_TOOLS = [
    {
        "tool_id": "semantic.catalog_read",
        "category": "semantic",
        "description": "Read semantic catalog snapshot",
        "input_schema": {"required": [], "properties": {}},
        "output_schema": {"required": ["semantic_version", "metrics", "dimensions", "models"]},
        "timeout_ms": 1000,
        "risk_level": "low",
        "side_effect": "read_only",
        "requires_human_review": False,
        "allowed_intents": ["metric_query", "breakdown", "comparison", "anomaly", "attribution", "unsupported"],
        "idempotent": True,
    },
    {
        "tool_id": "warehouse.schema_introspect",
        "category": "data_source",
        "description": "Read physical schema from readonly warehouse adapter",
        "input_schema": {"required": [], "properties": {}},
        "output_schema": {"required": ["schema"]},
        "timeout_ms": 3000,
        "risk_level": "low",
        "side_effect": "read_only",
        "requires_human_review": False,
        "allowed_intents": ["metric_query", "breakdown", "comparison", "anomaly", "attribution"],
        "idempotent": True,
    },
    {
        "tool_id": "warehouse.query_sql",
        "category": "data_source",
        "description": "Execute readonly SELECT/WITH SQL against warehouse adapter",
        "input_schema": {
            "required": ["sql"],
            "properties": {
                "sql": {"type": "string", "min_len": 1},
                "limit": {"type": "integer", "min": 0, "max": 1000},
                "offset": {"type": "integer", "min": 0, "max": 100000},
            },
        },
        "output_schema": {"required": ["rows", "row_count", "sql"]},
        "timeout_ms": 8000,
        "risk_level": "medium",
        "side_effect": "read_only",
        "requires_human_review": False,
        "allowed_intents": ["metric_query", "breakdown", "comparison", "anomaly", "attribution"],
        "forbidden_operations": ["insert", "update", "delete", "drop", "alter", "truncate", "export"],
        "idempotent": True,
    },
    {
        "tool_id": "ecommerce.overview",
        "category": "ecommerce_analytics",
        "description": "Read shop-level metric anomaly overview for the diagnostic state machine",
        "input_schema": {"required": [], "properties": {"metric": {"type": "string"}, "time_range": {"type": "string"}}},
        "output_schema": {"required": ["metric", "current", "baseline", "delta_pct"]},
        "timeout_ms": 1000,
        "risk_level": "low",
        "side_effect": "read_only",
        "requires_human_review": False,
        "allowed_intents": ["anomaly", "attribution", "metric_query"],
        "idempotent": True,
    },
    {
        "tool_id": "ecommerce.channel_performance",
        "category": "ecommerce_analytics",
        "description": "Read channel breakdown evidence for ecommerce anomaly diagnosis",
        "input_schema": {"required": [], "properties": {"metric": {"type": "string"}, "time_range": {"type": "string"}}},
        "output_schema": {"required": ["rows", "row_count"]},
        "timeout_ms": 1000,
        "risk_level": "low",
        "side_effect": "read_only",
        "requires_human_review": False,
        "allowed_intents": ["anomaly", "attribution", "breakdown"],
        "idempotent": True,
    },
    {
        "tool_id": "ecommerce.product_performance",
        "category": "ecommerce_analytics",
        "description": "Read product/SKU breakdown evidence for ecommerce anomaly diagnosis",
        "input_schema": {"required": [], "properties": {"metric": {"type": "string"}, "time_range": {"type": "string"}}},
        "output_schema": {"required": ["rows", "row_count"]},
        "timeout_ms": 1000,
        "risk_level": "low",
        "side_effect": "read_only",
        "requires_human_review": False,
        "allowed_intents": ["anomaly", "attribution", "breakdown"],
        "idempotent": True,
    },
    {
        "tool_id": "ecommerce.review_sentiment",
        "category": "ecommerce_market",
        "description": "Read review sentiment snapshot as hypothesis evidence",
        "input_schema": {"required": [], "properties": {"product_id": {"type": "string"}}},
        "output_schema": {"required": ["negative_rate", "top_terms"]},
        "timeout_ms": 1000,
        "risk_level": "low",
        "side_effect": "read_only",
        "requires_human_review": False,
        "allowed_intents": ["anomaly", "attribution"],
        "idempotent": True,
    },
    {
        "tool_id": "ecommerce.competitor_price",
        "category": "ecommerce_market",
        "description": "Read competitor price snapshot with TTL for diagnostic hypotheses",
        "input_schema": {"required": [], "properties": {"product_id": {"type": "string"}}},
        "output_schema": {"required": ["items", "ttl_seconds"]},
        "timeout_ms": 1000,
        "risk_level": "low",
        "side_effect": "read_only",
        "requires_human_review": False,
        "allowed_intents": ["anomaly", "attribution"],
        "idempotent": True,
    },
    {
        "tool_id": "harness.run_suite",
        "category": "ops",
        "description": "Run a harness suite by name or JSONL path",
        "input_schema": {
            "required": ["suite"],
            "properties": {"suite": {"type": "string", "min_len": 1}},
        },
        "output_schema": {"required": ["suite", "metrics"]},
        "timeout_ms": 30000,
        "risk_level": "low",
        "side_effect": "read_only",
        "requires_human_review": False,
        "allowed_intents": ["ops", "evaluation"],
        "idempotent": True,
    },
]


def _load_yaml(path):
    if yaml is None or not os.path.exists(path):
        return None
    with codecs.open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f.read())


class ExternalToolRegistry(object):
    def __init__(self, config_path=None, tools=None):
        self.config_path = config_path or os.path.join(BASE, "rules", "external_tools.yaml")
        self.tools = {}
        loaded = None
        try:
            loaded = _load_yaml(self.config_path)
        except Exception:
            loaded = None
        items = tools or (loaded.get("tools") if isinstance(loaded, dict) else None) or DEFAULT_EXTERNAL_TOOLS
        for item in items:
            self.register(item)

    def register(self, spec):
        spec = dict(spec or {})
        tool_id = spec.get("tool_id")
        if not tool_id:
            raise ValueError("external tool spec missing tool_id")
        self.tools[tool_id] = spec
        return spec

    def get(self, tool_id):
        spec = self.tools.get(tool_id)
        return dict(spec) if spec else None

    def list_tools(self):
        return [dict(self.tools[k]) for k in sorted(self.tools.keys())]


_REGISTRY = ExternalToolRegistry()


def get_external_tool_registry():
    return _REGISTRY


__all__ = ["ExternalToolRegistry", "get_external_tool_registry", "DEFAULT_EXTERNAL_TOOLS"]
