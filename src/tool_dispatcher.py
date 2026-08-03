"""Unified tool dispatcher for Data Agent.

Centralizes tool execution so graph/reasoning loops don't need to carry
large tool-specific if/elif blocks.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple


@dataclass
class DispatchResult:
    """Normalized tool execution result."""

    ok: bool
    tool: str
    dataid: Optional[str] = None
    observation: str = ""
    sample_rows: Optional[dict] = None
    result: Optional[dict] = None
    error: Optional[str] = None
    final_sql: Optional[str] = None
    final_results: Optional[list[dict]] = None


class ToolDispatcher:
    """Dispatches tools against an AgentRuntime and optional DB backend."""

    def __init__(self, runtime, db=None):
        self.rt = runtime
        self.db = db

    def dispatch(
        self,
        tool: str,
        args: Dict[str, Any],
        current_dataids: list[str],
    ) -> DispatchResult:
        try:
            if tool == "catalog":
                return self._catalog()
            if tool == "switch":
                return self._switch(args, current_dataids)
            if tool == "preview":
                return self._preview(args, current_dataids)
            if tool == "filter":
                return self._filter(args, current_dataids)
            if tool == "aggregate":
                return self._aggregate(args, current_dataids)
            if tool == "sort":
                return self._sort(args, current_dataids)
            if tool == "top":
                return self._top(args, current_dataids)
            if tool == "filter_value":
                return self._filter_value(args, current_dataids)
            if tool == "merge":
                return self._merge(args, current_dataids)
            if tool == "compare_periods":
                return self._compare_periods(args, current_dataids)
            return DispatchResult(ok=False, tool=tool, error=f"Unknown tool: {tool}")
        except Exception as e:
            return DispatchResult(ok=False, tool=tool, error=str(e))

    def _catalog(self) -> DispatchResult:
        from config import SEMANTIC_SUMMARY as _SS

        cat = {"models": _SS["models"], "metrics": _SS["metrics"], "dimensions": _SS["dimensions"]}
        return DispatchResult(ok=True, tool="catalog", observation=f"Catalog: {json.dumps(cat, ensure_ascii=False)}", result=cat)

    def _switch(self, args: Dict[str, Any], current_dataids: list[str]) -> DispatchResult:
        model_id = args.get("model_id", args.get("model", "order_detail"))
        new_dataid = self.rt.switch(model_id, db=self.db)
        sample_rows = self.rt.trace[-1].get("sample_rows", {})
        current_dataids.append(new_dataid)
        return DispatchResult(ok=True, tool="switch", dataid=new_dataid, sample_rows=sample_rows)

    def _preview(self, args: Dict[str, Any], current_dataids: list[str]) -> DispatchResult:
        dataid = args.get("dataid", current_dataids[-1] if current_dataids else "d1")
        n = args.get("n", 5)
        new_dataid = self.rt.preview(dataid, n, db=self.db)
        sample_rows = self.rt.trace[-1].get("sample_rows", {})
        current_dataids.append(new_dataid)
        return DispatchResult(ok=True, tool="preview", dataid=new_dataid, sample_rows=sample_rows)

    def _filter(self, args: Dict[str, Any], current_dataids: list[str]) -> DispatchResult:
        dataid = args.get("dataid", current_dataids[-1] if current_dataids else "d1")
        metric_id = args.get("metric_id", args.get("metric", "gmv"))
        start = args.get("start_iso", args.get("start", ""))
        end = args.get("end_iso", args.get("end", ""))
        if start and end:
            start_dt = dt.datetime.fromisoformat(start)
            end_dt = dt.datetime.fromisoformat(end)
        else:
            now = dt.datetime.now()
            start_dt = (now - dt.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            end_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
        new_dataid = self.rt.filter_time_and_defaults(dataid, metric_id, start_dt, end_dt)
        sample_rows = self.rt.trace[-1].get("sample_rows", {})
        current_dataids.append(new_dataid)
        return DispatchResult(ok=True, tool="filter", dataid=new_dataid, sample_rows=sample_rows)

    def _aggregate(self, args: Dict[str, Any], current_dataids: list[str]) -> DispatchResult:
        dataid = args.get("dataid", current_dataids[-1] if current_dataids else "d2")
        metric_id = args.get("metric_id", args.get("metric", "gmv"))
        dims = args.get("dimensions", [])
        new_dataid = self.rt.aggregate(dataid, metric_id, dims)
        sample_rows = self.rt.trace[-1].get("sample_rows", {})
        current_dataids.append(new_dataid)
        return DispatchResult(ok=True, tool="aggregate", dataid=new_dataid, sample_rows=sample_rows)

    def _sort(self, args: Dict[str, Any], current_dataids: list[str]) -> DispatchResult:
        dataid = args.get("dataid", current_dataids[-1] if current_dataids else "d3")
        by = args.get("by", args.get("metric", "gmv"))
        order = args.get("order", "DESC")
        new_dataid = self.rt.sort(dataid, by, order)
        sample_rows = self.rt.trace[-1].get("sample_rows", {})
        current_dataids.append(new_dataid)
        return DispatchResult(ok=True, tool="sort", dataid=new_dataid, sample_rows=sample_rows)

    def _top(self, args: Dict[str, Any], current_dataids: list[str]) -> DispatchResult:
        dataid = args.get("dataid", current_dataids[-1] if current_dataids else "d3")
        by = args.get("by", args.get("metric", "gmv"))
        n = args.get("n", 5)
        order = args.get("order", "DESC")
        new_dataid = self.rt.top(dataid, by, n, order)
        sample_rows = self.rt.trace[-1].get("sample_rows", {})
        current_dataids.append(new_dataid)
        return DispatchResult(ok=True, tool="top", dataid=new_dataid, sample_rows=sample_rows)

    def _filter_value(self, args: Dict[str, Any], current_dataids: list[str]) -> DispatchResult:
        dataid = args.get("dataid", current_dataids[-1] if current_dataids else "d3")
        dimension = args.get("dimension", "channel")
        value = args.get("value", "")
        new_dataid = self.rt.filter_value(dataid, dimension, value)
        sample_rows = self.rt.trace[-1].get("sample_rows", {})
        current_dataids.append(new_dataid)
        return DispatchResult(ok=True, tool="filter_value", dataid=new_dataid, sample_rows=sample_rows)

    def _merge(self, args: Dict[str, Any], current_dataids: list[str]) -> DispatchResult:
        dataid_a = args.get("dataid_a", current_dataids[-2] if len(current_dataids) >= 2 else "d3")
        dataid_b = args.get("dataid_b", current_dataids[-1] if current_dataids else "d5")
        on = args.get("on", "channel")
        new_dataid = self.rt.merge(dataid_a, dataid_b, on)
        sample_rows = self.rt.trace[-1].get("sample_rows", {})
        current_dataids.append(new_dataid)
        return DispatchResult(ok=True, tool="merge", dataid=new_dataid, sample_rows=sample_rows)

    def _compare_periods(self, args: Dict[str, Any], current_dataids: list[str]) -> DispatchResult:
        dataid = args.get("dataid", current_dataids[-1] if current_dataids else "d1")
        metric_id = args.get("metric_id", args.get("metric", "gmv"))
        dims = args.get("dimensions", ["channel"])
        p1s = args.get("p1_start", args.get("period1_start", ""))
        p1e = args.get("p1_end", args.get("period1_end", ""))
        p2s = args.get("p2_start", args.get("period2_start", ""))
        p2e = args.get("p2_end", args.get("period2_end", ""))
        if not p1s:
            now = dt.datetime.now()
            p1s_dt = (now - dt.timedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0)
            p1e_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
            p2s_dt = (now - dt.timedelta(days=14)).replace(hour=0, minute=0, second=0, microsecond=0)
            p2e_dt = (now - dt.timedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0)
        else:
            p1s_dt = dt.datetime.fromisoformat(p1s)
            p1e_dt = dt.datetime.fromisoformat(p1e)
            p2s_dt = dt.datetime.fromisoformat(p2s)
            p2e_dt = dt.datetime.fromisoformat(p2e)
        new_dataid = self.rt.compare_periods(dataid, metric_id, p1s_dt, p1e_dt, p2s_dt, p2e_dt, dims)
        sample_rows = self.rt.trace[-1].get("sample_rows", {})
        current_dataids.append(new_dataid)
        return DispatchResult(ok=True, tool="compare_periods", dataid=new_dataid, sample_rows=sample_rows)
