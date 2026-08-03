# -*- coding: utf-8 -*-
"""Result Merger: merge multiple ExecutionResult into one.

This module provides strategies for merging sub-results after task decomposition.
Python 2.7 compatible.
"""


class MergeResult(object):
    """Result of merging multiple sub-results."""

    def __init__(self, merged=None, strategy="concat", sub_results=None,
                 diagnostics=None):
        self.merged = merged or {}
        self.strategy = strategy
        self.sub_results = sub_results or []
        self.diagnostics = diagnostics or {}

    def to_dict(self):
        return {
            "merged": dict(self.merged),
            "strategy": self.strategy,
            "sub_results": list(self.sub_results),
            "diagnostics": dict(self.diagnostics),
        }


class ResultMerger(object):
    """Merge multiple ExecutionResult into one.

    Strategies:
        - concat: simple concatenation (multi-metric flat)
        - group: group by dimension (multi-dimension breakdown)
        - nest: nested structure (overview + detail)
    """

    def __init__(self, strategy="auto"):
        self.strategy = strategy

    def merge(self, results, original_plan=None):
        """Merge sub-results into a single result.

        Args:
            results: list of ExecutionResult dicts
            original_plan: original AnalysisPlan dict (for strategy selection)

        Returns:
            MergeResult
        """
        if not results:
            return MergeResult(
                merged={},
                strategy="noop",
                sub_results=[],
                diagnostics={"error": "no_results"},
            )

        if len(results) == 1:
            return MergeResult(
                merged=results[0],
                strategy="noop",
                sub_results=results,
                diagnostics={"count": 1},
            )

        # Auto-select strategy based on original plan
        strategy = self.strategy
        if strategy == "auto":
            strategy = self._select_strategy(results, original_plan)

        if strategy == "nest":
            return self._merge_nest(results, original_plan)
        elif strategy == "group":
            return self._merge_group(results, original_plan)
        else:
            return self._merge_concat(results, original_plan)

    def _select_strategy(self, results, original_plan):
        """Auto-select merge strategy based on plan and results."""
        pd = original_plan or {}
        decompose_reason = pd.get("decompose_reason", "")

        if "overview_expansion" in decompose_reason:
            return "nest"
        if "multi_dimension" in decompose_reason:
            return "group"
        return "concat"

    def _merge_concat(self, results, original_plan):
        """Concat strategy: simple concatenation of results."""
        merged = dict(results[0])
        all_rows = []
        all_errors = []
        all_sqls = []
        status = "ok"

        for r in results:
            rows = r.get("results") or r.get("execution", {}).get("rows") or []
            if isinstance(rows, list):
                all_rows.extend(rows)
            errors = r.get("errors") or []
            if errors:
                all_errors.extend(errors)
            sql = r.get("sql")
            if sql:
                all_sqls.append(sql)
            if r.get("status") != "ok":
                status = r.get("status", "error")

        merged["results"] = all_rows
        merged["sql"] = all_sqls[0] if len(all_sqls) == 1 else all_sqls
        merged["errors"] = all_errors
        merged["status"] = status
        merged["multi_result"] = True
        merged["sub_result_count"] = len(results)

        # Merge diagnostics
        diag = dict(merged.get("diagnostics") or {})
        diag["merge_strategy"] = "concat"
        diag["merged_from"] = [r.get("task_id") for r in results if r.get("task_id")]
        merged["diagnostics"] = diag

        return MergeResult(
            merged=merged,
            strategy="concat",
            sub_results=results,
            diagnostics={
                "total_rows": len(all_rows),
                "total_errors": len(all_errors),
                "sub_result_count": len(results),
            },
        )

    def _merge_group(self, results, original_plan):
        """Group strategy: group results by dimension."""
        merged = dict(results[0])
        grouped = {}
        all_errors = []
        status = "ok"

        for r in results:
            dims = r.get("dimensions") or []
            dim_key = "_".join(dims) if dims else "overall"
            if dim_key not in grouped:
                grouped[dim_key] = {
                    "dimensions": dims,
                    "results": [],
                    "metric": r.get("metric"),
                }
            rows = r.get("results") or r.get("execution", {}).get("rows") or []
            if isinstance(rows, list):
                grouped[dim_key]["results"].extend(rows)
            errors = r.get("errors") or []
            if errors:
                all_errors.extend(errors)
            if r.get("status") != "ok":
                status = r.get("status", "error")

        merged["grouped_results"] = grouped
        merged["errors"] = all_errors
        merged["status"] = status
        merged["multi_result"] = True
        merged["sub_result_count"] = len(results)

        diag = dict(merged.get("diagnostics") or {})
        diag["merge_strategy"] = "group"
        diag["merged_from"] = [r.get("task_id") for r in results if r.get("task_id")]
        merged["diagnostics"] = diag

        return MergeResult(
            merged=merged,
            strategy="group",
            sub_results=results,
            diagnostics={
                "groups": len(grouped),
                "total_errors": len(all_errors),
                "sub_result_count": len(results),
            },
        )

    def _merge_nest(self, results, original_plan):
        """Nest strategy: overview + detail structure."""
        merged = dict(results[0]) if results else {}
        overview = None
        details = []

        for r in results:
            intent = r.get("intent", "")
            if intent == "metric_query":
                overview = r
            else:
                details.append(r)

        merged["overview"] = overview
        merged["details"] = details
        merged["multi_result"] = True
        merged["sub_result_count"] = len(results)

        # Use overview status as primary
        if overview:
            merged["status"] = overview.get("status", "ok")
            merged["results"] = overview.get("results") or overview.get("execution", {}).get("rows") or []
            merged["sql"] = overview.get("sql")
        elif details:
            merged["status"] = details[0].get("status", "ok")

        diag = dict(merged.get("diagnostics") or {})
        diag["merge_strategy"] = "nest"
        diag["overview_task_id"] = overview.get("task_id") if overview else None
        diag["detail_count"] = len(details)
        diag["merged_from"] = [r.get("task_id") for r in results if r.get("task_id")]
        merged["diagnostics"] = diag

        return MergeResult(
            merged=merged,
            strategy="nest",
            sub_results=results,
            diagnostics={
                "has_overview": overview is not None,
                "detail_count": len(details),
                "sub_result_count": len(results),
            },
        )


__all__ = ["MergeResult", "ResultMerger"]
