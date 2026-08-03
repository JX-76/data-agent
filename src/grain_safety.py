# -*- coding: utf-8 -*-
"""Conservative grain-aware aggregate rewrite planning.

The rewrite is intentionally limited to additive measures from one declared fact
source joined directly to many-to-one / one-to-one dimensions.  It aggregates
at the fact grain before the dimension joins.  Any ambiguous relationship,
external measure, unsupported filter, or missing grain contract falls back to
the existing compiler path; unsafe fanout remains blocked by ``join_planner``.
"""
from __future__ import unicode_literals

import re

from join_planner import referenced_tables
from metadata_catalog import resolve_field


_EQ_JOIN = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\s*=\s*"
                      r"([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\s*$")


def _field_table(field):
    tables = referenced_tables(field)
    return tables[0] if len(tables) == 1 else None


def _join_keys(join, base_table):
    """Return (base_key, dimension_table, dimension_key), or None.

    R12 only supports a direct, declared equality edge out of the fact table.
    Multi-hop paths and non-equality joins stay on the conservative direct path.
    """
    match = _EQ_JOIN.match(str((join or {}).get("condition") or ""))
    if not match:
        return None
    left_table, left_key, right_table, right_key = match.groups()
    if left_table == base_table:
        return left_key, right_table, right_key
    if right_table == base_table:
        return right_key, left_table, left_key
    return None


def _result(selected, reason, base_table, **extra):
    data = {
        "contract": "grain_aggregate_rewrite_v1",
        "selected": bool(selected),
        "strategy": "pre_aggregate" if selected else "direct",
        "reason": reason,
        "base_table": base_table,
    }
    data.update(extra)
    return data


def plan_preaggregate_rewrite(catalog, base_table, metric_id, metric_def,
                              dimension_fields, join_plan, filter_fields=None,
                              filter_specs=None):
    """Determine whether a fact-grain pre-aggregate rewrite is provably safe.

    It returns declarative instructions only; the SQL compiler owns rendering.
    A non-selected result is not an error.  It means no safe rewrite proof is
    available and preserves the existing direct compiler path.
    """
    metric_def = metric_def or {}
    tables = (catalog or {}).get("tables") or {}
    base_meta = tables.get(base_table) or {}
    joins = list((join_plan or {}).get("joins") or [])
    aggregation_type = metric_def.get("aggregation_type") or metric_def.get("measure_type")
    metric_field = resolve_field(metric_def, metric_id)
    metric_tables = referenced_tables(metric_field)

    if not joins:
        return _result(False, "no_join_to_rewrite", base_table)
    if base_meta.get("table_role") != "fact":
        return _result(False, "base_table_not_declared_fact", base_table)
    if not base_meta.get("grain"):
        return _result(False, "missing_fact_grain", base_table)
    if aggregation_type != "additive":
        return _result(False, "metric_not_declared_additive", base_table,
                       aggregation_type=aggregation_type)
    if metric_tables and metric_tables != [base_table]:
        return _result(False, "metric_not_sourced_from_base_fact", base_table,
                       metric_tables=metric_tables)
    if not metric_def.get("aggregation"):
        return _result(False, "missing_metric_aggregation_contract", base_table)

    filter_fields = filter_fields or []
    filter_specs = filter_specs or []
    dimension_filters = []
    for field in filter_fields:
        field_table = _field_table(field)
        if field_table and field_table != base_table:
            dimension_filters.append(field)

    base_group_fields = []
    outer_join_keys = []
    for field in dimension_fields or []:
        if _field_table(field) == base_table and field not in base_group_fields:
            base_group_fields.append(field)

    for join in joins:
        card = join.get("cardinality") or join.get("relationship")
        if card not in ("many_to_one", "one_to_one"):
            return _result(False, "join_cardinality_not_rewrite_safe", base_table,
                           join_id=join.get("id"), cardinality=card)
        keys = _join_keys(join, base_table)
        if not keys:
            return _result(False, "join_not_direct_fact_equality", base_table,
                           join_id=join.get("id"))
        base_key, dim_table, dim_key = keys
        base_field = "%s.%s" % (base_table, base_key)
        if base_field not in base_group_fields:
            base_group_fields.append(base_field)
        outer_join_keys.append({
            "join_id": join.get("id") or join.get("condition"),
            "dimension_table": dim_table,
            "base_key": base_key,
            "dimension_key": dim_key,
        })

    # Dimension filters can be applied inside the fact CTE only as a declared
    # semi-join.  This avoids grouping fact rows after a dimension join while
    # preserving the semantics of the direct query.
    joined_tables = set([item["dimension_table"] for item in outer_join_keys])
    joined_by_table = dict((item["dimension_table"], item) for item in outer_join_keys)
    semijoin_pushdowns = []
    for spec in filter_specs:
        if not isinstance(spec, dict):
            continue
        field = spec.get("field")
        field_table = _field_table(field)
        if not field_table or field_table == base_table:
            continue
        if field_table not in joined_tables:
            return _result(False, "filter_table_not_in_rewrite_join", base_table,
                           filter_field=field)
        op = str(spec.get("op") or "=").upper()
        if op not in ("=", "!=", "<>", ">", ">=", "<", "<=", "IN", "LIKE"):
            return _result(False, "filter_operator_not_pushdown_safe", base_table,
                           filter_field=field, filter_operator=op)
        if spec.get("value") is None:
            return _result(False, "filter_value_missing", base_table, filter_field=field)
        key = joined_by_table[field_table]
        semijoin_pushdowns.append({
            "contract": "dimension_filter_semijoin_v1",
            "join_id": key.get("join_id"),
            "dimension_table": field_table,
            "base_key": key.get("base_key"),
            "dimension_key": key.get("dimension_key"),
            "filter_field": field,
            "op": op,
            "value": spec.get("value"),
        })
    if dimension_filters and not semijoin_pushdowns:
        return _result(False, "filter_not_sourced_from_base_fact", base_table,
                       filter_field=dimension_filters[0])

    # A selected rewrite must include a joined dimension in output or a safe
    # semi-join filter. Otherwise the join is unnecessary and direct SQL is clearer.
    has_joined_dimension = any(_field_table(field) in joined_tables for field in (dimension_fields or []))
    if not has_joined_dimension and not semijoin_pushdowns:
        return _result(False, "no_joined_dimension_to_rewrite", base_table)

    return _result(True, "declared_additive_fact_preaggregate", base_table,
                    metric=metric_id, grain=base_meta.get("grain"),
                    metric_field=metric_field,
                    aggregation=str(metric_def.get("aggregation")).upper(),
                    join_count=len(joins),
                    base_group_fields=base_group_fields,
                    outer_join_keys=outer_join_keys,
                    semijoin_pushdowns=semijoin_pushdowns)


__all__ = ["plan_preaggregate_rewrite"]
