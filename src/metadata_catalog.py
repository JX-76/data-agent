# -*- coding: utf-8 -*-
"""Execution-facing catalog built from versioned semantics and physical schema."""
from __future__ import unicode_literals

import hashlib
import json

from schema_introspection import normalize_schema
from semantic_registry import get_semantic_registry


def _normalize_joins(joins):
    if isinstance(joins, dict):
        return joins
    result = {}
    for item in joins or []:
        if not isinstance(item, dict):
            continue
        join_id = item.get("id") or "%s_to_%s" % (item.get("left_table"), item.get("right_table"))
        result[join_id] = item
    return result


def _fingerprint(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    if not isinstance(raw, bytes):
        raw = raw.encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def build_metadata_catalog(registry=None, physical_schema=None):
    registry = registry or get_semantic_registry()
    payload = registry.get() if hasattr(registry, "get") else {}
    embedded = payload.get("tables") or {}
    schema = normalize_schema(physical_schema if physical_schema is not None else embedded)
    models = payload.get("models") or {}
    metrics = payload.get("metrics") or {}
    dimensions = payload.get("dimensions") or {}
    catalog = {
        "contract": "metadata_catalog_v1",
        "semantic_version": registry.get_version() if hasattr(registry, "get_version") else None,
        "schema_contract": schema.get("contract"),
        "schema_fingerprint": schema.get("fingerprint"),
        "models": models, "metrics": metrics, "dimensions": dimensions,
        "tables": schema.get("tables") or {}, "joins": _normalize_joins(payload.get("joins") or {}),
    }
    catalog["fingerprint"] = _fingerprint({"semantic_version": catalog["semantic_version"], "models": models,
                                            "metrics": metrics, "dimensions": dimensions, "tables": catalog["tables"],
                                            "joins": catalog["joins"]})
    return catalog


def resolve_model_table(catalog, model_id):
    models = (catalog or {}).get("models") or {}
    model = models.get(model_id) or {}
    return model.get("table") or model.get("base_table") or model_id


def resolve_field(definition, fallback):
    definition = definition or {}
    return definition.get("field") or definition.get("source_field") or definition.get("column") or fallback


__all__ = ["build_metadata_catalog", "resolve_model_table", "resolve_field"]
