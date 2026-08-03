# -*- coding: utf-8 -*-
"""Metadata-driven join planner for compiled metric SQL.

The planner is deliberately narrow: it finds the smallest declared join path from
one base table to the tables referenced by registered metric/dimension fields.
It never infers physical join keys from user input. R10 adds a conservative
fanout safety check so additive metrics are not silently duplicated by declared
one-to-many or many-to-many joins.
"""
from __future__ import unicode_literals

import re


_FIELD_TABLE = re.compile(r"(?:DATE\()?\s*([A-Za-z_][A-Za-z0-9_]*)\.")


def referenced_tables(*expressions):
    """Return declared table qualifiers used by registered expressions."""
    found = []
    for expression in expressions:
        for table in _FIELD_TABLE.findall(str(expression or "")):
            if table not in found:
                found.append(table)
    return found


def _join_records(catalog):
    joins = (catalog or {}).get("joins") or {}
    if isinstance(joins, dict):
        values = joins.values()
    else:
        values = joins
    records = []
    for item in values:
        if not isinstance(item, dict):
            continue
        left = item.get("left_table")
        right = item.get("right_table")
        condition = item.get("condition")
        if left and right and condition:
            records.append(item)
    return records


def _neighbors(table, joins):
    result = []
    for join in joins:
        if join.get("left_table") == table:
            result.append((join.get("right_table"), join))
        elif join.get("right_table") == table:
            result.append((join.get("left_table"), join))
    return result


def _path_to_target(base_table, target_table, joins):
    if target_table == base_table:
        return []
    queue = [(base_table, [])]
    visited = set([base_table])
    while queue:
        current, path = queue.pop(0)
        for neighbor, join in _neighbors(current, joins):
            if neighbor in visited:
                continue
            candidate = path + [join]
            if neighbor == target_table:
                return candidate
            visited.add(neighbor)
            queue.append((neighbor, candidate))
    return None


def _fanout_safety(join_plan, metric_def):
    metric_def = metric_def or {}
    errors = []
    warnings = []
    if metric_def.get("fanout_safe") is True:
        return {"contract": "fanout_safety_v1", "ok": True,
                "errors": errors, "warnings": warnings}
    for join in (join_plan or {}).get("joins") or []:
        card = join.get("cardinality") or join.get("relationship")
        if card in ("one_to_many", "many_to_many"):
            errors.append("fanout risk on join %s: %s" % (join.get("id"), card))
        elif not card:
            warnings.append("join %s has no cardinality contract" % (join.get("id")))
    return {"contract": "fanout_safety_v1", "ok": not errors,
            "errors": errors, "warnings": warnings}


def plan_joins(catalog, base_table, required_tables, metric_def=None, enforce_fanout=False):
    """Build a deterministic join plan from declared catalog metadata."""
    joins = _join_records(catalog)
    required = []
    for table in required_tables or []:
        if table and table != base_table and table not in required:
            required.append(table)
    selected = []
    errors = []
    for target in required:
        path = _path_to_target(base_table, target, joins)
        if path is None:
            errors.append("no declared join path from %s to %s" % (base_table, target))
            continue
        for join in path:
            join_id = join.get("id") or join.get("condition")
            if not any((item.get("id") or item.get("condition")) == join_id for item in selected):
                selected.append(join)
    result = {
        "contract": "join_plan_v1",
        "ok": not errors,
        "base_table": base_table,
        "required_tables": required,
        "joins": selected,
        "errors": errors,
    }
    safety = _fanout_safety(result, metric_def)
    result["fanout_safety"] = safety
    if enforce_fanout and not safety.get("ok"):
        result["errors"].extend(safety.get("errors") or [])
        result["ok"] = False
    return result


def render_joins(join_plan):
    """Render only joins returned by ``plan_joins`` into readonly SQL."""
    chunks = []
    for join in (join_plan or {}).get("joins") or []:
        chunks.append("JOIN \"%s\" ON %s" % (join.get("right_table"), join.get("condition")))
    return chunks


__all__ = ["referenced_tables", "plan_joins", "render_joins"]
