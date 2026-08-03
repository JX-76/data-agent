# -*- coding: utf-8 -*-
"""Metric dictionary driven SQL compiler.

R8 intentionally supports the stable single-model ecommerce path first. Complex
joins and advanced task families remain on the legacy runtime strategy until
registered explicitly in metadata.
"""
from __future__ import unicode_literals

import hashlib
import json

from metadata_catalog import resolve_field, resolve_model_table
from join_planner import plan_joins, referenced_tables, render_joins
from grain_safety import plan_preaggregate_rewrite


class MetricSqlCompileResult(object):
    def __init__(self, sql=None, ok=True, errors=None, warnings=None, metadata=None):
        self.sql = sql
        self.ok = ok
        self.errors = errors or []
        self.warnings = warnings or []
        self.metadata = metadata or {}

    def to_dict(self):
        data = {"contract": "compiled_sql_v1", "ok": self.ok, "sql": self.sql,
                "errors": list(self.errors), "warnings": list(self.warnings)}
        data.update(self.metadata or {})
        return data


def _fingerprint(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    if not isinstance(raw, bytes):
        raw = raw.encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def _quote(name):
    text = str(name or "")
    if not text.replace("_", "").isalnum():
        raise ValueError("invalid identifier: %s" % text)
    return '"%s"' % text.replace('"', '""')


def _field_expr(field):
    """Permit registered qualified fields/functions, never arbitrary plan fields."""
    text = str(field or "")
    if text.upper().startswith("DATE(") and text.endswith(")"):
        return "DATE(%s)" % _field_expr(text[5:-1])
    parts = text.split(".")
    if len(parts) > 1:
        return ".".join([_quote(part) for part in parts])
    return _quote(text)


def _alias_for_field(field):
    text = str(field or "")
    if text.upper().startswith("DATE(") and text.endswith(")"):
        text = text[5:-1]
    return text.split(".")[-1]


def _qualified_alias(field):
    return "_fact_agg.%s" % _quote(_alias_for_field(field))


def _metric_expr(metric_id, metric_def):
    expr = (metric_def or {}).get("expression")
    if expr:
        # Metric expressions originate only from the semantic registry.
        return expr
    agg = (metric_def or {}).get("aggregation") or (metric_def or {}).get("agg")
    field = resolve_field(metric_def, metric_id)
    if agg:
        return "%s(%s)" % (str(agg).upper(), _field_expr(field))
    if metric_id in ("order_count", "user_count", "sku_count"):
        return "COUNT(%s)" % (_quote(field) if field != metric_id else "*")
    if metric_id in ("aov",):
        return "CASE WHEN COUNT(*) = 0 THEN NULL ELSE SUM(%s) * 1.0 / COUNT(*) END" % _quote("gmv")
    return "SUM(%s)" % _quote(field)


def _aggregate_expr(aggregation, field):
    agg = str(aggregation or "SUM").upper()
    if agg in ("COUNT_DISTINCT", "COUNT DISTINCT"):
        return "COUNT(DISTINCT %s)" % _field_expr(field)
    return "%s(%s)" % (agg, _field_expr(field))


def _literal(value):
    if isinstance(value, (list, tuple, set)):
        return "(" + ", ".join([_literal(item) for item in value]) + ")"
    if value is None:
        return "NULL"
    return "'%s'" % str(value).replace("'", "''")


def _render_filter_predicate(field, op, value):
    op = str(op or "=").upper()
    if op == "IN":
        values = value if isinstance(value, (list, tuple, set)) else [value]
        return "%s IN %s" % (_field_expr(field), _literal(list(values)))
    return "%s %s %s" % (_field_expr(field), op, _literal(value))


def _render_semijoin_predicate(item, base_table):
    dim_table = item.get("dimension_table")
    dim_key = item.get("dimension_key")
    filter_field = item.get("filter_field")
    op = item.get("op") or "="
    value = item.get("value")
    return "%s IN (SELECT %s FROM %s WHERE %s)" % (
        _field_expr("%s.%s" % (base_table, item.get("base_key"))),
        _field_expr("%s.%s" % (dim_table, dim_key)),
        _field_expr(dim_table),
        _render_filter_predicate(filter_field, op, value),
    )


def _render_preaggregate_sql(rewrite, metric_id, dimensions, dimension_fields,
                             where_parts, limit):
    base_table = rewrite.get("base_table")
    group_fields = list(rewrite.get("base_group_fields") or [])
    metric_field = rewrite.get("metric_field")
    aggregation = rewrite.get("aggregation") or "SUM"
    outer_join_keys = list(rewrite.get("outer_join_keys") or [])

    cte_select = []
    cte_group = []
    for field in group_fields:
        cte_select.append("%s AS %s" % (_field_expr(field), _quote(_alias_for_field(field))))
        cte_group.append(_field_expr(field))
    cte_select.append("%s AS %s" % (_aggregate_expr(aggregation, metric_field), _quote(metric_id)))

    cte = "WITH _fact_agg AS (\n  SELECT %s\n  FROM %s" % (", ".join(cte_select), _field_expr(base_table))
    semijoin_parts = []
    for item in rewrite.get("semijoin_pushdowns") or []:
        semijoin_parts.append(_render_semijoin_predicate(item, base_table))
    all_where_parts = list(where_parts or []) + semijoin_parts
    if all_where_parts:
        cte += "\n  WHERE " + " AND ".join(all_where_parts)
    if cte_group:
        cte += "\n  GROUP BY " + ", ".join(cte_group)
    cte += "\n)"

    outer_select = []
    outer_group = []
    join_tables = {}
    for item in outer_join_keys:
        join_tables[item.get("dimension_table")] = item

    for dim, field in zip(dimensions, dimension_fields):
        tables = referenced_tables(field)
        if tables and tables[0] == base_table:
            expr = _qualified_alias(field)
        else:
            expr = _field_expr(field)
        outer_select.append("%s AS %s" % (expr, _quote(dim)))
        outer_group.append(expr)
    outer_metric = "SUM(_fact_agg.%s)" % _quote(metric_id)
    outer_select.append("%s AS %s" % (outer_metric, _quote(metric_id)))

    sql = cte + "\nSELECT %s\nFROM _fact_agg" % ", ".join(outer_select)
    for item in outer_join_keys:
        sql += "\nJOIN %s ON _fact_agg.%s = %s.%s" % (
            _field_expr(item.get("dimension_table")),
            _quote(item.get("base_key")),
            _field_expr(item.get("dimension_table")),
            _quote(item.get("dimension_key")),
        )
    if outer_group:
        sql += "\nGROUP BY " + ", ".join(outer_group)
        sql += "\nORDER BY %s DESC" % _quote(metric_id)
    sql += "\nLIMIT %d" % int(limit or 1000)
    return sql


def compile_metric_sql(plan, catalog, limit=1000):
    plan = plan.to_dict() if hasattr(plan, "to_dict") else dict(plan or {})
    catalog = catalog or {}
    metric_id = plan.get("metric") or "gmv"
    model_id = plan.get("model") or "order_detail"
    dimensions = list(plan.get("dimensions") or [])
    filters = list(plan.get("filters") or [])
    time_range = plan.get("time_range")
    metrics = catalog.get("metrics") or {}
    dims_meta = catalog.get("dimensions") or {}
    tables = catalog.get("tables") or {}
    errors = []
    warnings = []
    metric_def = metrics.get(metric_id)
    if not metric_def:
        errors.append("unknown metric: %s" % metric_id)
    table_name = resolve_model_table(catalog, model_id)
    table = tables.get(table_name) or tables.get(model_id)
    if tables and not table:
        errors.append("unknown table for model: %s" % model_id)
    allowed = set((metric_def or {}).get("allowed_dimensions") or [])
    used_fields = []
    select_parts = []
    group_parts = []
    for dim in dimensions:
        dim_def = dims_meta.get(dim)
        if not dim_def:
            errors.append("unknown dimension: %s" % dim)
            continue
        if allowed and dim not in allowed:
            errors.append("dimension not allowed for metric: %s" % dim)
        field = resolve_field(dim_def, dim)
        used_fields.append(field)
        select_parts.append("%s AS %s" % (_field_expr(field), _quote(dim)))
        group_parts.append(_field_expr(field))
    if errors:
        return MetricSqlCompileResult(ok=False, errors=errors, warnings=warnings,
                                      metadata={"metric": metric_id, "model": model_id, "dimensions": dimensions})
    metric_expression = _metric_expr(metric_id, metric_def)
    used_fields.append(resolve_field(metric_def, metric_id))
    select_parts.append("%s AS %s" % (metric_expression, _quote(metric_id)))
    where_parts = []
    rewrite_where_parts = []
    time_field = (metric_def or {}).get("time_field")
    if not time_field and table:
        time_field = table.get("time_column")
    if not time_field:
        time_field = plan.get("time_dimension") or "date"
    if time_range and isinstance(time_range, (list, tuple)) and len(time_range) == 2:
        time_clause = "%s >= '%s' AND %s <= '%s'" % (_field_expr(time_field), time_range[0], _field_expr(time_field), time_range[1])
        where_parts.append(time_clause)
        rewrite_where_parts.append(time_clause)
        used_fields.append(time_field)
    for item in filters:
        if isinstance(item, dict) and item.get("field") and item.get("value") is not None:
            field = item.get("field")
            op = item.get("op") or "="
            if op not in ("=", "!=", "<>", ">", ">=", "<", "<=", "IN", "LIKE"):
                errors.append("unsupported filter operator: %s" % op)
                continue
            predicate = _render_filter_predicate(field, op, item.get("value"))
            where_parts.append(predicate)
            tables_for_filter = referenced_tables(field)
            if not tables_for_filter or tables_for_filter == [table_name]:
                rewrite_where_parts.append(predicate)
            used_fields.append(field)
    if errors:
        return MetricSqlCompileResult(ok=False, errors=errors, warnings=warnings,
                                      metadata={"metric": metric_id, "model": model_id, "dimensions": dimensions})
    required_tables = referenced_tables(metric_expression, *used_fields)
    join_plan = plan_joins(catalog, table_name, required_tables, metric_def=metric_def,
                           enforce_fanout=True)
    warnings.extend((join_plan.get("fanout_safety") or {}).get("warnings") or [])
    if not join_plan.get("ok"):
        errors.extend(join_plan.get("errors") or [])
        return MetricSqlCompileResult(ok=False, errors=errors, warnings=warnings,
                                      metadata={"metric": metric_id, "model": model_id,
                                                "dimensions": dimensions,
                                                "fanout_safety": join_plan.get("fanout_safety")})
    dimension_fields = [resolve_field(dims_meta.get(dim), dim) for dim in dimensions]
    filter_fields = [time_field] if time_range else []
    filter_fields.extend([item.get("field") for item in filters if isinstance(item, dict) and item.get("field")])
    grain_rewrite = plan_preaggregate_rewrite(
        catalog, table_name, metric_id, metric_def, dimension_fields, join_plan,
        filter_fields=filter_fields, filter_specs=filters)

    if grain_rewrite.get("selected"):
        sql = _render_preaggregate_sql(grain_rewrite, metric_id, dimensions,
                                       dimension_fields, rewrite_where_parts, limit)
    else:
        join_sql = render_joins(join_plan)
        sql = "SELECT %s\nFROM %s" % (", ".join(select_parts), _field_expr(table_name))
        if join_sql:
            sql += "\n" + "\n".join(join_sql)
        if where_parts:
            sql += "\nWHERE " + " AND ".join(where_parts)
        if group_parts:
            sql += "\nGROUP BY " + ", ".join(group_parts)
            sql += "\nORDER BY %s DESC" % _quote(metric_id)
        sql += "\nLIMIT %d" % int(limit or 1000)
    metadata = {"contract": "compiled_sql_v1", "metric": metric_id, "model": model_id,
                "table": table_name, "dimensions": dimensions, "used_fields": sorted(set(used_fields)),
                "joins": [j.get("id") or j.get("condition") for j in (join_plan.get("joins") or [])],
                "fanout_safety": join_plan.get("fanout_safety"),
                "grain_rewrite": grain_rewrite,
                "catalog_fingerprint": catalog.get("fingerprint"), "fingerprint": _fingerprint(sql)}
    return MetricSqlCompileResult(sql=sql, ok=True, warnings=warnings, metadata=metadata)


__all__ = ["MetricSqlCompileResult", "compile_metric_sql"]
