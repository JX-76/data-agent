# -*- coding: utf-8 -*-
"""Normalize physical schema introspection payloads for metadata-driven SQL."""
from __future__ import unicode_literals

import copy
import hashlib
import json


def _fingerprint(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    if not isinstance(raw, bytes):
        raw = raw.encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def normalize_schema(raw_schema):
    """Return a stable schema_introspection_v1 payload.

    Accepts either {table: [columns]} or {"tables": {table: {"columns": [...]}}}.
    Column entries can be strings or dictionaries.
    """
    raw = copy.deepcopy(raw_schema or {})
    tables_in = raw.get("tables") if isinstance(raw, dict) and "tables" in raw else raw
    tables = {}
    for table_name, table_def in (tables_in or {}).items():
        table_role = None
        grain = []
        if isinstance(table_def, dict):
            cols_in = table_def.get("columns") or []
            primary_key = table_def.get("primary_key") or []
            if primary_key and not isinstance(primary_key, (list, tuple)):
                primary_key = [primary_key]
            time_column = table_def.get("time_column") or table_def.get("time_field")
            table_role = table_def.get("table_role") or table_def.get("role")
            grain = table_def.get("grain") or []
        else:
            cols_in = table_def or []
            primary_key = []
            time_column = None
        columns = []
        for col in cols_in:
            if isinstance(col, dict):
                item = {"name": col.get("name"), "type": col.get("type") or col.get("data_type") or "unknown"}
                if col.get("nullable") is not None:
                    item["nullable"] = bool(col.get("nullable"))
                if col.get("primary_key"):
                    item["primary_key"] = True
                    if item["name"] not in primary_key:
                        primary_key.append(item["name"])
            else:
                item = {"name": str(col), "type": "unknown"}
            if item.get("name"):
                columns.append(item)
        if not time_column:
            for candidate in ("date", "day", "order_date", "created_at", "dt"):
                if candidate in [c.get("name") for c in columns]:
                    time_column = candidate
                    break
        entry = {"columns": columns, "primary_key": list(primary_key or []), "time_column": time_column}
        if table_role:
            entry["table_role"] = table_role
        if grain:
            if isinstance(grain, (list, tuple)):
                entry["grain"] = list(grain)
            else:
                entry["grain"] = [grain]
        tables[table_name] = entry
    return {"contract": "schema_introspection_v1", "tables": tables, "fingerprint": _fingerprint(tables)}


def table_columns(schema, table_name):
    data = schema if isinstance(schema, dict) else {}
    tables = data.get("tables") or {}
    table = tables.get(table_name) or {}
    return [c.get("name") for c in (table.get("columns") or []) if isinstance(c, dict) and c.get("name")]


__all__ = ["normalize_schema", "table_columns"]
