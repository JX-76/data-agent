# -*- coding: utf-8 -*-
"""DAG-specific runtime extensions.

This module contains the experimental DAG runtime that used to live inside
`dag_agent.py`. `dag_agent.py` keeps a compatibility alias so existing imports
continue to work.
"""

from runtime_core import AgentRuntime as BaseAgentRuntime, Dataset
from semantic_utils import load_semantic_layer, yaml

SEM = load_semantic_layer() if yaml else None


class DAGAgentRuntime(BaseAgentRuntime):
    """In-graph runtime that holds datasets and CTE chain."""

    # Allowed characters for identifiers
    _IDENT_RE = __import__('re').compile(r'^[a-zA-Z_][a-zA-Z0-9_]*$')

    def __init__(self):
        super(DAGAgentRuntime, self).__init__()

    @staticmethod
    def _sanitize_value(value):
        """Sanitize user-supplied value for safe SQL interpolation.

        Escapes single quotes (SQL standard: '' → escaped) and
        rejects dangerous SQL injection patterns.
        """
        if not isinstance(value, str):
            value = str(value)

        # Check injection patterns BEFORE escaping (catch raw attack strings)
        # These patterns indicate SQL injection intent, not legitimate data
        dangerous = ["--", ";", "/*", "*/", "DROP ", "DELETE FROM",
                    "INSERT INTO", "UPDATE ", "ALTER ", "CREATE ",
                    "UNION SELECT", "1=1", "1=2", "EXEC "]
        upper = value.upper()
        for pattern in dangerous:
            if pattern in upper:
                raise ValueError("Unsafe value: contains '%s'" % pattern.strip())

        # Block unescaped quotes in combination with SQL operators
        # This catches: x' OR '1'='1, x' AND 1=1, etc.
        if "'" in value:
            # If value contains a single quote AND SQL operators, it's likely injection
            op_patterns = [" OR ", " AND ", "="]
            for op in op_patterns:
                if op in value.upper():
                    raise ValueError("Unsafe value: contains quote+operator '%s'" % op)

        # Escape single quotes
        value = value.replace("'", "''")
        return value

    @staticmethod
    def _sanitize_identifier(name):
        """Validate an identifier is safe (alphanumeric + underscore only)."""
        if not DAGAgentRuntime._IDENT_RE.match(name):
            raise ValueError("Unsafe identifier: %s" % name)
        return name

    def _next_id(self):
        self.counter += 1
        return "d%s" % self.counter

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

    def switch(self, model_id, db=None):
        model = SEM["models"][model_id]
        joins = []
        has_product = False
        for jid in model.get("joins", []):
            j = SEM["joins"][jid]
            joins.append("LEFT JOIN %s ON %s" % (j['right_table'], j['condition']))
            if "product" in jid:
                has_product = True
        fields = []
        visible_cols = list(model.get("visible_dimensions", []))
        for did in visible_cols:
            d = SEM["dimensions"][did]
            fields.append("%s AS %s" % (d['field'], did))
        internal_cols = ["__sell_through", "__order_id", "__paid_at", "__order_status"]
        fields.extend(["fct_orders.sell_through AS __sell_through",
                       "fct_orders.order_id AS __order_id",
                       "fct_orders.paid_at AS __paid_at",
                       "fct_orders.order_status AS __order_status"])
        if has_product:
            internal_cols.append("__unit_price")
            fields.append("dim_product.unit_price AS __unit_price")
        sql = "SELECT " + ", ".join(fields) + "\nFROM %s" % model["base_table"]
        if joins:
            sql += "\n" + "\n".join(joins)
        all_columns = visible_cols + internal_cols
        sample_rows = {"model": model_id, "columns": all_columns, "visible_columns": visible_cols, "internal_columns": internal_cols}
        if db is not None:
            sample_rows["row_count"] = db.row_count("fct_orders")
        else:
            sample_rows["row_count"] = 0
        return self._register(model_id, sql, all_columns, op="switch(%s)" % model_id, sample_rows=sample_rows)

    def preview(self, dataid, n=5, db=None):
        source = self.datasets[dataid]
        sql = "SELECT *\nFROM %s\nLIMIT %s" % (dataid, n)
        sample_rows = {"preview_of": dataid, "columns": source.columns, "model": source.model, "limit": n}
        if db is not None:
            chain_sql = self.compile_sql(dataid, limit=n)
            rows = db.execute_cte(chain_sql)
            sample_rows["rows"] = rows
            sample_rows["row_count"] = len(rows)
        else:
            sample_rows["rows"] = "demo: 无真实数据"
            sample_rows["note"] = "Agent 可在此节点查看前 %s 行样本" % n
        return self._register(source.model, sql, source.columns, parent=dataid, op="preview(%s,%s)" % (dataid, n), sample_rows=sample_rows)

    def filter_time_and_defaults(self, dataid, metric_id, start, end):
        metric = SEM["metrics"][metric_id]
        source = self.datasets[dataid]
        filters = [
            "__paid_at >= '%s'" % start.strftime("%Y-%m-%d %H:%M:%S"),
            "__paid_at < '%s'" % end.strftime("%Y-%m-%d %H:%M:%S"),
            "__order_status IN ('paid', 'completed')",
        ]
        # The original metric filters live in semantic config; runtime applies equivalent filters on the single-table view.
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

    def sort_data(self, dataid, by, order="DESC"):
        source = self.datasets[dataid]
        if by not in source.columns:
            raise ValueError("sort field %s not in %s" % (by, source.columns))
        self._sanitize_identifier(by)
        direction = "DESC" if order.upper() in ("DESC", "DESCENDING") else "ASC"
        sql = "SELECT *\nFROM %s\nORDER BY %s %s" % (dataid, by, direction)
        return self._register(source.model, sql, source.columns, parent=dataid, op="sort(%s,%s)" % (by, direction))

    def sort(self, dataid, by, order="DESC"):
        """Compatibility alias used by the facade layer."""
        return self.sort_data(dataid, by=by, order=order)

    def top(self, dataid, by, n=5, order="DESC"):
        source = self.datasets[dataid]
        if by not in source.columns:
            raise ValueError("top field %s not in %s" % (by, source.columns))
        self._sanitize_identifier(by)
        direction = "DESC" if order.upper() in ("DESC", "DESCENDING") else "ASC"
        n_int = int(n)
        if n_int < 1:
            raise ValueError("top n must be >= 1, got %s" % n_int)
        sql = "SELECT *\nFROM %s\nORDER BY %s %s\nLIMIT %s" % (dataid, by, direction, n_int)
        return self._register(source.model, sql, source.columns, parent=dataid, op="top(%s,%s,%s)" % (by, n_int, direction))

    # ── P1-1: 窗口函数 ──
    def rank(self, dataid, metric, partition_by=None, top_n=10):
        """窗口排名: RANK() OVER (PARTITION BY ... ORDER BY ... DESC)"""
        source = self.datasets[dataid]
        self._sanitize_identifier(metric)
        partition_clause = ""
        if partition_by:
            valid_parts = [p for p in partition_by if p in source.columns]
            if valid_parts:
                partition_clause = "PARTITION BY " + ", ".join(valid_parts)
        over_clause = "OVER (%s ORDER BY %s DESC)" % (partition_clause, metric) if partition_clause else "OVER (ORDER BY %s DESC)" % metric
        # 保留原始列 + 排名列
        cols = list(source.columns) + ["rank"]
        sql = "SELECT *, RANK() %s AS rank\nFROM %s\nLIMIT %s" % (over_clause, dataid, top_n)
        return self._register(source.model, sql, cols, parent=dataid, op="rank(%s,top_n=%s)" % (metric, top_n))

    def row_number(self, dataid, metric, partition_by=None, top_n=10):
        """窗口行号: ROW_NUMBER() OVER (同rank)"""
        source = self.datasets[dataid]
        self._sanitize_identifier(metric)
        partition_clause = ""
        if partition_by:
            valid_parts = [p for p in partition_by if p in source.columns]
            if valid_parts:
                partition_clause = "PARTITION BY " + ", ".join(valid_parts)
        over_clause = "OVER (%s ORDER BY %s DESC)" % (partition_clause, metric) if partition_clause else "OVER (ORDER BY %s DESC)" % metric
        cols = list(source.columns) + ["row_num"]
        sql = "SELECT *, ROW_NUMBER() %s AS row_num\nFROM %s\nLIMIT %s" % (over_clause, dataid, top_n)
        return self._register(source.model, sql, cols, parent=dataid, op="row_number(%s,top_n=%s)" % (metric, top_n))

    def cumulative(self, dataid, metric, partition_by=None):
        """累计值: SUM(metric) OVER (PARTITION BY ... ORDER BY ...)"""
        source = self.datasets[dataid]
        self._sanitize_identifier(metric)
        partition_clause = ""
        if partition_by:
            valid_parts = [p for p in partition_by if p in source.columns]
            if valid_parts:
                partition_clause = "PARTITION BY " + ", ".join(valid_parts)
        # 需要有日期列做排序
        date_col = None
        for c in source.columns:
            if "date" in c.lower() or c in ("date", "day", "paid_at"):
                date_col = c
                break
        order_clause = "ORDER BY %s" % date_col if date_col else "ORDER BY (SELECT 1)"
        over_clause = "OVER (%s %s)" % (partition_clause, order_clause) if partition_clause else "OVER (%s)" % order_clause
        cols = list(source.columns) + ["cumulative_%s" % metric]
        sql = "SELECT *, SUM(%s) %s AS cumulative_%s\nFROM %s" % (metric, over_clause, metric, dataid)
        return self._register(source.model, sql, cols, parent=dataid, op="cumulative(%s)" % metric)

    def lag_metric(self, dataid, metric, partition_by=None, offset=1):
        """LAG窗口函数: 获取前N行值，用于环比计算"""
        source = self.datasets[dataid]
        self._sanitize_identifier(metric)
        partition_clause = ""
        if partition_by:
            valid_parts = [p for p in partition_by if p in source.columns]
            if valid_parts:
                partition_clause = "PARTITION BY " + ", ".join(valid_parts)
        date_col = None
        for c in source.columns:
            if "date" in c.lower() or c in ("date", "day", "paid_at"):
                date_col = c
                break
        order_clause = "ORDER BY %s" % date_col if date_col else "ORDER BY (SELECT 1)"
        over_clause = "OVER (%s %s)" % (partition_clause, order_clause) if partition_clause else "OVER (%s)" % order_clause
        cols = list(source.columns) + ["prev_%s" % metric, "%s_change_pct" % metric]
        sql = (
            "SELECT *, "
            "LAG(%s, %s) %s AS prev_%s, " % (metric, offset, over_clause, metric)
            + "CASE WHEN LAG(%s, %s) %s > 0 " % (metric, offset, over_clause)
            + "THEN ROUND((%s - LAG(%s, %s) %s) * 100.0 / LAG(%s, %s) %s, 1) " % (metric, metric, offset, over_clause, metric, offset, over_clause)
            + "ELSE NULL END AS %s_change_pct\n" % metric
            + "FROM %s" % dataid
        )
        return self._register(source.model, sql, cols, parent=dataid, op="lag(%s,%s)" % (metric, offset))

    def moving_average(self, dataid, metric, window_size=3, partition_by=None):
        """移动平均: AVG(metric) OVER (ROWS N PRECEDING)"""
        source = self.datasets[dataid]
        self._sanitize_identifier(metric)
        partition_clause = ""
        if partition_by:
            valid_parts = [p for p in partition_by if p in source.columns]
            if valid_parts:
                partition_clause = "PARTITION BY " + ", ".join(valid_parts)
        date_col = None
        for c in source.columns:
            if "date" in c.lower() or c in ("date", "day", "paid_at"):
                date_col = c
                break
        order_clause = "ORDER BY %s" % date_col if date_col else "ORDER BY (SELECT 1)"
        rows_clause = "ROWS BETWEEN %s PRECEDING AND CURRENT ROW" % (window_size - 1)
        over_clause = "OVER (%s %s %s)" % (partition_clause, order_clause, rows_clause) if partition_clause else "OVER (%s %s)" % (order_clause, rows_clause)
        cols = list(source.columns) + ["ma_%s_%s" % (window_size, metric)]
        sql = "SELECT *, AVG(%s) %s AS ma_%s_%s\nFROM %s" % (metric, over_clause, window_size, metric, dataid)
        return self._register(source.model, sql, cols, parent=dataid, op="moving_avg(%s,%s)" % (metric, window_size))

    # ── P1-2: 子查询方法 ──
    def subquery_exists(self, dataid, target_entity, conditions):
        """生成 EXISTS 子查询: 满足多条件的实体。
        
        例如: "购买过A又购买过B的用户" →
        SELECT user_id FROM dataid WHERE EXISTS(
            SELECT 1 FROM dataid AS t2 WHERE t2.user_id = main.user_id AND condition1
        ) AND EXISTS(
            SELECT 1 FROM dataid AS t2 WHERE t2.user_id = main.user_id AND condition2
        )
        """
        source = self.datasets[dataid]
        # 确定实体列
        entity_col = self._guess_entity_column(target_entity, source.columns)
        
        # 构建条件SQL
        where_parts = []
        for cond in conditions:
            safe_cond = self._sanitize_value(cond)
            # 尝试匹配列名
            matched = False
            for col in source.columns:
                if col in ("channel", "region", "category", "product_name", "store_name"):
                    where_parts.append("t2.%s = '%s'" % (col, safe_cond))
                    matched = True
                    break
            if not matched:
                # fallback: channel
                where_parts.append("t2.channel = '%s'" % safe_cond)
        
        # 构建 EXISTS 子查询
        exists_clauses = []
        for wp in where_parts:
            exists_clauses.append(
                "EXISTS (SELECT 1 FROM %s AS t2 " % dataid +
                "WHERE t2.%s = main.%s AND %s)" % (entity_col, entity_col, wp)
            )
        
        where_total = " AND ".join(exists_clauses)
        sql = "SELECT DISTINCT %s\nFROM %s AS main\nWHERE %s" % (entity_col, dataid, where_total)
        cols = [entity_col] + list(source.columns)[:5]
        return self._register(source.model, sql, cols, parent=dataid, op="subquery_exists(%s)" % target_entity)

    def subquery_in(self, dataid, target_entity, subquery_desc):
        """生成 IN 子查询: 在某结果集中筛选。
        
        例如: "在GMV前10渠道中的订单" →
        SELECT * FROM dataid WHERE channel IN (
            SELECT channel FROM dataid GROUP BY channel ORDER BY SUM(gmv) DESC LIMIT 10
        )
        """
        source = self.datasets[dataid]
        entity_col = self._guess_entity_column(target_entity, source.columns)
        
        # 构建内部子查询（默认：按GMV TOP 10）
        inner_sql = (
            "SELECT %s FROM %s " % (entity_col, dataid) +
            "GROUP BY %s ORDER BY SUM(__sell_through) DESC LIMIT 10" % entity_col
        )
        sql = "SELECT *\nFROM %s\nWHERE %s IN (\n  %s\n)" % (dataid, entity_col, inner_sql)
        return self._register(source.model, sql, source.columns, parent=dataid, op="subquery_in(%s)" % target_entity)

    def subquery_cte(self, dataid, cte_name, cte_sql, main_select):
        """生成 CTE (WITH) 子查询。"""
        source = self.datasets[dataid]
        sql = "WITH %s AS (\n  %s\n)\n%s\nFROM %s\nJOIN %s USING (channel)" % (cte_name, cte_sql, main_select, dataid, cte_name)
        return self._register(source.model, sql, source.columns, parent=dataid, op="cte(%s)" % cte_name)

    @staticmethod
    def _guess_entity_column(target_entity, columns):
        """猜测实体对应的列名。"""
        if target_entity == "user":
            for c in columns:
                if "user" in c.lower() or c == "user_id":
                    return c
            return "channel"  # fallback
        if target_entity == "order":
            for c in columns:
                if "order" in c.lower() or c == "__order_id":
                    return c
            return "__order_id"
        if target_entity == "product":
            for c in columns:
                if "product" in c.lower() or "category" in c.lower():
                    return c
            return "category"
        return "channel"

    def filter_value(self, dataid, dimension, value):
        source = self.datasets[dataid]
        if dimension not in source.columns:
            raise ValueError("filter_value dimension %s not in %s" % (dimension, source.columns))
        # Sanitize user-supplied value against SQL injection
        negate = value.startswith("!")
        clean_value = self._sanitize_value(value[1:] if negate else value)
        op_symbol = "!=" if negate else "="
        sql = "SELECT *\nFROM %s\nWHERE %s %s '%s'" % (dataid, dimension, op_symbol, clean_value)
        return self._register(source.model, sql, source.columns, parent=dataid, op="filter_value(%s%s%s)" % (dimension, op_symbol, clean_value))

    def merge(self, dataid_a, dataid_b, on):
        source_a = self.datasets[dataid_a]
        source_b = self.datasets[dataid_b]
        if on not in source_a.columns or on not in source_b.columns:
            raise ValueError("merge key %s not in both datasets" % on)

        # Detect colliding columns (excluding the join key)
        cols_a = set(source_a.columns)
        cols_b = set(source_b.columns)
        shared = (cols_a & cols_b) - {on}

        combined_cols = list(source_a.columns)
        select_b_parts = []
        for c in source_b.columns:
            if c == on:
                continue
            if c in shared:
                # Suffix to avoid collision
                aliased = "%s_%s" % (c, dataid_b)
                select_b_parts.append("b.%s AS %s" % (c, aliased))
                combined_cols.append(aliased)
            else:
                select_b_parts.append("b.%s AS %s" % (c, c))
                combined_cols.append(c)

        sql = "SELECT a.*, %s\nFROM %s a\nJOIN %s b ON a.%s = b.%s" % (', '.join(select_b_parts), dataid_a, dataid_b, on, on)
        return self._register(source_a.model, sql, combined_cols, parent="%s+%s" % (dataid_a, dataid_b), op="merge(%s,%s,on=%s)" % (dataid_a, dataid_b, on), parents=[dataid_a, dataid_b])

    def compare_periods(self, dataid, metric_id, p1s, p1e, p2s, p2e, dimensions):
        source = self.datasets[dataid]
        metric_exprs = {
            "gmv": "SUM(__sell_through)",
            "order_count": "COUNT(DISTINCT __order_id)",
            "aov": "SUM(__sell_through) / NULLIF(COUNT(DISTINCT __order_id), 0)",
            "avg_price": "AVG(__unit_price)",
        }
        mexpr = metric_exprs.get(metric_id, "COUNT(*)")
        dim_key = dimensions[0] if dimensions else "channel"

        p1_filters = [
            "__paid_at >= '%s'" % p1s.strftime("%Y-%m-%d %H:%M:%S"),
            "__paid_at < '%s'" % p1e.strftime("%Y-%m-%d %H:%M:%S"),
            "__order_status IN ('paid', 'completed')",
        ]
        p1_sql = "SELECT *\nFROM %s\nWHERE " % dataid + " AND ".join(p1_filters)
        p1_id = self._register(source.model, p1_sql, source.columns, parent=dataid, op="compare_periods.p1_filter")
        p1_agg = "SELECT %s, %s AS %s_p1\nFROM %s\nGROUP BY %s" % (dim_key, mexpr, metric_id, p1_id, dim_key)
        p1_agg_id = self._register(source.model, p1_agg, [dim_key, "%s_p1" % metric_id], parent=p1_id, op="compare_periods.p1_agg")

        p2_filters = [
            "__paid_at >= '%s'" % p2s.strftime("%Y-%m-%d %H:%M:%S"),
            "__paid_at < '%s'" % p2e.strftime("%Y-%m-%d %H:%M:%S"),
            "__order_status IN ('paid', 'completed')",
        ]
        p2_sql = "SELECT *\nFROM %s\nWHERE " % dataid + " AND ".join(p2_filters)
        p2_id = self._register(source.model, p2_sql, source.columns, parent=dataid, op="compare_periods.p2_filter")
        p2_agg = "SELECT %s, %s AS %s_p2\nFROM %s\nGROUP BY %s" % (dim_key, mexpr, metric_id, p2_id, dim_key)
        p2_agg_id = self._register(source.model, p2_agg, [dim_key, "%s_p2" % metric_id], parent=p2_id, op="compare_periods.p2_agg")

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


__all__ = ["DAGAgentRuntime", "Dataset", "SEM"]
