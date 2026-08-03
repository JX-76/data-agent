#!/usr/bin/env python3
"""Data Agent MVP v2.

This demo follows the AI calling layer design:
- Agent does not freely generate SQL.
- Agent sees semantic single-table models, not raw warehouse topology.
- Natural language is mapped into a small sequence of deterministic tools.
- Each tool returns a dataid; the runtime compiles the dataid chain into CTE SQL.
"""
import argparse
import datetime as dt
import re
import sys
from pathlib import Path

from runtime_core import AgentRuntime as BaseAgentRuntime, Dataset, validate_sql
from semantic_utils import DANGEROUS, SENSITIVE, load_semantic_layer as load_semantic_layer_shared, yaml

try:
    from llm_router import llm_route_and_plan
    from db_executor import get_db as _get_db
except ImportError:
    llm_route_and_plan = None
    _get_db = None

BASE = Path(__file__).resolve().parents[1]
NOW = dt.datetime(2026, 6, 28, 12, 0, 0)


def load_semantic_layer():
    return load_semantic_layer_shared(table_index=True)


SEM = load_semantic_layer() if yaml else None


def parse_time(q):
    if "昨天" in q:
        start = (NOW - dt.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        end = NOW.replace(hour=0, minute=0, second=0, microsecond=0)
        return start, end
    if "最近7天" in q or "近7天" in q:
        end = NOW.replace(hour=0, minute=0, second=0, microsecond=0)
        start = end - dt.timedelta(days=7)
        return start, end
    if "本月" in q:
        start = NOW.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end = NOW
        return start, end
    if "上周" in q:
        this_monday = (NOW - dt.timedelta(days=NOW.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        return this_monday - dt.timedelta(days=7), this_monday
    return None


def detect_metric(q):
    q_lower = q.lower()
    for mid, m in SEM["metrics"].items():
        for s in m.get("synonyms", []):
            if s.lower() in q_lower:
                return mid
    return None


def detect_dimensions(q):
    dims = []
    for did, d in SEM["dimensions"].items():
        if any(s in q for s in d.get("synonyms", [])):
            dims.append(did)
    if "各" in q and "渠道" in q and "channel" not in dims:
        dims.append("channel")
    if any(x in q for x in ["各大区", "各区域", "地区", "大区"]) and "region" not in dims:
        dims.append("region")
    if any(x in q for x in ["各品类", "各类目", "品类", "分类", "类目"]) and "category" not in dims:
        dims.append("category")
    if any(x in q for x in ["趋势", "按天", "每天"]) and "date" not in dims:
        dims.append("date")
    return dims


def choose_model(metric_id, dims, query=""):
    candidates = list(SEM["models"].values())
    if any(x in query for x in ["用户", "用户概览", "user"]):
        candidates.sort(key=lambda m: 0 if m["id"] == "user_summary" else 1)
    if any(x in query for x in ["品类", "类目", "商品", "产品", "category", "product"]):
        candidates.sort(key=lambda m: 0 if m["id"] == "product_analysis" else 1)
    for model in candidates:
        visible = set(model.get("visible_dimensions", []))
        if set(dims).issubset(visible):
            return model["id"]
    return None


def route_and_plan(query, use_llm=False):
    if use_llm and llm_route_and_plan is not None:
        try:
            plan = llm_route_and_plan(query)
            if plan.get("status") not in ("error", "router_error"):
                return plan
        except Exception as e:
            logger.warning("bare_exception_caught", error=str(e))
            pass  # fall through to regex router
    q_lower = query.lower()
    if any(x in q_lower for x in DANGEROUS):
        return {"status": "blocked", "intent": "blocked", "reason": "写操作或危险操作被禁止"}
    if any(x in q_lower for x in SENSITIVE):
        return {"status": "blocked", "intent": "blocked", "reason": "敏感字段不允许通过 AI 调用层访问"}

    metric = detect_metric(query)
    time_range = parse_time(query)
    dims = detect_dimensions(query)

    if not metric:
        return {"status": "need_clarification", "intent": "clarification", "reason": "缺少指标"}
    if not time_range:
        return {"status": "need_clarification", "intent": "clarification", "reason": "缺少时间范围"}

    illegal_dims = [d for d in dims if d not in SEM["metrics"][metric].get("allowed_dimensions", [])]
    if illegal_dims:
        return {"status": "need_clarification", "intent": "clarification", "reason": "指标 %s 不支持维度 %s" % (metric, illegal_dims)}

    model = choose_model(metric, dims, query)
    if not model:
        return {"status": "need_clarification", "intent": "clarification", "reason": "没有可用的 AI 单表模型覆盖这些维度"}

    intent = "breakdown" if dims else "metric_query"
    # Merge detection: if query contains two metrics with comparison intent
    if dims and _has_multi_metric(query, metric):
        intent = "merge"
    clarification = None
    if metric == "gmv" and "口径" in query:
        clarification = {
            "metric": metric,
            "question": "GMV 口径确认：你想看总 GMV 还是按维度拆分后的 GMV？",
            "options": [
                {"id": "metric_query", "label": "总 GMV", "description": "默认口径，直接返回一个数值"},
                {"id": "breakdown", "label": "按维度拆分", "description": "例如按渠道/区域/日期拆分"},
            ],
        }
    plan = {"status": "ok", "intent": intent, "model": model, "metric": metric, "dimensions": dims, "time_range": time_range, "clarification": clarification}
    if intent == "merge":
        # Find the second metric for merge
        for mid, m in SEM["metrics"].items():
            if mid != metric:
                for s in m.get("synonyms", []):
                    if s.lower() in query.lower():
                        plan["metrics"] = [metric, mid]
                        plan["merge_on"] = dims[0] if dims else "channel"
                        break
                if "metrics" in plan:
                    break
    return plan


def _has_multi_metric(query, primary):
    """Detect if query asks for two metrics to be compared."""
    for mid, m in SEM["metrics"].items():
        if mid == primary:
            continue
        for s in m.get("synonyms", []):
            if s.lower() in query.lower():
                return True
    return False


class AgentRuntime(BaseAgentRuntime):
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
        fields.extend(["fct_orders.sell_through AS __sell_through", "fct_orders.order_id AS __order_id", "fct_orders.paid_at AS __paid_at", "fct_orders.order_status AS __order_status"])
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
            sample_rows["row_count"] = "demo: 无真实数据"
        return self._register(model_id, sql, all_columns, op="switch(%s)" % model_id, sample_rows=sample_rows)

    def preview(self, dataid, n=5, db=None):
        source = self.datasets[dataid]
        sql = "SELECT *\nFROM %s\nLIMIT %s" % (dataid, n)
        sample_rows = {"preview_of": dataid, "columns": source.columns, "model": source.model, "limit": n}
        if db is not None:
            # Compile and execute the chain up to this dataid
            chain_sql = self.compile_sql(dataid, limit=n)
            rows = db.execute_cte(chain_sql)
            sample_rows["rows"] = rows
            sample_rows["row_count"] = len(rows)
        else:
            sample_rows["rows"] = "demo: 无真实数据"
            sample_rows["note"] = "Agent 可在此节点查看前 %s 行样本" % n
        return self._register(source.model, sql, source.columns, parent=dataid, op="preview(%s,%s)" % (dataid, n), sample_rows=sample_rows)

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

    def sort(self, dataid, by, order="DESC"):
        source = self.datasets[dataid]
        if by not in source.columns:
            raise ValueError("sort field %s not in %s" % (by, source.columns))
        sql = "SELECT *\nFROM %s\nORDER BY %s %s" % (dataid, by, order)
        return self._register(source.model, sql, source.columns, parent=dataid, op="sort(%s,%s)" % (by, order))

    def merge(self, dataid_a, dataid_b, on):
        """Merge two datasets on a shared dimension column (JOIN)."""
        source_a = self.datasets[dataid_a]
        source_b = self.datasets[dataid_b]
        if on not in source_a.columns or on not in source_b.columns:
            raise ValueError("merge key %s not in both datasets" % on)
        # Build joined column list (dedup the join key)
        combined_cols = list(source_a.columns)
        for c in source_b.columns:
            if c != on and c not in combined_cols:
                combined_cols.append(c)
        sql = "SELECT a.*, %s\nFROM %s a\nJOIN %s b ON a.%s = b.%s" % (", ".join(["b.%s AS %s" % (c, c) for c in source_b.columns if c != on]), dataid_a, dataid_b, on, on)
        return self._register(source_a.model, sql, combined_cols, parent="%s+%s" % (dataid_a, dataid_b), op="merge(%s,%s,on=%s)" % (dataid_a, dataid_b, on), parents=[dataid_a, dataid_b])

    # ── v5 tools ──

    def top(self, dataid, by, n=5, order="DESC"):
        """Sort and limit to top/bottom N rows."""
        source = self.datasets[dataid]
        if by not in source.columns:
            raise ValueError("top field %s not in %s" % (by, source.columns))
        sql = "SELECT *\nFROM %s\nORDER BY %s %s\nLIMIT %s" % (dataid, by, order, n)
        return self._register(source.model, sql, source.columns, parent=dataid, op="top(%s,%s,%s)" % (by, n, order))

    def filter_value(self, dataid, dimension, value):
        """Filter by a specific dimension value (e.g. channel='online')."""
        source = self.datasets[dataid]
        if dimension not in source.columns:
            raise ValueError("filter_value dimension %s not in %s" % (dimension, source.columns))
        # Support exclusion: value starting with ! means NOT
        if value.startswith("!"):
            sql = "SELECT *\nFROM %s\nWHERE %s != '%s'" % (dataid, dimension, value[1:])
            op_str = "filter_value(%s!=%s)" % (dimension, value[1:])
        else:
            sql = "SELECT *\nFROM %s\nWHERE %s = '%s'" % (dataid, dimension, value)
            op_str = "filter_value(%s=%s)" % (dimension, value)
        return self._register(source.model, sql, source.columns, parent=dataid, op=op_str)

    def compare_periods(self, dataid, metric_id, period1_start, period1_end, period2_start, period2_end, dimensions):
        """Compare two time periods: returns both aggregates merged on dimensions.

        Internally does: filter(p1) → aggregate → filter(p2) → aggregate → merge
        """
        source = self.datasets[dataid]
        metric_exprs = {
            "gmv": "SUM(__sell_through)",
            "order_count": "COUNT(DISTINCT __order_id)",
            "aov": "SUM(__sell_through) / NULLIF(COUNT(DISTINCT __order_id), 0)",
            "avg_price": "AVG(__unit_price)",
        }
        mexpr = metric_exprs.get(metric_id, "COUNT(*)")

        # Period 1 CTE
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

        # Period 2 CTE
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

        # Merge on dimension
        combined = [dim_key, "%s_p1" % metric_id, "%s_p2" % metric_id]
        merge_sql = "SELECT a.*, b.%s_p2\nFROM %s a\nLEFT JOIN %s b ON a.%s = b.%s" % (metric_id, p1_agg_id, p2_agg_id, dim_key, dim_key)
        return self._register(source.model, merge_sql, combined, parent="%s+%s" % (p1_agg_id, p2_agg_id), op="compare_periods(%s)" % metric_id, parents=[p1_agg_id, p2_agg_id])

    def compile_sql(self, final_dataid, limit=100):
        # Collect all reachable dataids via BFS, then build CTEs in dependency order
        ordered = []
        visited = set()
        stack = [(final_dataid, False)]  # (dataid, done)
        while stack:
            did, done = stack.pop()
            if done:
                ordered.append(did)
                continue
            if did in visited or did not in self.datasets:
                continue
            visited.add(did)
            ds = self.datasets[did]
            stack.append((did, True))  # revisit to add after children
            if ds.parents:
                for p in reversed(ds.parents):
                    stack.append((p, False))
            elif ds.parent and ds.parent != did and ds.parent in self.datasets:
                stack.append((ds.parent, False))
        # ordered now has leaf nodes first, root last (post-order from DFS)
        ctes = ["%s AS (\n%s\n)" % (did, self.datasets[did].sql) for did in ordered if did in self.datasets]
        return "WITH\n" + ",\n".join(ctes) + "\nSELECT * FROM %s\nLIMIT %s" % (final_dataid, limit)


def execute_plan(plan, db=None):
    rt = AgentRuntime()
    d1 = rt.switch(plan["model"], db=db)
    if plan.get("clarification"):
        preview_id = rt.preview(d1, 5, db=db)
        return rt, preview_id, None
    start, end = plan["time_range"]

    # Merge intent: two metrics joined on a shared dimension
    if plan.get("intent") == "merge" and plan.get("metrics"):
        m1, m2 = plan["metrics"]
        on = plan.get("merge_on", plan["dimensions"][0] if plan["dimensions"] else "channel")
        dims = plan.get("dimensions", [on])
        # Metric 1
        d2a = rt.filter_time_and_defaults(d1, m1, start, end)
        d3a = rt.aggregate(d2a, m1, dims)
        # Metric 2
        d2b = rt.filter_time_and_defaults(d1, m2, start, end)
        d3b = rt.aggregate(d2b, m2, dims)
        # Merge
        d4 = rt.merge(d3a, d3b, on)
        final = rt.sort(d4, m1, "DESC")
        return rt, final, rt.compile_sql(final)

    d2 = rt.filter_time_and_defaults(d1, plan["metric"], start, end)
    d3 = rt.aggregate(d2, plan["metric"], plan["dimensions"])
    final = d3
    if plan["dimensions"]:
        final = rt.sort(d3, plan["metric"], "DESC")
    return rt, final, rt.compile_sql(final)


def run(query, use_llm=False, use_db=False):
    plan = route_and_plan(query, use_llm=use_llm)
    if plan["status"] != "ok":
        out = {"plan": plan, "tools": [], "sql": None, "valid": None}
        from contracts import normalize_result
        return normalize_result(out, query=query, used_db=use_db, used_llm=use_llm)
    db = _get_db() if (use_db and _get_db is not None) else None
    runtime, final_dataid, sql = execute_plan(plan, db=db)
    out = {"plan": plan, "tools": runtime.trace, "final_dataid": final_dataid, "sql": sql, "valid": None}
    if sql:
        ok, reason = validate_sql(sql)
        out["valid"] = {"ok": ok, "reason": reason}
        # Run the final SQL against DB if available
        if db is not None:
            results = db.execute_cte(sql)
            out["results"] = results
            # ── Analysis Layer ──
            try:
                from analysis import analyze as _analyze
                out["analysis"] = _analyze(results, dimensions=plan.get("dimensions", []), metric=plan.get("metric", ""))
            except ImportError:
                pass
            # ── NL Insight + Chart Layer ──
            try:
                from nlu import generate_insight as _nl_insight
                out["insight"] = _nl_insight(query, sql, results, out.get("analysis", {}))
            except ImportError:
                pass
    if plan.get("clarification"):
        out["clarification"] = plan["clarification"]
    from contracts import normalize_result
    return normalize_result(out, query=query, used_db=use_db, used_llm=use_llm)


def resume(plan, choice_id, db=None):
    """Resume a plan that returned clarification: apply user's choice and execute."""
    clarification = plan.get("clarification")
    if not clarification:
        return {"error": "plan has no pending clarification"}
    options = clarification.get("options", [])
    if not any(o["id"] == choice_id for o in options):
        return {"error": "invalid choice '%s', valid: %s" % (choice_id, [o["id"] for o in options])}

    plan = dict(plan)
    plan.pop("clarification", None)

    if choice_id == "breakdown":
        plan["dimensions"] = ["channel"]
        plan["intent"] = "breakdown"
    elif choice_id == "metric_query":
        plan["dimensions"] = []
        plan["intent"] = "metric_query"

    runtime, final_dataid, sql = execute_plan(plan, db=db)
    out = {"plan": plan, "tools": runtime.trace, "final_dataid": final_dataid, "sql": sql, "valid": None}
    if sql:
        ok, reason = validate_sql(sql)
        out["valid"] = {"ok": ok, "reason": reason}
        if db is not None:
            results = db.execute_cte(sql)
            out["results"] = results
            try:
                from analysis import analyze as _analyze
                out["analysis"] = _analyze(results, dimensions=plan.get("dimensions", []), metric=plan.get("metric", ""))
            except ImportError:
                pass
            try:
                from nlu import generate_insight as _nl_insight
                out["insight"] = _nl_insight(plan.get("query", ""), sql, results, out.get("analysis", {}))
            except ImportError:
                pass
    from contracts import normalize_result
    return normalize_result(out, query=plan.get("query", ""), used_db=bool(db), used_llm=False)


# ── Multi-turn Session ──

class Session:
    """Stateful multi-turn Data Agent session with context memory."""
    def __init__(self):
        self.history = []  # [(query, plan, results, analysis, insight)]
        self.turns = 0

    def query(self, q, use_llm=False, use_db=True):
        result = run(q, use_llm=use_llm, use_db=use_db)
        # Enrich with context hints
        if self.history:
            result["_context"] = {
                "previous_turns": self.turns,
                "last_query": self.history[-1][0],
                "last_dims": self.history[-1][1].get("dimensions", []) if len(self.history[-1]) > 1 else [],
            }
            # Auto-infer drill-down: if user asks a follow-up, reuse model from history
            prev_plan = self.history[-1][1]
            if result.get("plan", {}).get("status") == "ok" and prev_plan.get("status") == "ok":
                # Check if it looks like a drill-down (short query, no explicit model)
                if not result["plan"].get("dimensions") and prev_plan.get("dimensions"):
                    # Could be a drill-down; suggest
                    result["_drill_hint"] = "Previous query used dimensions: %s" % prev_plan["dimensions"]

        self.history.append((q, result.get("plan", {}), result.get("results"), result.get("analysis"), result.get("insight")))
        self.turns += 1
        return result

    def drill_down(self, dimension):
        """Drill into a dimension from the last query."""
        if not self.history:
            return {"error": "No previous query to drill down from"}
        last_q, last_plan = self.history[-1][0], self.history[-1][1]
        plan = dict(last_plan)
        dims = list(plan.get("dimensions", []))
        if dimension not in dims:
            dims.append(dimension)
        plan["dimensions"] = dims
        plan["intent"] = "breakdown"
        runtime, final_dataid, sql = execute_plan(plan)
        out = {"plan": plan, "tools": runtime.trace, "final_dataid": final_dataid, "sql": sql, "valid": validate_sql(sql)[0] if sql else None}
        if sql:
            db = _get_db() if _get_db else None
            if db is not None:
                results = db.execute_cte(sql)
                out["results"] = results
                try:
                    from analysis import analyze as _analyze
                    out["analysis"] = _analyze(results, dimensions=dims, metric=plan.get("metric", ""))
                except ImportError:
                    pass
                try:
                    from nlu import generate_insight as _nl_insight
                    out["insight"] = _nl_insight(last_q, sql, results, out.get("analysis", {}))
                except ImportError:
                    pass
        self.history.append(("drill_down(%s)" % dimension, plan, out.get("results"), out.get("analysis"), out.get("insight")))
        self.turns += 1
        return out


def catalog():
    """Return the semantic layer catalog for agent self-awareness."""
    from config import SEMANTIC_SUMMARY as _SS
    return {
        "models": _SS["models"],
        "metrics": _SS["metrics"],
        "dimensions": _SS["dimensions"],
        "tools": ["switch", "preview", "filter", "aggregate", "sort", "top", "filter_value", "merge", "compare_periods", "catalog"],
    }


def load_yaml(path):
    if yaml is None:
        raise RuntimeError("PyYAML is required for eval mode")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _validate_out(case_id, out, exp):
    plan = out["plan"]
    errors = []
    for k in ["status", "intent"]:
        if exp.get(k) and plan.get(k) != exp[k]:
            errors.append("%s: expected %s, got %s" % (k, exp[k], plan.get(k)))
    for k in ["model", "metric"]:
        if exp.get(k) and plan.get(k) != exp[k]:
            errors.append("%s: expected %s, got %s" % (k, exp[k], plan.get(k)))
    if "dimensions" in exp and plan.get("dimensions", []) != exp["dimensions"]:
        errors.append("dimensions: expected %s, got %s" % (exp["dimensions"], plan.get("dimensions")))
    if exp.get("status") == "ok":
        if exp.get("clarification"):
            if not out.get("clarification"):
                errors.append("clarification expected but missing")
        elif not out["valid"] or not out["valid"]["ok"]:
            errors.append("sql invalid: %s" % out["valid"])
        for tool in exp.get("tools_include", []):
            if not any(tool in t["op"] for t in out["tools"]):
                errors.append("tool trace missing: %s" % tool)
        for s in exp.get("sql_contains", []):
            if not out.get("sql") or s not in out["sql"]:
                errors.append("sql missing: %s" % s)
    return errors


def eval_cases(path):
    data = load_yaml(path)
    failures = []
    total = 0
    passed = 0
    for case in data["cases"]:
        out = run(case["query"])
        exp = case["expected"]
        errors = _validate_out(case["id"], out, exp)
        if errors:
            failures.append((case["id"], errors, out))
        else:
            print("PASS %s" % case["id"])
            passed += 1
        total += 1

        if case.get("resume"):
            choice = case["resume"]["choice"]
            resume_exp = case["resume"]["expected"]
            out2 = resume(out["plan"], choice)
            errors2 = _validate_out("%s.resume" % case["id"], out2, resume_exp)
            label = "%s.resume" % case["id"]
            if errors2:
                failures.append((label, errors2, out2))
            else:
                print("PASS %s" % label)
                passed += 1
            total += 1

    if failures:
        print("\nFAILURES:")
        for cid, errors, out in failures:
            print(cid, errors)
            print(out)
        return 1
    print("\nALL PASSED: %s/%s cases" % (passed, total))
    return 0


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--eval")
    p.add_argument("--react", action="store_true", help="Use ReAct agent loop instead of pipeline")
    p.add_argument("--db", action="store_true", help="Use SQLite DB executor for real data")
    p.add_argument("query", nargs="*")
    args = p.parse_args()
    if args.eval:
        sys.exit(eval_cases(BASE / args.eval if not Path(args.eval).is_absolute() else Path(args.eval)))
    q = " ".join(args.query) or "昨天 GMV 是多少？"

    if args.react:
        import json as _json
        from agent_loop import react_loop as _react
        result = _react(q, use_db=args.db)
        print(_json.dumps({
            "query": result["query"],
            "steps": len(result["steps"]),
            "insight": result["insight"],
            "chart": result["chart"],
            "sql": result["sql"] is not None,
            "results": result["results"],
        }, ensure_ascii=False, indent=2))
    else:
        out = run(q, use_db=args.db)
        import pprint
        pp = pprint.PrettyPrinter(indent=2, width=120)
        pp.pprint({k: v for k, v in out.items() if k != "tools"})
        if "analysis" in out:
            print("\n── ANALYSIS ──")
            pp.pprint(out["analysis"])
        if "insight" in out:
            print("\n── INSIGHT ──")
            i = out["insight"]
            if isinstance(i, dict):
                print(i.get("insight", ""))
                if i.get("chart"):
                    print("\n  Chart recommendation: %s" % i["chart"])
            else:
                print(i)


if __name__ == "__main__":
    main()
