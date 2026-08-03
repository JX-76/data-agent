# -*- coding: utf-8 -*-
"""Shared runtime primitives for Data Agent execution paths.

This module centralizes the common runtime data model and basic execution
helpers that were duplicated across `mvp_agent.py` and `dag_agent.py`.
It is kept Python 2.7 compatible for the legacy code paths in this repo.
"""

from semantic_utils import DANGEROUS, SENSITIVE, load_semantic_layer, yaml

SEM = load_semantic_layer(table_index=True) if yaml else None


class Dataset(object):
    def __init__(self, dataid, model, sql, columns, parent=None, parents=None, op="switch"):
        self.dataid = dataid
        self.model = model
        self.sql = sql
        self.columns = columns
        self.parent = parent
        self.parents = parents
        self.op = op

    def __repr__(self):
        return "Dataset(%r, %r)" % (self.dataid, self.model)


class AgentRuntime(object):
    """Shared runtime for building and chaining dataid-backed SQL."""

    def __init__(self):
        self.datasets = {}
        self.counter = 0
        self.trace = []

    def _next_id(self):
        self.counter += 1
        return "d%d" % self.counter

    def _register(self, model, sql, columns, parent=None, op="tool", sample_rows=None, parents=None):
        did = self._next_id()
        self.datasets[did] = Dataset(did, model, sql, columns, parent=parent, op=op)
        if parents:
            self.datasets[did].parents = parents
        entry = {"dataid": did, "op": op, "parent": parent, "columns": columns}
        if sample_rows is not None:
            entry["sample_rows"] = sample_rows
        self.trace.append(entry)
        return did

    @staticmethod
    def _sanitize_value(value):
        if not isinstance(value, str):
            value = str(value)

        dangerous = ["--", ";", "/*", "*/", "DROP ", "DELETE FROM", "INSERT INTO", "UPDATE ", "ALTER ", "CREATE ", "UNION SELECT", "1=1", "1=2", "EXEC "]
        upper = value.upper()
        for pattern in dangerous:
            if pattern in upper:
                raise ValueError("Unsafe value: contains '%s'" % pattern.strip())

        if "'" in value:
            for op in [" OR ", " AND ", "="]:
                if op in value.upper():
                    raise ValueError("Unsafe value: contains quote+operator '%s'" % op)

        return value.replace("'", "''")

    @staticmethod
    def _sanitize_identifier(name):
        import re

        if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", name):
            raise ValueError("Unsafe identifier: %s" % name)
        return name

    def switch(self, model_id, db=None):
        model = SEM["models"][model_id]
        joins = []
        has_product = False
        for jid in model.get("joins", []):
            j = SEM["joins"][jid]
            joins.append("LEFT JOIN %s ON %s" % (j["right_table"], j["condition"]))
            if "product" in jid:
                has_product = True
        fields = []
        visible_cols = list(model.get("visible_dimensions", []))
        for did in visible_cols:
            d = SEM["dimensions"][did]
            fields.append("%s AS %s" % (d["field"], did))
        internal_cols = ["__sell_through", "__order_id", "__paid_at", "__order_status"]
        fields.extend([
            "fct_orders.sell_through AS __sell_through",
            "fct_orders.order_id AS __order_id",
            "fct_orders.paid_at AS __paid_at",
            "fct_orders.order_status AS __order_status",
        ])
        if has_product:
            internal_cols.append("__unit_price")
            fields.append("dim_product.unit_price AS __unit_price")
        sql = "SELECT " + ", ".join(fields) + "\nFROM %s" % model["base_table"]
        if joins:
            sql += "\n" + "\n".join(joins)
        all_columns = visible_cols + internal_cols
        sample_rows = {"model": model_id, "columns": all_columns, "visible_columns": visible_cols, "internal_columns": internal_cols}
        sample_rows["row_count"] = db.row_count("fct_orders") if db is not None else "demo: 无真实数据"
        return self._register(model_id, sql, all_columns, op="switch(%s)" % model_id, sample_rows=sample_rows)

    def preview(self, dataid, n=5, db=None):
        source = self.datasets[dataid]
        sql = "SELECT *\nFROM %s\nLIMIT %d" % (dataid, n)
        sample_rows = {"preview_of": dataid, "columns": source.columns, "model": source.model, "limit": n}
        if db is not None:
            chain_sql = self.compile_sql(dataid, limit=n)
            rows = db.execute_cte(chain_sql)
            sample_rows["rows"] = rows
            sample_rows["row_count"] = len(rows)
        else:
            sample_rows["rows"] = "demo: 无真实数据"
            sample_rows["note"] = "Agent 可在此节点查看前 %d 行样本" % n
        return self._register(source.model, sql, source.columns, parent=dataid, op="preview(%s,%d)" % (dataid, n), sample_rows=sample_rows)

    def clarify(self, metric_id):
        metric = SEM["metrics"][metric_id]
        return {
            "metric": metric_id,
            "question": "你要看的是 %s 的哪种口径？" % metric["name"],
            "options": [
                {"id": metric_id, "label": metric["name"], "description": metric["description"]},
                {"id": metric_id, "label": "%s（按日趋势）" % metric["name"], "description": "按日期拆分后查看趋势"},
            ],
        }

    def filter_time_and_defaults(self, dataid, metric_id, start, end):
        source = self.datasets[dataid]
        filters = [
            "__paid_at >= '%s'" % start.strftime("%Y-%m-%d %H:%M:%S"),
            "__paid_at < '%s'" % end.strftime("%Y-%m-%d %H:%M:%S"),
            "__order_status IN ('paid', 'completed')",
        ]
        sql = "SELECT *\nFROM %s\nWHERE " % dataid + " AND ".join(filters)
        return self._register(source.model, sql, source.columns, parent=dataid, op="filter(time,default_metric_filters)")

    def aggregate(self, dataid, metric_id, dimensions):
        source = self.datasets[dataid]
        metric_exprs = {
            "gmv": "SUM(__sell_through)",
            "order_count": "COUNT(DISTINCT __order_id)",
            "aov": "SUM(__sell_through) / NULLIF(COUNT(DISTINCT __order_id), 0)",
            "avg_price": "AVG(__unit_price)",
        }
        select_parts = list(dimensions)
        select_parts.append("%s AS %s" % (metric_exprs.get(metric_id, "COUNT(*)"), metric_id))
        sql = "SELECT " + ", ".join(select_parts) + "\nFROM %s" % dataid
        if dimensions:
            sql += "\nGROUP BY " + ", ".join(dimensions)
        return self._register(source.model, sql, list(dimensions) + [metric_id], parent=dataid, op="aggregate(%s)" % metric_id)

    def sort(self, dataid, by, order="DESC"):
        source = self.datasets[dataid]
        if by not in source.columns:
            raise ValueError("sort field %s not in %s" % (by, source.columns))
        sql = "SELECT *\nFROM %s\nORDER BY %s %s" % (dataid, by, order)
        return self._register(source.model, sql, source.columns, parent=dataid, op="sort(%s,%s)" % (by, order))

    def top(self, dataid, by, n=5, order="DESC"):
        source = self.datasets[dataid]
        if by not in source.columns:
            raise ValueError("top field %s not in %s" % (by, source.columns))
        sql = "SELECT *\nFROM %s\nORDER BY %s %s\nLIMIT %d" % (dataid, by, order, n)
        return self._register(source.model, sql, source.columns, parent=dataid, op="top(%s,%d,%s)" % (by, n, order))

    def filter_value(self, dataid, dimension, value):
        source = self.datasets[dataid]
        if dimension not in source.columns:
            raise ValueError("filter_value dimension %s not in %s" % (dimension, source.columns))
        if value.startswith("!"):
            sql = "SELECT *\nFROM %s\nWHERE %s != '%s'" % (dataid, dimension, value[1:])
            op_str = "filter_value(%s!=%s)" % (dimension, value[1:])
        else:
            sql = "SELECT *\nFROM %s\nWHERE %s = '%s'" % (dataid, dimension, value)
            op_str = "filter_value(%s=%s)" % (dimension, value)
        return self._register(source.model, sql, source.columns, parent=dataid, op=op_str)

    def merge(self, dataid_a, dataid_b, on):
        source_a = self.datasets[dataid_a]
        source_b = self.datasets[dataid_b]
        if on not in source_a.columns or on not in source_b.columns:
            raise ValueError("merge key %s not in both datasets" % on)
        combined_cols = list(source_a.columns)
        for c in source_b.columns:
            if c != on and c not in combined_cols:
                combined_cols.append(c)
        select_b = []
        for c in source_b.columns:
            if c != on:
                select_b.append("b.%s AS %s" % (c, c))
        sql = "SELECT a.*, %s\nFROM %s a\nJOIN %s b ON a.%s = b.%s" % (", ".join(select_b), dataid_a, dataid_b, on, on)
        return self._register(source_a.model, sql, combined_cols, parent="%s+%s" % (dataid_a, dataid_b), op="merge(%s,%s,on=%s)" % (dataid_a, dataid_b, on), parents=[dataid_a, dataid_b])

    def compare_periods(self, dataid, metric_id, period1_start, period1_end, period2_start, period2_end, dimensions):
        source = self.datasets[dataid]
        metric_exprs = {
            "gmv": "SUM(__sell_through)",
            "order_count": "COUNT(DISTINCT __order_id)",
            "aov": "SUM(__sell_through) / NULLIF(COUNT(DISTINCT __order_id), 0)",
            "avg_price": "AVG(__unit_price)",
        }
        mexpr = metric_exprs.get(metric_id, "COUNT(*)")
        p1_filters = [
            "__paid_at >= '%s'" % period1_start.strftime("%Y-%m-%d %H:%M:%S"),
            "__paid_at < '%s'" % period1_end.strftime("%Y-%m-%d %H:%M:%S"),
            "__order_status IN ('paid', 'completed')",
        ]
        p1_sql = "SELECT *\nFROM %s\nWHERE " % dataid + " AND ".join(p1_filters)
        p1_id = self._next_id()
        self.datasets[p1_id] = Dataset(p1_id, source.model, p1_sql, source.columns, parent=dataid, op="compare_periods.p1_filter")
        dim_key = dimensions[0] if dimensions else "channel"
        p1_agg = "SELECT %s, %s AS %s_p1\nFROM %s\nGROUP BY %s" % (dim_key, mexpr, metric_id, p1_id, dim_key)
        p1_agg_id = self._next_id()
        self.datasets[p1_agg_id] = Dataset(p1_agg_id, source.model, p1_agg, [dim_key, "%s_p1" % metric_id], parent=p1_id, op="compare_periods.p1_agg")

        p2_filters = [
            "__paid_at >= '%s'" % period2_start.strftime("%Y-%m-%d %H:%M:%S"),
            "__paid_at < '%s'" % period2_end.strftime("%Y-%m-%d %H:%M:%S"),
            "__order_status IN ('paid', 'completed')",
        ]
        p2_sql = "SELECT *\nFROM %s\nWHERE " % dataid + " AND ".join(p2_filters)
        p2_id = self._next_id()
        self.datasets[p2_id] = Dataset(p2_id, source.model, p2_sql, source.columns, parent=dataid, op="compare_periods.p2_filter")
        p2_agg = "SELECT %s, %s AS %s_p2\nFROM %s\nGROUP BY %s" % (dim_key, mexpr, metric_id, p2_id, dim_key)
        p2_agg_id = self._next_id()
        self.datasets[p2_agg_id] = Dataset(p2_agg_id, source.model, p2_agg, [dim_key, "%s_p2" % metric_id], parent=p2_id, op="compare_periods.p2_agg")
        combined = [dim_key, "%s_p1" % metric_id, "%s_p2" % metric_id]
        merge_sql = "SELECT a.*, b.%s_p2\nFROM %s a\nLEFT JOIN %s b ON a.%s = b.%s" % (metric_id, p1_agg_id, p2_agg_id, dim_key, dim_key)
        return self._register(source.model, merge_sql, combined, parent="%s+%s" % (p1_agg_id, p2_agg_id), op="compare_periods(%s)" % metric_id, parents=[p1_agg_id, p2_agg_id])

    def compile_sql(self, final_dataid, limit=100):
        ordered = []
        visited = set()
        stack = [(final_dataid, False)]
        while stack:
            did, done = stack.pop()
            if done:
                ordered.append(did)
                continue
            if did in visited or did not in self.datasets:
                continue
            visited.add(did)
            ds = self.datasets[did]
            stack.append((did, True))
            if ds.parents:
                for p in reversed(ds.parents):
                    stack.append((p, False))
            elif ds.parent and ds.parent != did and ds.parent in self.datasets:
                stack.append((ds.parent, False))
        ctes = ["%s AS (\n%s\n)" % (did, self.datasets[did].sql) for did in ordered if did in self.datasets]
        return "WITH\n" + ",\n".join(ctes) + "\nSELECT * FROM %s\nLIMIT %s" % (final_dataid, limit)


def validate_sql(sql):
    low = sql.lower()
    if any(x in low for x in ["delete", "update", "insert", "drop", "alter", "truncate"]):
        return False, "DDL/DML is forbidden"
    if any(x in low for x in SENSITIVE):
        return False, "Sensitive field is forbidden"
    if not low.strip().startswith("with"):
        return False, "Runtime SQL must be generated as CTE chain"
    if "select * from d" not in low:
        return False, "Final SQL must read from a dataid"
    return True, "ok"


__all__ = ["SEM", "Dataset", "AgentRuntime", "validate_sql"]
