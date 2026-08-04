# -*- coding: utf-8 -*-
"""Result-to-analysis strategies for task-specific product payloads."""
from __future__ import unicode_literals

from advanced_analysis import attribute_change, build_comparison, detect_anomalies
from cohort_analysis import analyze_cohort_events
from task_type_registry import get_task_type_registry
from task_types import ANOMALY, ATTRIBUTION, COMPARISON, DESCRIPTIVE, RETENTION, FORECAST
from strategy_evidence import assess_strategy_evidence, build_need_more_data_analysis


def _to_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _dimension_key(plan):
    dims = plan.get("dimensions") if isinstance(plan, dict) else []
    return dims[0] if dims else None


def _label(row, dimension):
    if not isinstance(row, dict):
        return "unknown"
    return row.get(dimension) or row.get("dimension") or row.get("label") or row.get("name") or "unknown"


def _time_value(row):
    if not isinstance(row, dict):
        return None
    return row.get("time") or row.get("date") or row.get("day") or row.get("dt") or row.get("period")


def _severity_from_z(z):
    z = abs(_to_float(z))
    if z >= 3:
        return "high"
    if z >= 2:
        return "medium"
    if z > 0:
        return "low"
    return "none"


class BaseAnalysisStrategy(object):
    name = DESCRIPTIVE

    def analyze(self, plan, execution_result):
        plan = plan or {}
        execution_result = execution_result or {}
        evidence_assessment = assess_strategy_evidence(self.name, plan, execution_result)
        if execution_result.get("status") not in (None, "ok") or evidence_assessment.get("row_count", 0) == 0:
            insufficient = build_need_more_data_analysis(self.name, plan, execution_result, evidence_assessment)
            quality = (execution_result.get("diagnostics") or {}).get("quality") or {}
            if quality.get("empty_result"):
                insufficient["status"] = "insufficient_data"
            return insufficient
        rows = execution_result.get("results") or execution_result.get("rows") or []
        quality = (execution_result.get("diagnostics") or {}).get("quality") or {}
        result = {
            "type": self.name,
            "status": "insufficient_data" if quality.get("empty_result") else "ok",
            "definition": self.definition(plan),
            "data_quality": self.data_quality(rows, quality),
            "items": list(rows),
            "summary_facts": {"row_count": len(rows)},
            "evidence_assessment": evidence_assessment,
        }
        self._attach_gmv_driver(plan, rows, result)
        return result

    def definition(self, plan):
        return {
            "task_type": self.name,
            "metric": plan.get("metric") or "gmv",
            "dimensions": list(plan.get("dimensions") or []),
            "time_range": plan.get("time_range"),
        }

    def data_quality(self, rows, quality):
        return {"row_count": len(rows or []), "empty_result": bool(quality.get("empty_result", not rows)), "status": quality.get("status", "unknown"), "messages": list(quality.get("messages") or [])}

    def _attach_gmv_driver(self, plan, rows, result):
        metric = plan.get("metric") if isinstance(plan, dict) else None
        if metric != "gmv" or self.name not in (DESCRIPTIVE, COMPARISON):
            return
        try:
            from gmv_driver_analysis import gmv_driver_decomposition, gmv_driver_report
            gmv_val = order_val = None
            for row in rows:
                if isinstance(row, dict):
                    if gmv_val is None and row.get("gmv") is not None:
                        gmv_val = float(row["gmv"])
                    if order_val is None and row.get("order_count") is not None:
                        order_val = float(row["order_count"])
            if gmv_val is not None and order_val is not None:
                decomp = gmv_driver_decomposition(gmv_val, order_val)
                result["gmv_driver"] = decomp
                result["gmv_driver_report"] = gmv_driver_report(decomp)
        except Exception:
            result.setdefault("diagnostics", {})["gmv_driver_error"] = "suppressed"


class ComparisonAnalysisStrategy(BaseAnalysisStrategy):
    name = COMPARISON

    def analyze(self, plan, execution_result):
        base = BaseAnalysisStrategy.analyze(self, plan, execution_result)
        if base.get("status") == "need_more_data":
            return base
        metric = plan.get("metric") or "gmv"
        rows = execution_result.get("results") or execution_result.get("rows") or []
        comparison = build_comparison(rows, metric=metric)
        current, previous = self._period_values(rows, metric)
        if current is None:
            current = comparison.get("current")
        if previous is None:
            previous = comparison.get("previous")
        delta = None if current is None or previous is None else current - previous
        delta_pct = None if previous in (None, 0) or delta is None else delta / previous
        dim_changes = self._dimension_changes(rows, metric, _dimension_key(plan))
        top_inc = dim_changes[0] if dim_changes else None
        top_dec = sorted(dim_changes, key=lambda x: x.get("delta", 0))[0] if dim_changes else None
        direction = "flat" if not delta else ("increase" if delta > 0 else "decrease")
        base.update({"status": "ok" if delta is not None or dim_changes else comparison.get("status", base["status"]), "comparison": comparison, "current_value": current, "previous_value": previous, "delta": delta, "delta_pct": delta_pct, "direction": direction, "dimension_changes": dim_changes, "top_increase": top_inc, "top_decrease": top_dec})
        base["items"] = dim_changes or list(comparison.get("items") or [])
        base["summary_facts"].update({"current": current, "previous": previous, "current_value": current, "previous_value": previous, "delta": delta, "delta_pct": delta_pct, "direction": direction, "top_increase": top_inc, "top_decrease": top_dec})
        base["definition"]["previous_time_range"] = plan.get("previous_time_range") or plan.get("compare_time_range")
        self._attach_gmv_change(rows, base)
        return base

    def _period_values(self, rows, metric):
        current = previous = None
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            period = row.get("period") or row.get("window") or row.get("time_role")
            if row.get("current_value") is not None:
                current = _to_float(row.get("current_value"))
            if row.get("previous_value") is not None:
                previous = _to_float(row.get("previous_value"))
            if period in ("current", "this", "本期"):
                current = _to_float(row.get(metric))
            elif period in ("previous", "last", "上期"):
                previous = _to_float(row.get(metric))
        return current, previous

    def _dimension_changes(self, rows, metric, dimension):
        items = []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            if row.get("current") is not None or row.get("previous") is not None:
                cur = _to_float(row.get("current", row.get("current_value", row.get(metric))))
                prev = _to_float(row.get("previous", row.get("previous_value", 0)))
                delta = cur - prev
                items.append({"dimension": _label(row, dimension), "current_value": cur, "previous_value": prev, "delta": delta, "delta_pct": None if prev == 0 else delta / prev, "direction": "increase" if delta > 0 else ("decrease" if delta < 0 else "flat")})
        items.sort(key=lambda x: x["delta"], reverse=True)
        return items

    def _attach_gmv_change(self, rows, result):
        try:
            from gmv_driver_analysis import gmv_driver_change, gmv_driver_report
            cur_gmv = cur_orders = prev_gmv = prev_orders = None
            for row in rows or []:
                if not isinstance(row, dict):
                    continue
                period = row.get("period") or row.get("time_role")
                if period in ("current", "this", "本期"):
                    cur_gmv, cur_orders = row.get("gmv"), row.get("order_count")
                elif period in ("previous", "last", "上期"):
                    prev_gmv, prev_orders = row.get("gmv"), row.get("order_count")
            if None not in (cur_gmv, cur_orders, prev_gmv, prev_orders):
                driver = gmv_driver_change(cur_gmv, cur_orders, prev_gmv, prev_orders)
                result["gmv_driver_change"] = driver
                result["gmv_driver_report"] = gmv_driver_report(driver)
        except Exception:
            pass


class AnomalyAnalysisStrategy(BaseAnalysisStrategy):
    name = ANOMALY

    def analyze(self, plan, execution_result):
        base = BaseAnalysisStrategy.analyze(self, plan, execution_result)
        if base.get("status") == "need_more_data":
            if ((execution_result.get("diagnostics") or {}).get("quality") or {}).get("empty_result"):
                base["status"] = "insufficient_data"
            return base
        evidence_assessment = assess_strategy_evidence(self.name, plan or {}, execution_result or {})
        if any(str(x).startswith("insufficient_rows") or x == "history_window_missing" for x in evidence_assessment.get("reasons") or []):
            return build_need_more_data_analysis(self.name, plan, execution_result, evidence_assessment)
        metric = plan.get("metric") or "gmv"
        rows = execution_result.get("results") or execution_result.get("rows") or []
        threshold = (plan.get("analysis_config") or {}).get("anomaly_threshold", 2.0)
        raw = detect_anomalies(rows, metric=metric, threshold=threshold)
        anomalies = []
        if not raw.get("items") and len(rows) >= 4:
            hist = [_to_float(r.get(metric)) for r in rows[:-1] if isinstance(r, dict)]
            cur_row = rows[-1] if isinstance(rows[-1], dict) else {}
            cur_val = _to_float(cur_row.get(metric))
            mean = sum(hist) / float(len(hist)) if hist else 0.0
            variance = sum((v - mean) ** 2 for v in hist) / float(len(hist)) if hist else 0.0
            std = variance ** 0.5
            z = 0.0 if std == 0 else (cur_val - mean) / std
            prev = hist[-1] if hist else 0.0
            mom = None if prev == 0 else (cur_val - prev) / prev
            if abs(z) >= threshold or (mom is not None and abs(mom) >= 0.3):
                item = dict(cur_row)
                item["z_score"] = z
                item["anomaly_direction"] = "high" if cur_val >= mean else "low"
                raw.setdefault("items", []).append(item)
                raw.setdefault("mean", mean)
                raw.setdefault("std", std)
        for item in raw.get("items") or []:
            z = item.get("z_score")
            sev = _severity_from_z(z)
            direction = item.get("anomaly_direction") or ("high" if _to_float(z) > 0 else "low")
            anomalies.append({"time": _time_value(item), "metric": metric, "value": _to_float(item.get(metric)), "severity": sev, "z_score": z, "direction": direction, "reason": u"%s 指标出现%s异常，z_score=%.2f" % (metric, u"高位" if direction == "high" else u"低位", _to_float(z)), "suggested_dimensions": ["channel", "category", "region"]})
        sev_rank = {"none": 0, "low": 1, "medium": 2, "high": 3}
        max_sev = "none"
        for a in anomalies:
            if sev_rank.get(a["severity"], 0) > sev_rank.get(max_sev, 0):
                max_sev = a["severity"]
        causes = [u"建议按渠道、品类、地区下钻排查流量、供给或活动变化。"] if anomalies else []
        drill = [u"按 channel 下钻", u"按 category 下钻", u"查看异常日前后明细"] if anomalies else [u"继续观察后续周期。"]
        base.update({"status": raw.get("status", base["status"]), "anomalies": anomalies, "items": anomalies, "severity_summary": {"max_severity": max_sev, "anomaly_count": len(anomalies)}, "possible_causes": causes, "drill_down_suggestions": drill})
        base["summary_facts"].update({"mean": raw.get("mean"), "std": raw.get("std"), "threshold": raw.get("threshold"), "anomaly_count": len(anomalies), "max_severity": max_sev, "latest_anomaly_time": anomalies[-1].get("time") if anomalies else None})
        base["definition"]["time_dimension"] = plan.get("time_dimension") or "day"
        return base


class AttributionAnalysisStrategy(BaseAnalysisStrategy):
    name = ATTRIBUTION

    def analyze(self, plan, execution_result):
        base = BaseAnalysisStrategy.analyze(self, plan, execution_result)
        if base.get("status") == "need_more_data":
            return base
        metric = plan.get("metric") or "gmv"
        dimension = _dimension_key(plan) or plan.get("attribution_dimension") or "channel"
        rows = execution_result.get("results") or execution_result.get("rows") or []
        attribution = attribute_change(rows, metric=metric, dimension=dimension)
        try:
            from contribution_analysis import contribution_breakdown, pareto_analysis, top_n_drivers
            pareto = pareto_analysis(rows, metric=metric, dimension=dimension)
            breakdown = contribution_breakdown(rows, metric=metric, dimension=dimension)
            drivers = top_n_drivers(rows, metric=metric, dimension=dimension).get("top_drivers") or attribution.get("top_drivers") or []
        except Exception:
            pareto, breakdown, drivers = {}, {}, attribution.get("top_drivers") or []
        primary = drivers[0] if drivers else (attribution.get("top_drivers") or [None])[0]
        primary_pct = primary.get("pct") if isinstance(primary, dict) else None
        if primary_pct is None and isinstance(primary, dict):
            primary_pct = primary.get("contribution_pct") or primary.get("share_pct")
        base.update({"status": attribution.get("status", base["status"]), "top_drivers": list(drivers), "contribution": breakdown, "pareto": pareto, "attribution": attribution, "primary_driver": primary, "primary_driver_pct": primary_pct})
        base["items"] = list(drivers)
        base["summary_facts"].update({"dimension": dimension, "total_delta": attribution.get("total_delta"), "driver_count": len(drivers), "primary_driver": primary.get("dimension") if isinstance(primary, dict) else None, "primary_driver_pct": primary_pct})
        base["definition"]["attribution_dimension"] = dimension
        return base


class RetentionAnalysisStrategy(BaseAnalysisStrategy):
    name = RETENTION

    def analyze(self, plan, execution_result):
        plan = plan or {}
        execution_result = execution_result or {}
        evidence_assessment = assess_strategy_evidence(self.name, plan, execution_result)
        if execution_result.get("status") not in (None, "ok") or evidence_assessment.get("row_count", 0) == 0:
            return build_need_more_data_analysis(self.name, plan, execution_result, evidence_assessment)
        definition = plan.get("cohort_definition") or {}
        rows = execution_result.get("results") or execution_result.get("rows") or []
        # Execution may already return aggregate matrix.  Raw events are accepted
        # only at this internal boundary and never copied to the product output.
        if rows and isinstance(rows[0], dict) and rows[0].get("event_name"):
            result = analyze_cohort_events(rows, definition, plan.get("retention_horizons"), plan.get("tenant_id"))
        else:
            matrix = [dict(x) for x in rows if isinstance(x, dict)]
            sample = sum((x.get("cohort_size") or 0) for x in matrix if x.get("period") == ((plan.get("retention_horizons") or [None])[0]))
            result = {"status": "ok" if matrix else "insufficient_data", "definition": definition,
                      "matrix": matrix, "items": matrix,
                      "summary_facts": {"row_count": len(matrix), "sample_size": sample,
                                        "cohort_count": len(set([x.get("cohort") for x in matrix])),
                                        "horizons": plan.get("retention_horizons") or definition.get("retention_horizons") or []},
                      "caveats": list(definition.get("caveats") or [])}
        result["type"] = RETENTION
        result.setdefault("data_quality", self.data_quality(result.get("matrix"), (execution_result.get("diagnostics") or {}).get("quality") or {}))
        result.setdefault("definition", definition)
        result.setdefault("evidence_assessment", evidence_assessment)
        return result


class ForecastAnalysisStrategy(BaseAnalysisStrategy):
    name = FORECAST

    def analyze(self, plan, execution_result):
        plan = plan or {}
        execution_result = execution_result or {}
        diagnostics = execution_result.get("diagnostics") or {}
        defn = diagnostics.get("forecast_definition") or {}
        points = execution_result.get("results") or []
        backtest = diagnostics.get("backtest") or {}
        status = execution_result.get("status") or "ok"
        evidence_assessment = assess_strategy_evidence(self.name, plan, execution_result)
        if status != "ok" or evidence_assessment.get("row_count", 0) == 0:
            return build_need_more_data_analysis(self.name, plan, execution_result, evidence_assessment)
        return {
            "type": FORECAST,
            "status": status,
            "definition": {"task_type": FORECAST, "metric": plan.get("metric") or execution_result.get("metric"), "granularity": defn.get("granularity"), "algorithm": diagnostics.get("method"), "method_version": diagnostics.get("method_version"), "forecast_horizon_upper_bound": defn.get("forecast_horizon_upper_bound")},
            "data_quality": self.data_quality(points, (execution_result.get("diagnostics") or {}).get("quality") or {}),
            "items": list(points),
            "baseline": execution_result.get("baseline"),
            "backtest": backtest,
            "summary_facts": {"forecast_point_count": len(points), "method": diagnostics.get("method"), "training_window": diagnostics.get("training_window"), "backtest_available": backtest.get("available", False), "backtest_mape": backtest.get("mape"), "backtest_passed": backtest.get("passed")},
            "caveats": list(execution_result.get("caveats") or []),
            "evidence_assessment": evidence_assessment,
        }


class AnalysisStrategyRegistry(object):
    def __init__(self):
        self._strategies = {}
        self.register(BaseAnalysisStrategy())
        self.register(ComparisonAnalysisStrategy())
        self.register(AnomalyAnalysisStrategy())
        self.register(AttributionAnalysisStrategy())
        self.register(RetentionAnalysisStrategy())
        self.register(ForecastAnalysisStrategy())

    def register(self, strategy):
        if not isinstance(strategy, BaseAnalysisStrategy):
            raise TypeError("strategy must be a BaseAnalysisStrategy")
        self._strategies[strategy.name] = strategy
        return strategy

    def get(self, task_type):
        return self._strategies.get(task_type) or self._strategies[DESCRIPTIVE]

    def names(self):
        return sorted(self._strategies.keys())

    def analyze(self, plan, execution_result):
        task_type = (plan or {}).get("task_type") or DESCRIPTIVE
        analyzer_name = get_task_type_registry().get_analyzer(task_type)
        return self.get(analyzer_name).analyze(plan, execution_result)


_DEFAULT_REGISTRY = AnalysisStrategyRegistry()


def get_analysis_strategy_registry():
    return _DEFAULT_REGISTRY


def analyze_execution_result(plan, execution_result):
    return _DEFAULT_REGISTRY.analyze(plan, execution_result)


__all__ = ["BaseAnalysisStrategy", "ComparisonAnalysisStrategy", "AnomalyAnalysisStrategy", "AttributionAnalysisStrategy", "RetentionAnalysisStrategy", "ForecastAnalysisStrategy", "AnalysisStrategyRegistry", "get_analysis_strategy_registry", "analyze_execution_result"]
