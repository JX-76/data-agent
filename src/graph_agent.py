# -*- coding: utf-8 -*-
"""Graph-based Data Agent — DAG nodes for the Data Agent workflow.

Built on Nucleus: each node is a pure function State → State.

Flow:
    __start__ → Route
                    │
          ┌─────────┼──────────┐
          ▼         ▼          ▼
      blocked   clarify    Switch
                   │          │
                (user)    Preview
                   │          │
                [resume]  Filter
                             │
                    ┌────────┼──────────┐
                    ▼        ▼          ▼
               Aggregate  FilterByValue  ComparePeriods
                    │        │               │
                    └────────┼───────────────┘
                             │
                    ┌────────┼──────────┐
                    ▼        ▼          ▼
                 Sort    Top(N)      Merge
                    │        │          │
                    └────────┼──────────┘
                             ▼
                         Analyze
                             │
                             ▼
                         Output
"""

from __future__ import annotations
import datetime as dt
from pathlib import Path

from nucleus import Graph, Interrupt
from dag_agent import (
    AgentRuntime, SEM, route_and_plan, validate_sql,
    _get_db, DEEPSEEK_KEY, DEEPSEEK_BASE, ANALYSIS_MODEL,
)
from semantic_utils import SENSITIVE
from contracts import normalize_status
import json


def build_data_agent_graph(use_db: bool = True, use_llm: bool = False) -> Graph:
    """Build the canonical Data Agent DAG.

    Args:
        use_db: Whether to connect to SQLite for real execution
        use_llm: Whether to use LLM routing

    Returns:
        A compiled Graph ready for execution.
    """
    g = Graph("data_agent")
    db = _get_db() if use_db else None

    # ── Shared state initialization ──

    def _ensure_rt(state):
        if "rt" not in state:
            state["rt"] = AgentRuntime()
        return state

    # ── Node: Route ──

    @g.node(description="Route the query to produce an execution plan")
    def route(state):
        import re
        _ensure_rt(state)
        query = state.get("query", "")

        # P2-3: 会话命令检测
        try:
            from session_manager import get_session
            session = get_session()
            cmd = session.parse_command(query)
            if cmd:
                state["_done"] = True
                if cmd["command"] == "history":
                    state["output"] = {
                        "status": "ok",
                        "session_command": cmd["command"],
                        "history": cmd.get("entries", []),
                        "message": f"最近 {len(cmd.get('entries', []))} 步历史",
                    }
                elif cmd["command"] in ("reset", "undo"):
                    if cmd.get("state"):
                        for k, v in cmd["state"].items():
                            if not k.startswith("_"):
                                state[k] = v
                    state["output"] = {
                        "status": "ok",
                        "session_command": cmd["command"],
                        "message": "会话已重置" if cmd["command"] == "reset" else f"已回退 {cmd.get('steps', 1)} 步",
                        "current_state": {k: state.get(k) for k in ("query", "metric", "dimensions", "intent")},
                    }
                return state
        except Exception as e:
            import structlog
            structlog.get_logger("graph_agent").warning("session_cmd_failed",
                                                         error=str(e)[:200])

        # P2-6: 异步任务状态查询
        if re.search(r"查进度|任务状态|任务列表", query):
            import re as _re
            task_id_match = _re.search(r"([a-f0-9]{12})", query)
            try:
                from async_tasks import get_async_engine
                engine = get_async_engine()
                if task_id_match:
                    task_id = task_id_match.group(1)
                    s = engine.status(task_id)
                    state["_done"] = True
                    state["output"] = {
                        "status": "ok",
                        "session_command": "task_status",
                        "task": s,
                        "message": f"任务 {task_id}: {s['state']}",
                    }
                else:
                    tasks = engine.list_tasks(limit=10)
                    state["_done"] = True
                    state["output"] = {
                        "status": "ok",
                        "session_command": "task_list",
                        "tasks": tasks,
                        "message": f"最近 {len(tasks)} 个任务",
                    }
                return state
            except Exception as e:
                import structlog
                structlog.get_logger("graph_agent").warning("task_status_failed",
                                                             error=str(e)[:200])

        # If resuming after clarification, use the already-set state (don't re-parse)
        if state.get("__resume_payload__") and state.get("plan"):
            # Re-sync plan fields that may have changed during resume
            plan = state["plan"]
            state["model"] = plan.get("model", "order_detail")
            state["metric"] = plan.get("metric", "gmv")
            state["dimensions"] = state.get("dimensions", plan.get("dimensions", []))
            state["intent"] = state.get("intent", plan.get("intent", "metric_query"))
            state["clarification"] = plan.get("clarification")
            state["blocked_reason"] = plan.get("blocked_reason")
            return state

        plan = route_and_plan(query, use_llm=use_llm)
        state["plan"] = plan
        state["model"] = plan.get("model", "order_detail")
        state["metric"] = plan.get("metric", "gmv")
        state["dimensions"] = plan.get("dimensions", [])
        state["intent"] = plan.get("intent", "metric_query")
        state["time_range"] = plan.get("time_range", (dt.datetime.now() - dt.timedelta(days=1), dt.datetime.now()))
        state["clarification"] = plan.get("clarification")
        state["blocked_reason"] = plan.get("blocked_reason")
        return state

    def route_condition(state):
        if state.get("blocked_reason"):
            return "blocked"
        if state.get("clarification"):
            return "clarify"
        return "switch"

    # ── Node: Blocked ──

    @g.node(description="Handle blocked/unsafe queries")
    def blocked(state):
        state["output"] = {
            "status": "blocked",
            "reason": state.get("blocked_reason", "Query blocked"),
            "query": state.get("query", ""),
        }
        state["_done"] = True
        return state

    # ── Node: Clarify ──

    @g.node(description="Request clarification from the user")
    def clarify(state):
        _ensure_rt(state)
        clarification = state.get("clarification", {})
        raise Interrupt({
            "type": "clarification",
            "question": clarification.get("question", "请选择分析口径"),
            "options": clarification.get("options", []),
        })

    # ── Node: Switch ──

    @g.node(description="Switch to the selected semantic model")
    def switch(state):
        _ensure_rt(state)
        rt = state["rt"]
        model = state.get("model", "order_detail")
        dataid = rt.switch(model, db=db)
        state["current_dataid"] = dataid
        state["_root_dataid"] = dataid  # Remember the root for filter to use
        # Store results for downstream
        sample = rt.trace[-1].get("sample_rows", {})
        state["_switch_sample"] = sample
        return state

    # ── Node: Preview ──

    @g.node(description="Preview sample rows of the current dataset")
    def preview(state):
        _ensure_rt(state)
        rt = state["rt"]
        dataid = state.get("current_dataid", "d1")
        new_dataid = rt.preview(dataid, 5, db=db)
        state["_preview_dataid"] = new_dataid  # Preview is a dead-end branch; don't use for filter
        state["current_dataid"] = dataid  # Restore root dataid for downstream
        sample = rt.trace[-1].get("sample_rows", {})
        state["_preview_sample"] = sample
        return state

    # ── Node: Filter ──

    @g.node(description="Filter by time range and apply default metric filters")
    def filter_node(state):
        _ensure_rt(state)
        rt = state["rt"]
        dataid = state.get("current_dataid", "d2")
        metric = state.get("metric", "gmv")
        start, end = state.get("time_range", (dt.datetime.now() - dt.timedelta(days=1), dt.datetime.now()))
        new_dataid = rt.filter_time_and_defaults(dataid, metric, start, end)
        state["current_dataid"] = new_dataid
        return state

    # ── Node: Aggregate ──

    @g.node(description="Aggregate by dimensions")
    def aggregate(state):
        _ensure_rt(state)
        rt = state["rt"]
        dataid = state.get("current_dataid", "d3")
        metric = state.get("metric", "gmv")
        dims = state.get("dimensions", [])
        new_dataid = rt.aggregate(dataid, metric, dims)
        state["current_dataid"] = new_dataid
        return state

    def aggregate_condition(state):
        intent = state.get("intent", "")
        if intent == "merge":
            return "merge_dual"
        if intent == "compare_periods":
            return "compare_periods"
        if intent == "time_compare":
            return "time_compare"
        if intent == "composition":
            return "composition"
        if intent == "anomaly_detection":
            return "anomaly_detection"
        if intent == "root_cause":
            return "root_cause"
        if intent == "top_n":
            return "top_n"
        # P1-2: 子查询 → 先 aggregate 再 subquery
        if intent == "subquery":
            return "aggregate"
        if intent == "subquery_intersect":
            return "aggregate"
        # P2-4: 漏斗分析 → 先 aggregate 再 funnel
        if intent == "funnel_analysis":
            return "aggregate"
        # P2-6: 异步任务 → 直接到 async_task
        if intent == "async_task":
            return "async_task"
        # metric_query / breakdown / filter_value: run aggregate first
        return "aggregate"

    # ── Node: Filter Value ──

    @g.node(description="Filter aggregated results by a specific dimension value")
    def filter_value_node(state):
        _ensure_rt(state)
        rt = state["rt"]
        dataid = state.get("current_dataid", "d4")
        plan = state.get("plan", {})
        filter_dim = plan.get("filter_dim", "channel")
        filter_val = plan.get("filter_val", "")
        new_dataid = rt.filter_value(dataid, filter_dim, filter_val)
        state["current_dataid"] = new_dataid
        return state

    def filter_value_condition(state):
        intent = state.get("intent", "")
        if intent == "filter_value":
            return "filter_value_node"
        return "sort_or_top"

    # ── Node: Merge Dual ──

    @g.node(description="Run multiple metric aggregations and merge them")
    def merge_dual(state):
        _ensure_rt(state)
        rt = state["rt"]
        plan = state.get("plan", {})
        metrics = plan.get("metrics", [state.get("metric", "gmv"), "order_count"])
        if len(metrics) < 2:
            metrics = [state.get("metric", "gmv"), "order_count"]
        on = plan.get("merge_on", state.get("dimensions", ["channel"])[0] if state.get("dimensions") else "channel")
        dims = plan.get("dimensions") or [on]
        start, end = state.get("time_range", (dt.datetime.now() - dt.timedelta(days=1), dt.datetime.now()))
        dataid = state.get("current_dataid", "d3")

        # P1-3: 支持N个指标并行聚合
        from concurrent.futures import ThreadPoolExecutor, as_completed
        import structlog
        logger = structlog.get_logger("graph_agent")

        def _filter_agg(metric_id, trace_label):
            """Filter + aggregate for a single metric."""
            d_filtered = rt.filter_time_and_defaults(dataid, metric_id, start, end)
            d_agg = rt.aggregate(d_filtered, metric_id, dims)
            return d_agg, trace_label

        # 并行处理所有指标
        results = {}
        max_workers = min(len(metrics), 8)
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(_filter_agg, m, f"metric_{i}"): m
                for i, m in enumerate(metrics)
            }
            for future in as_completed(futures):
                metric_name = futures[future]
                try:
                    d_id, label = future.result()
                    results[label] = d_id
                except Exception as e:
                    logger.warning("merge_metric_failed", metric=metric_name, error=str(e)[:200])
                    d_filtered = rt.filter_time_and_defaults(dataid, metric_name, start, end)
                    results[f"metric_{metrics.index(metric_name)}"] = rt.aggregate(d_filtered, metric_name, dims)

        # 顺序合并多个数据集
        metric_keys = sorted(results.keys(), key=lambda k: int(k.split("_")[1]))
        if len(metric_keys) < 2:
            # 只有1个指标：直接返回聚合结果
            state["current_dataid"] = results[metric_keys[0]]
            return state

        # 两两合并
        d_merged = results[metric_keys[0]]
        for key in metric_keys[1:]:
            d_merged = rt.merge(d_merged, results[key], on)
        state["current_dataid"] = d_merged
        return state

    # ── Node: Compare Periods ──

    @g.node(description="Compare two time periods")
    def compare_periods(state):
        _ensure_rt(state)
        rt = state["rt"]
        dataid = state.get("current_dataid", "d3")
        metric = state.get("metric", "gmv")
        dims = state.get("dimensions", ["channel"])

        # Default: this week vs last week
        now = dt.datetime.now()
        p1_e = now
        p1_s = now - dt.timedelta(days=7)
        p2_e = p1_s
        p2_s = p1_s - dt.timedelta(days=7)

        new_dataid = rt.compare_periods(dataid, metric, p1_s, p1_e, p2_s, p2_e, dims)
        state["current_dataid"] = new_dataid
        state["_compare_periods"] = True
        return state

    # ── Node: Sort / Top ──

    @g.node(description="Sort or top-N the aggregated results")
    def sort_or_top(state):
        _ensure_rt(state)
        rt = state["rt"]
        dataid = state.get("current_dataid", "d4")
        metric = state.get("metric", "gmv")
        dims = state.get("dimensions", [])

        # Only sort if there are dimensions (and thus metric column exists after aggregate)
        if dims or state.get("intent") == "compare_periods":
            sort_col = metric
            if state.get("_compare_periods"):
                sort_col = f"{metric}_p1"
            if sort_col not in rt.datasets[dataid].columns:
                raise RuntimeError(
                    f"sort_or_top: column '{sort_col}' not found in dataset {dataid} "
                    f"(available: {rt.datasets[dataid].columns})"
                )
            new_dataid = rt.sort_data(dataid, sort_col, "DESC")
            state["current_dataid"] = new_dataid
        return state

    # ── P1-1: Window Function Nodes ──
    @g.node(description="Apply RANK window function")
    def window_rank(state):
        _ensure_rt(state)
        rt = state["rt"]
        dataid = state.get("current_dataid", "d4")
        metric = state.get("metric", "gmv")
        plan = state.get("plan", {})
        partition_by = plan.get("window_partition", []) or state.get("dimensions", [])
        top_n = plan.get("top_n", 10)
        new_dataid = rt.rank(dataid, metric, partition_by, top_n)
        state["current_dataid"] = new_dataid
        return state

    @g.node(description="Apply cumulative/window aggregate")
    def window_aggregate(state):
        _ensure_rt(state)
        rt = state["rt"]
        dataid = state.get("current_dataid", "d4")
        metric = state.get("metric", "gmv")
        plan = state.get("plan", {})
        partition_by = plan.get("window_partition", []) or state.get("dimensions", [])
        window_func = plan.get("window_func", "cumulative")
        if window_func == "moving_avg":
            window_size = plan.get("window_size", 3)
            new_dataid = rt.moving_average(dataid, metric, window_size, partition_by)
        else:
            new_dataid = rt.cumulative(dataid, metric, partition_by)
        state["current_dataid"] = new_dataid
        return state

    @g.node(description="Apply LAG/LEAD window offset")
    def window_offset(state):
        _ensure_rt(state)
        rt = state["rt"]
        dataid = state.get("current_dataid", "d4")
        metric = state.get("metric", "gmv")
        plan = state.get("plan", {})
        partition_by = plan.get("window_partition", []) or state.get("dimensions", [])
        offset = plan.get("window_offset", 1)
        new_dataid = rt.lag_metric(dataid, metric, partition_by, offset)
        state["current_dataid"] = new_dataid
        return state

    def sort_condition(state):
        intent = state.get("intent", "")
        # P1-2: 子查询路由（在aggregate后执行）
        if intent == "subquery":
            return "subquery"
        if intent == "subquery_intersect":
            return "subquery_intersect"
        # P1-1: 窗口函数路由
        if intent == "window_rank":
            return "window_rank"
        if intent == "window_aggregate":
            return "window_aggregate"
        if intent == "window_offset":
            return "window_offset"
        # P2-4: 漏斗分析
        if intent == "funnel_analysis":
            return "funnel_analysis"
        if intent == "time_compare":
            return "time_compare"
        return "analyze"

    # ── P1-2: Subquery Nodes ──
    @g.node(description="Execute EXISTS subquery")
    def subquery(state):
        _ensure_rt(state)
        rt = state["rt"]
        dataid = state.get("current_dataid", "d3")
        plan = state.get("plan", {})
        target = plan.get("subquery_target", "user")
        conditions = plan.get("subquery_conditions", [])
        subquery_type = plan.get("subquery_type", "exists")
        
        if subquery_type == "in":
            new_dataid = rt.subquery_in(dataid, target, "")
        else:
            new_dataid = rt.subquery_exists(dataid, target, conditions or ["default"])
        state["current_dataid"] = new_dataid
        return state

    @g.node(description="Execute INTERSECT subquery")
    def subquery_intersect(state):
        _ensure_rt(state)
        rt = state["rt"]
        dataid = state.get("current_dataid", "d3")
        plan = state.get("plan", {})
        target = plan.get("subquery_target", "user")
        conditions = plan.get("subquery_conditions", [])
        new_dataid = rt.subquery_exists(dataid, target, conditions or ["default"])
        state["current_dataid"] = new_dataid
        return state

    # ── P2-6: Async Task Node ──
    @g.node(description="Submit long-running query for async execution")
    def async_task(state):
        """异步任务节点：提交长任务后立即返回 task_id。"""
        _ensure_rt(state)
        rt = state["rt"]
        query = state.get("query", "")
        dataid = state.get("current_dataid", "d3")
        metric = state.get("metric", "gmv")
        dimensions = state.get("dimensions", [])
        time_range = state.get("time_range", None)
        
        try:
            from async_tasks import get_async_engine
            engine = get_async_engine()
            
            # 包装 SQL 执行为可调用函数
            if db is not None:
                def _run():
                    sql = rt.compile_sql(dataid, limit=50000)
                    return db.execute_cte(sql)
            else:
                def _run():
                    import time
                    time.sleep(0.5)  # 模拟延时
                    return [{"message": "模拟异步执行完成"}]
            
            task_id = engine.submit(query, _run, timeout=600.0)
            
            state["_done"] = True
            state["output"] = {
                "status": "ok",
                "async": True,
                "task_id": task_id,
                "query": query[:100],
                "metric": metric,
                "dimensions": dimensions,
                "message": f"已提交后台执行（task_id: {task_id}），可发送 '查进度 {task_id}' 或 '任务列表' 查看状态",
            }
        except Exception as e:
            state["_done"] = True
            state["output"] = {"status": "error", "reason": f"Async submit failed: {e}"}
        
        return state

    # ── P2-4: Funnel Analysis Node ──
    @g.node(description="Execute funnel/conversion analysis")
    def funnel_analysis(state):
        _ensure_rt(state)
        rt = state["rt"]
        dataid = state.get("current_dataid", "d3")
        plan = state.get("plan", {})
        funnel_template = plan.get("funnel_template", "custom")
        
        # 执行SQL获取数据
        if db is not None:
            try:
                sql = rt.compile_sql(dataid, limit=5000)
                results = db.execute_cte(sql)
            except Exception as e:
                state["_done"] = True
                state["output"] = {"status": "error", "reason": f"Funnel data fetch failed: {e}"}
                return state
        else:
            # 无DB：使用模拟数据演示
            from funnel_analysis import PRESET_FUNNELS
            template = PRESET_FUNNELS.get(funnel_template, PRESET_FUNNELS["ecommerce_purchase"])
            results = [
                {"user_id": f"u{i:02d}", "event": step["keywords"][0]}
                for i in range(100)
                for step in template["steps"][:i % 4 + 1]
            ]
        
        # 执行漏斗分析
        from funnel_analysis import FunnelEngine, PRESET_FUNNELS
        engine = FunnelEngine()
        
        template = PRESET_FUNNELS.get(funnel_template)
        if template:
            steps = template["steps"]
        else:
            # 默认漏斗
            steps = PRESET_FUNNELS["ecommerce_purchase"]["steps"]
        
        funnel_result = engine.analyze(results, steps)
        
        state["results"] = results[:100]  # 前100行用于展示
        state["funnel_result"] = {
            "steps": [
                {
                    "name": s.name,
                    "user_count": s.user_count,
                    "conversion_rate": round(s.conversion_rate, 3),
                    "overall_rate": round(s.overall_rate, 3),
                    "drop_count": s.drop_count,
                    "drop_rate": round(s.drop_rate, 3),
                }
                for s in funnel_result.steps
            ],
            "overall_conversion": round(funnel_result.overall_conversion, 3),
            "bottleneck_step": funnel_result.bottleneck_step,
            "bottleneck_drop_rate": round(funnel_result.bottleneck_drop_rate, 3),
            "insights": funnel_result.insights,
            "markdown": engine.format_markdown(funnel_result),
        }
        return state

    # ── Node: Time Compare ──
    @g.node(description="Run time comparison (YoY/MoM/trend)")
    def time_compare(state):
        _ensure_rt(state)
        rt = state["rt"]
        dataid = state.get("current_dataid", "d3")
        metric = state.get("metric", "gmv")
        plan = state.get("plan", {})
        
        # Parse time config from plan
        compare_type = plan.get("compare_type", "trend")
        dimensions = state.get("dimensions", [])
        
        # Use time_intelligence module
        from time_intelligence import TimeIntelligence
        engine = TimeIntelligence()
        
        query = state.get("query", "")
        config = engine.parse_time_expression(query)
        
        # Build SQL for time comparison
        sql = engine.build_sql(metric, config, dimensions)
        state["sql"] = sql
        state["time_config"] = config.__dict__
        
        # Execute SQL
        db = state.get("_db")
        if db is not None:
            try:
                results = db.execute_cte(sql)
                state["results"] = results
                # Format results with insights
                formatted = engine.format_result(results, config)
                state["time_analysis"] = formatted
            except Exception as e:
                state["_done"] = True
                state["output"] = {"status": "error", "reason": f"Time comparison failed: {e}"}
                return state
        
        return state

    # ── Node: Composition ──
    @g.node(description="Run composition/percentage analysis")
    def composition(state):
        _ensure_rt(state)
        rt = state["rt"]
        dataid = state.get("current_dataid", "d3")
        metric = state.get("metric", "gmv")
        dimensions = state.get("dimensions", [])
        
        if not dimensions:
            # No dimensions specified, can't do composition
            state["_done"] = True
            state["output"] = {"status": "error", "reason": "Composition requires dimensions"}
            return state
        
        dimension = dimensions[0]
        
        # Build composition SQL
        from composition import build_composition_sql
        sql = build_composition_sql(dimension, metric)
        state["sql"] = sql
        
        # Execute SQL
        db = state.get("_db")
        if db is not None:
            try:
                results = db.execute_cte(sql)
                state["results"] = results
                
                # Analyze composition
                from composition import analyze_composition
                analysis = analyze_composition(results, dimension, metric)
                state["composition_analysis"] = analysis
                state["analysis"] = analysis
            except Exception as e:
                state["_done"] = True
                state["output"] = {"status": "error", "reason": f"Composition analysis failed: {e}"}
                return state
        
        return state

    # ── Node: Root Cause ──
    @g.node(description="Run root cause analysis")
    def root_cause(state):
        _ensure_rt(state)
        rt = state["rt"]
        dataid = state.get("current_dataid", "d3")
        metric = state.get("metric", "gmv")
        dimensions = state.get("dimensions", [])
        
        if not dimensions:
            state["_done"] = True
            state["output"] = {"status": "error", "reason": "Root cause analysis requires dimensions"}
            return state
        
        # Get current period data
        current_sql = rt.compile_sql(dataid, limit=100)
        db = state.get("_db")
        
        if db is not None:
            try:
                current_results = db.execute_cte(current_sql)
                state["results"] = current_results
                
                # For root cause, we need previous period data
                # Build a simple comparison
                from root_cause import RootCauseAnalyzer
                analyzer = RootCauseAnalyzer()
                
                # Get previous period data (simplified: use aggregate with time filter)
                # In practice, this would need a proper time comparison
                dimension = dimensions[0]
                
                # Mock previous data for now (in production, fetch actual previous period)
                previous_results = current_results  # Placeholder
                
                result = analyzer.analyze(metric, current_results, previous_results, dimension)
                state["root_cause"] = result
                state["analysis"] = {
                    "root_cause": {
                        "primary_dimension": result.primary_cause.dimension if result.primary_cause else None,
                        "primary_value": result.primary_cause.dimension_value if result.primary_cause else None,
                        "findings": result.findings,
                        "recommendations": result.recommendations
                    }
                }
            except Exception as e:
                state["_done"] = True
                state["output"] = {"status": "error", "reason": f"Root cause analysis failed: {e}"}
                return state
        
        return state

    # ── Node: Anomaly Detection ──
    @g.node(description="Run anomaly detection on time series data")
    def anomaly_detection(state):
        _ensure_rt(state)
        rt = state["rt"]
        dataid = state.get("current_dataid", "d3")
        metric = state.get("metric", "gmv")
        
        # Get results from previous analysis
        results = state.get("results", [])
        if not results:
            state["_done"] = True
            state["output"] = {"status": "error", "reason": "No data for anomaly detection"}
            return state
        
        # Run anomaly detection
        from analysis import analyze as _analyze
        analysis = _analyze(results, dimensions=state.get("dimensions", []), metric=metric)
        anomalies = analysis.get("anomalies", {})
        
        state["anomalies"] = anomalies
        state["analysis"] = analysis
        
        return state

    # ── Node: Top N ──
    @g.node(description="Get top N items by metric")
    def top_n(state):
        _ensure_rt(state)
        rt = state["rt"]
        dataid = state.get("current_dataid", "d3")
        metric = state.get("metric", "gmv")
        plan = state.get("plan", {})
        limit = int(plan.get("limit", 10))
        
        # Sort and limit
        new_dataid = rt.sort_data(dataid, metric, "DESC")
        # Limit is handled in output
        state["current_dataid"] = new_dataid
        state["top_n_limit"] = limit
        
        return state
        _ensure_rt(state)
        rt = state["rt"]
        dataid = state.get("current_dataid", "d4")
        metric = state.get("metric", "gmv")
        dims = state.get("dimensions", [])

        # Only sort if there are dimensions (and thus metric column exists after aggregate)
        if dims or state.get("intent") == "compare_periods":
            sort_col = metric
            if state.get("_compare_periods"):
                sort_col = f"{metric}_p1"
            if sort_col not in rt.datasets[dataid].columns:
                raise RuntimeError(
                    f"sort_or_top: column '{sort_col}' not found in dataset {dataid} "
                    f"(available: {rt.datasets[dataid].columns})"
                )
            new_dataid = rt.sort_data(dataid, sort_col, "DESC")
            state["current_dataid"] = new_dataid
        return state

    # ── Node: Analyze ──

    @g.node(description="Run analysis and generate SQL results")
    def analyze(state):
        _ensure_rt(state)
        rt = state["rt"]
        dataid = state.get("current_dataid", "d5")

        # Compile SQL
        try:
            sql = rt.compile_sql(dataid, limit=20)
            state["sql"] = sql
            state["valid"] = validate_sql(sql)
        except Exception as e:
            state["sql"] = None
            state["valid"] = (False, str(e))
            state["_done"] = True
            state["output"] = {"status": "error", "reason": f"SQL compilation failed: {e}"}
            return state

        # Execute SQL
        results = None
        if db is not None:
            try:
                results = db.execute_cte(sql)
                state["results"] = results
            except Exception as e:
                state["_done"] = True
                state["output"] = {"status": "error", "reason": f"SQL execution failed: {e}"}
                return state

        # Analysis layer
        analysis_result = None
        try:
            from analysis import analyze as _analyze
            analysis_result = _analyze(
                results or [],
                dimensions=state.get("dimensions", []),
                metric=state.get("metric", "")
            )
        except ImportError:
            import structlog
            structlog.get_logger("graph_agent").warning("analysis_import_failed",
                                                         hint="analysis module not importable")
        except Exception as e:
            import structlog
            structlog.get_logger("graph_agent").warning("analysis_exec_failed",
                                                         error=str(e)[:200])
            analysis_result = {"error": f"Analysis failed: {e}"}
        state["analysis"] = analysis_result

        # P2-5: 异动归因分析（compare_periods场景）
        if state.get("intent") == "compare_periods" and results:
            try:
                from root_cause import RootCauseEngine
                rc_engine = RootCauseEngine()
                metric = state.get("metric", "gmv")
                dims = state.get("dimensions", [])
                # 简单拆分：前半截为基期，后半截为当期
                mid = len(results) // 2
                baseline_data = results[:mid] if mid > 0 else results
                current_data = results[mid:] if mid > 0 else results
                rc_result = rc_engine.compare_two_periods(
                    current_data, baseline_data, metric, dims
                )
                state["root_cause"] = rc_result
            except Exception as e:
                import structlog
                structlog.get_logger("graph_agent").warning("root_cause_failed",
                                                              error=str(e)[:200])

        # P2-7: 统计显著性检验（compare_periods场景）
        if state.get("intent") == "compare_periods" and results:
            try:
                from stat_tests import compare_metric
                metric = state.get("metric", "gmv")
                mid = len(results) // 2
                cur_vals = [float(r.get(metric, 0) or 0) for r in results[mid:]]
                base_vals = [float(r.get(metric, 0) or 0) for r in results[:mid]]
                if cur_vals and base_vals:
                    state["stat_test"] = compare_metric(cur_vals, base_vals, metric)
            except Exception as e:
                import structlog
                structlog.get_logger("graph_agent").warning("stat_test_failed",
                                                              error=str(e)[:200])

        return state

    # ── Node: Output ──

    @g.node(description="Generate NL insights and final output")
    def output(state):
        _ensure_rt(state)

        # If already done (e.g., blocked), preserve the existing output
        if state.get("_done"):
            return state

        query = state.get("query", "")
        sql = state.get("sql", "")
        results = state.get("results", [])
        analysis = state.get("analysis", {})
        dimensions = state.get("dimensions", [])

        # 优化点2：分析层接入output节点
        # 如果已有分析结果但未被充分利用，重新格式化
        if results and not analysis.get("_formatted"):
            try:
                from analysis import analyze as _analyze
                analysis = _analyze(
                    results,
                    dimensions=dimensions,
                    metric=state.get("metric", "")
                )
                state["analysis"] = analysis
            except Exception as e:
                import structlog
                structlog.get_logger("graph_agent").warning("analysis_reformat_failed",
                                                             error=str(e)[:200])

        # Detect metrics from analysis keys
        metrics = [k for k in analysis.keys() if k not in ("summary", "trends", "top_n", "anomalies", "distribution", "recommendations", "error")]
        if not metrics:
            metrics = [state.get("metric", "")]
        metrics = [m for m in metrics if m]

        insight = {"insight": "分析完成。", "chart": {"type": "none", "reason": "no chart"}}

        if results and analysis:
            try:
                from nlu import generate_insight as _nl_insight
                insight = _nl_insight(query, sql, results, analysis,
                                      dimensions=dimensions, metrics=metrics,
                                      use_llm=use_llm)
            except ImportError:
                import structlog
                structlog.get_logger("graph_agent").warning("nlu_import_failed",
                                                             hint="nlu module not importable")
            except Exception as e:
                import structlog
                structlog.get_logger("graph_agent").warning("insight_generation_failed",
                                                             error=str(e)[:200])

        state["insight"] = insight
        
        # Generate chart HTML page if results exist
        chart_html_path = None
        if results and sql:
            try:
                from chart_renderer import render_chart_page
                import datetime as _dt
                chart_html = render_chart_page(
                    query=query,
                    sql=sql,
                    results=results,
                    analysis=analysis,
                    chart_config=insight.get("chart", {}).get("config", {}),
                    insight_text=insight.get("insight", "分析完成。"),
                )
                # Save to a temp file
                chart_dir = Path("/tmp/data_agent_charts")
                chart_dir.mkdir(parents=True, exist_ok=True)
                chart_html_path = str(chart_dir / f"chart_{_dt.datetime.now().strftime('%Y%m%d_%H%M%S')}.html")
                with open(chart_html_path, "w", encoding="utf-8") as f:
                    f.write(chart_html)
            except Exception as e:
                import structlog
                structlog.get_logger("graph_agent").warning("chart_render_failed",
                                                             error=str(e)[:200])
        
        analysis_output = analysis if isinstance(analysis, dict) else {}
        if analysis_output and "summary" not in analysis_output:
            analysis_output = dict(analysis_output)
            analysis_output.setdefault("summary", analysis_output.get("insight"))
            analysis_output.setdefault("top_n", analysis_output.get("top_n"))
        state["output"] = {
            "status": "ok",
            "query": query,
            "model": state.get("model"),
            "metric": state.get("metric"),
            "dimensions": state.get("dimensions", []),
            "intent": state.get("intent"),
            "sql": sql,
            "results": results,
            "analysis": analysis_output,
            "insight": insight,
            "chart_html_path": chart_html_path,
            "trace": state["rt"].trace,
            "valid": state.get("valid"),
        }
        
        # 接入分析模板引擎
        try:
            from analysis_template import create_analysis_engine
            engine = create_analysis_engine()
            template_data = {
                "query": query,
                "sql": sql,
                "results": results,
                "analysis": analysis,
                "insight": insight,
            }
            # 根据intent选择模板
            template_id = "daily_report"  # 默认模板
            if state.get("intent") == "sales_analysis":
                template_id = "sales_analysis"
            elif state.get("intent") == "inventory_monitor":
                template_id = "inventory_monitor"
            
            dashboard_html = engine.generate_dashboard_html(template_id, template_data)
            state["output"]["dashboard_html"] = dashboard_html
            state["output"]["template_id"] = template_id
        except ImportError:
            pass
        except Exception as e:
            import structlog
            structlog.get_logger("graph_agent").warning("template_engine_failed",
                                                         error=str(e)[:200])
        
        # P2-3: 保存会话快照
        if not state.get("output", {}).get("session_command"):
            try:
                from session_manager import get_session
                session = get_session()
                session.snapshot(state, query, summary=query[:50])
            except Exception as e:
                import structlog
                structlog.get_logger("graph_agent").warning("snapshot_failed",
                                                              error=str(e)[:200])
        
        # P2-2: 数据质量主动提示
        quality_report = None
        if results:
            try:
                from data_quality import quick_check
                quality_report = quick_check(results, dimensions or None)
                state["output"]["data_quality"] = {
                    "status": quality_report.status,
                    "score": quality_report.score,
                    "messages": quality_report.messages,
                }
            except Exception as e:
                import structlog
                structlog.get_logger("graph_agent").warning("quality_check_failed",
                                                              error=str(e)[:200])
        
        # P2-1: 结果缓存
        if sql and results:
            try:
                from query_cache import get_cache
                cache = get_cache()
                cache.set(sql, results, ttl=300)
                state["output"]["cached"] = True
                state["output"]["cache_stats"] = cache.stats()
            except Exception as e:
                import structlog
                structlog.get_logger("graph_agent").warning("cache_save_failed",
                                                              error=str(e)[:200])
        
        state["_done"] = True
        return state

    # ── Build the graph ──

    # Add nodes
    g.add_node("route", route)
    g.add_node("blocked", blocked)
    g.add_node("clarify", clarify)
    g.add_node("switch", switch)
    g.add_node("preview", preview)
    g.add_node("filter_node", filter_node)
    g.add_node("aggregate", aggregate)
    g.add_node("filter_value_node", filter_value_node)
    g.add_node("merge_dual", merge_dual)
    g.add_node("compare_periods", compare_periods)
    g.add_node("time_compare", time_compare)
    g.add_node("composition", composition)
    g.add_node("anomaly_detection", anomaly_detection)
    g.add_node("root_cause", root_cause)
    g.add_node("top_n", top_n)
    g.add_node("sort_or_top", sort_or_top)
    # P1-1: 窗口函数节点
    g.add_node("window_rank", window_rank)
    g.add_node("window_aggregate", window_aggregate)
    g.add_node("window_offset", window_offset)
    # P1-2: 子查询节点
    g.add_node("subquery", subquery)
    g.add_node("subquery_intersect", subquery_intersect)
    # P2-4: 漏斗分析节点
    g.add_node("funnel_analysis", funnel_analysis)
    # P2-6: 异步任务节点
    g.add_node("async_task", async_task)
    g.add_node("analyze", analyze)
    g.add_node("output", output)

    # Set entry and flow
    g.set_entry("route")
    g.set_finish("output")

    # Route → conditional branching
    g.conditional_edge("route", route_condition)

    # Blocked path
    g.edge("blocked", "output")

    # Clarify → route (re-evaluate with user choice applied)
    g.edge("clarify", "route")

    # Normal path: Switch → Preview → Filter
    g.edge("switch", "preview")
    g.edge("preview", "filter_node")

    # Filter → conditional (merge / compare / normal)
    g.conditional_edge("filter_node", aggregate_condition)

    # Merge path
    g.edge("merge_dual", "sort_or_top")

    # Compare periods path
    g.edge("compare_periods", "sort_or_top")

    # Time compare path
    g.edge("time_compare", "analyze")

    # Composition path
    g.edge("composition", "analyze")

    # Anomaly detection path
    g.edge("anomaly_detection", "analyze")

    # Root cause path
    g.edge("root_cause", "analyze")

    # Top N path
    g.edge("top_n", "analyze")

    # Aggregate → conditional (filter_value? sort_or_top?)
    g.conditional_edge("aggregate", filter_value_condition)
    g.edge("filter_value_node", "sort_or_top")

    # Sort → conditional (window func? analyze?)
    g.conditional_edge("sort_or_top", sort_condition)
    # P1-1: Window function edges
    g.edge("window_rank", "analyze")
    g.edge("window_aggregate", "analyze")
    g.edge("window_offset", "analyze")
    # P1-2: Subquery edges
    g.edge("subquery", "analyze")
    g.edge("subquery_intersect", "analyze")
    # P2-4: Funnel edge
    g.edge("funnel_analysis", "analyze")
    # P2-6: Async task goes directly to output
    g.edge("async_task", "output")
    g.edge("analyze", "output")

    return g


def run_graph(query: str, use_db: bool = True, use_llm: bool = False, tracer=None) -> dict:
    """Run the Data Agent graph on a query.

    Args:
        query: Natural language query
        use_db: Whether to use SQLite for real execution
        use_llm: Whether to use LLM routing
        tracer: Optional TracingObserver for observability (e.g., LangfuseTracer)

    Returns:
        The final output dict from the graph.
    """
    g = build_data_agent_graph(use_db=use_db, use_llm=use_llm)
    executor = g.compile(observer=tracer)
    import uuid
    initial_state = {"query": query, "__trace_id__": uuid.uuid4().hex}
    result = executor.run(initial_state)

    # Handle interrupt (clarification)
    if result.get("__interrupt__"):
        interrupt = result["__interrupt__"]
        clarification = result.get("clarification") or {}
        if not isinstance(clarification, dict):
            clarification = {}
        clarification = dict(clarification)
        clarification.setdefault("question", interrupt.get("question", "请选择分析口径"))
        clarification.setdefault("options", interrupt.get("options", []))
        clarification.setdefault("reason", "user clarification required")
        final_output = {
            "status": normalize_status("clarification_needed"),
            "interrupt": interrupt,
            "clarification": clarification,
            "state": result,
            "executor": executor,
            "analysis": result.get("analysis") or {},
        }
        from contracts import normalize_result
        return normalize_result(final_output, query=query, used_db=use_db, used_llm=use_llm)

    final_output = result.get("output", {"status": "error", "reason": "No output produced"})

    # Attach diagnosis report (for auditability)
    try:
        from diagnosis import diagnose_agent_output
        final_output["diagnosis"] = diagnose_agent_output(final_output, query).to_dict()
    except ImportError:
        final_output["diagnosis"] = None

    from contracts import normalize_result
    return normalize_result(final_output, query=query, used_db=use_db, used_llm=use_llm)


def resume_graph(state: dict, executor, user_choice: str = None) -> dict:
    """Resume a graph that was interrupted for clarification.

    Args:
        state: The state returned by run_graph (with __interrupt__)
        executor: The Executor instance from run_graph
        user_choice: User's choice id from the clarification options

    Returns:
        Final output dict.
    """
    if not user_choice:
        from contracts import normalize_result
        return normalize_result(
            {"status": "error", "reason": "No user choice provided"},
            query=state.get("query", ""), used_db=True, used_llm=False)

    # Apply the user choice to the state
    resume_payload = {"choice_id": user_choice}

    # If the user chose a breakdown, add channel dimension
    if user_choice == "breakdown":
        state["dimensions"] = ["channel"]
        state["intent"] = "breakdown"
        state["clarification"] = None  # Clear clarification now that we've resolved it
        # Also clear plan-level clarification so route doesn't re-add it
        if state.get("plan"):
            state["plan"]["clarification"] = None
            state["plan"]["intent"] = "breakdown"
    elif user_choice == "metric_query":
        state["dimensions"] = []
        state["intent"] = "metric_query"
        state["clarification"] = None
        if state.get("plan"):
            state["plan"]["clarification"] = None
            state["plan"]["intent"] = "metric_query"

    result = executor.resume(state, resume_payload)

    query = state.get("query", "")
    if result.get("__interrupt__"):
        interrupt = result["__interrupt__"]
        clarification = result.get("clarification") or {}
        if not isinstance(clarification, dict):
            clarification = {}
        clarification = dict(clarification)
        clarification.setdefault("question", interrupt.get("question", "请选择分析口径"))
        clarification.setdefault("options", interrupt.get("options", []))
        clarification.setdefault("reason", "user clarification required")
        final_output = {
            "status": normalize_status("clarification_needed"),
            "interrupt": interrupt,
            "clarification": clarification,
            "state": result,
            "executor": executor,
        }
        from contracts import normalize_result
        return normalize_result(final_output, query=query, used_db=True, used_llm=False)

    final_output = result.get("output", {"status": "error", "reason": "No output produced"})

    # Attach diagnosis report (for auditability)
    try:
        from diagnosis import diagnose_agent_output
        final_output["diagnosis"] = diagnose_agent_output(final_output, query).to_dict()
    except ImportError:
        final_output["diagnosis"] = None

    from contracts import normalize_result
    return normalize_result(final_output, query=query, used_db=True, used_llm=False)


# ── CLI entry ──

def main():
    import argparse, sys, pprint as pp

    p = argparse.ArgumentParser(description="Data Agent (Nucleus DAG)")
    p.add_argument("--db", action="store_true", help="Use SQLite DB")
    p.add_argument("--llm", action="store_true", help="Use LLM routing")
    p.add_argument("--mermaid", action="store_true", help="Print Mermaid diagram of the graph")
    p.add_argument("--eval", type=str, help="Path to golden test YAML")
    p.add_argument("query", nargs="*")
    args = p.parse_args()

    if args.mermaid:
        g = build_data_agent_graph()
        print(g.compile().to_mermaid())
        return

    if args.eval:
        from mvp_agent import eval_cases as _eval
        sys.exit(_eval(Path(args.eval)))

    q = " ".join(args.query) or "昨天 GMV 是多少？"
    result = run_graph(q, use_db=args.db, use_llm=args.llm)

    pp = pp.PrettyPrinter(indent=2, width=120)

    if normalize_status(result.get("status")) == "need_clarification":
        print("⚠️  Clarification needed:")
        pp.pprint(result["interrupt"])
        print("\nUse resume_graph(state, executor, 'breakdown') or resume_graph(state, executor, 'metric_query')")
        return

    pp.pprint({k: v for k, v in result.items() if k not in ("trace", "results")})
    if "insight" in result and isinstance(result.get("insight"), dict):
        print("\n── INSIGHT ──")
        print(result["insight"].get("insight", ""))
        print(f"\n  Chart: {result['insight'].get('chart', {}).get('type', 'none')}")
    if result.get("results"):
        print("\n── RESULTS ──")
        pp.pprint(result["results"])


if __name__ == "__main__":
    main()
