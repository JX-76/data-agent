# -*- coding: utf-8 -*-
"""Anomaly Detection: Statistical and business rule-based anomaly detection.

Detects anomalies in time series data and suggests root cause dimensions.

Python 2.7 compatible and deterministic.
"""

from __future__ import unicode_literals

import statistics


def _to_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


class AnomalyResult(dict):
    """Dict-compatible anomaly result with attribute access.

    Backwards compatible with both attribute-style access (result.is_anomaly)
    and dict-style access (result["is_anomaly"]). This keeps consumer contracts
    stable while preserving Python 2.7 compatibility.
    """

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name)

    def __setattr__(self, name, value):
        self[name] = value


def _make_anomaly_result(is_anomaly, severity, current_value, expected_range,
                         deviation_pct, z_score, mom_change_pct,
                         suggested_dimensions, reason):
    """Create an AnomalyResult (dict + attribute access) for stable contract."""
    return AnomalyResult({
        "is_anomaly": is_anomaly,
        "severity": severity,
        "current_value": current_value,
        "expected_range": expected_range,
        "deviation_pct": deviation_pct,
        "z_score": z_score,
        "mom_change_pct": mom_change_pct,
        "suggested_dimensions": suggested_dimensions,
        "reason": reason,
    })


def detect_anomaly(metric, current_value, history):
    """Module-level convenience for AnomalyDetector.detect."""
    return AnomalyDetector().detect(metric, current_value, history)



class AnomalyDetector(object):
    """Anomaly detector using statistical and business rules.

    Detection methods:
    1. Statistical: Z-score (>2sigma = anomaly, >3sigma = high)
    2. Business: MoM change (>30% = warning, >50% = high)
    3. Combined: Either statistical OR business rule triggers
    """

    # Thresholds
    Z_SCORE_WARNING = 2.0
    Z_SCORE_HIGH = 3.0
    MOM_WARNING = 0.30  # 30%
    MOM_HIGH = 0.50     # 50%

    def __init__(self, history_periods=30):
        self.history_periods = history_periods

    def detect(self, metric, current_value, history):
        """Detect anomaly in current value against history.

        Args:
            metric: Metric name
            current_value: Current period value
            history: Historical values (oldest first)

        Returns:
            Dict with anomaly detection details
        """
        current_value = _to_float(current_value)
        history = [_to_float(h) for h in (history or [])]

        if not history or len(history) < 3:
            return _make_anomaly_result(
                is_anomaly=False,
                severity="none",
                current_value=current_value,
                expected_range=(current_value, current_value),
                deviation_pct=0.0,
                z_score=0.0,
                mom_change_pct=0.0,
                suggested_dimensions=[],
                reason=u"历史数据不足，无法检测异常"
            )

        # Statistical analysis
        mean = statistics.mean(history)
        std = statistics.stdev(history) if len(history) > 1 else 0
        z_score = (current_value - mean) / std if std > 0 else 0

        # Business rule: MoM change
        mom_change = (current_value - history[-1]) / history[-1] if history[-1] != 0 else 0

        # Determine severity
        severity = "none"
        is_anomaly = False
        reason = ""

        # Statistical rule
        if abs(z_score) > self.Z_SCORE_HIGH:
            severity = "high"
            is_anomaly = True
            reason = u"统计异常: Z-score=%.2f (>%.1f)" % (z_score, self.Z_SCORE_HIGH)
        elif abs(z_score) > self.Z_SCORE_WARNING:
            severity = "medium"
            is_anomaly = True
            reason = u"统计警告: Z-score=%.2f (>%.1f)" % (z_score, self.Z_SCORE_WARNING)

        # Business rule
        if abs(mom_change) > self.MOM_HIGH:
            if severity == "high":
                reason += u", 业务异常: 环比变化%+.1f%% (>%.0f%%)" % (mom_change * 100, self.MOM_HIGH * 100)
            else:
                severity = "high"
                is_anomaly = True
                reason = u"业务异常: 环比变化%+.1f%% (>%.0f%%)" % (mom_change * 100, self.MOM_HIGH * 100)
        elif abs(mom_change) > self.MOM_WARNING:
            if not is_anomaly:
                severity = "medium"
                is_anomaly = True
                reason = u"业务警告: 环比变化%+.1f%% (>%.0f%%)" % (mom_change * 100, self.MOM_WARNING * 100)

        if not is_anomaly:
            reason = u"未检测到异常"

        # Expected range (mean +/- 2sigma)
        expected_lower = mean - 2 * std
        expected_upper = mean + 2 * std

        # Deviation percentage
        deviation_pct = (current_value - mean) / mean * 100 if mean != 0 else 0

        # Suggest dimensions for root cause analysis
        suggested_dimensions = self._suggest_dimensions(metric, history)

        return _make_anomaly_result(
            is_anomaly=is_anomaly,
            severity=severity,
            current_value=current_value,
            expected_range=(expected_lower, expected_upper),
            deviation_pct=deviation_pct,
            z_score=z_score,
            mom_change_pct=mom_change * 100,
            suggested_dimensions=suggested_dimensions,
            reason=reason
        )

    def _suggest_dimensions(self, metric, history):
        """Suggest dimensions to drill down for root cause analysis.

        Priority:
        1. channel (most common business driver)
        2. region (geographic impact)
        3. category (product mix)
        4. time (hour/day patterns)
        """
        # Default priority for e-commerce
        dimensions = ["channel", "region", "category"]

        # Could be customized based on metric type
        if metric in ("retention_rate", "churn_rate"):
            dimensions = ["channel", "region", "user_segment"]
        elif metric in ("conversion_rate", "uv"):
            dimensions = ["channel", "landing_page", "campaign"]

        return dimensions

    def batch_detect(self, metric, values):
        """Detect anomalies for a series of values.

        Args:
            metric: Metric name
            values: Time series values (oldest first)

        Returns:
            List of anomaly result dicts for each value
        """
        results = []
        for i, value in enumerate(values):
            # Use all previous values as history
            history = values[:i]
            result = self.detect(metric, value, history)
            results.append(result)
        return results


class DimensionDrillDown(object):
    """Drill down analysis to find root cause across dimensions."""

    def analyze(self, metric, current_value, history, dimension_data):
        """Analyze which dimension contributes most to the anomaly.

        Args:
            metric: Metric name
            current_value: Current total value
            history: Historical total values
            dimension_data: Dict of dimension -> breakdown data
                e.g., {"channel": [{"channel": "online", "gmv": 5000}, ...]}

        Returns:
            Dict with dimension contributions and findings
        """
        findings = []

        for dimension, data in dimension_data.items():
            # Calculate contribution of each dimension value
            total = sum(item.get(metric, 0) for item in data)

            # Find biggest drop/growth
            changes = []
            for item in data:
                label = item.get(dimension, "unknown")
                value = item.get(metric, 0)
                # Compare with historical average for this dimension
                # (simplified: use current proportion)
                proportion = value / total if total > 0 else 0
                changes.append({
                    "label": label,
                    "value": value,
                    "proportion": proportion,
                    "impact": abs(value - total / len(data)),  # Deviation from average
                })

            # Sort by impact
            changes.sort(key=lambda x: x["impact"], reverse=True)

            if changes:
                top = changes[0]
                findings.append({
                    "dimension": dimension,
                    "biggest_driver": top["label"],
                    "driver_value": top["value"],
                    "driver_proportion": top["proportion"],
                    "impact_score": top["impact"],
                    "top_3": changes[:3]
                })

        # Sort findings by impact
        findings.sort(key=lambda x: x["impact_score"], reverse=True)

        return {
            "total_value": current_value,
            "dimension_count": len(dimension_data),
            "findings": findings,
            "primary_dimension": findings[0]["dimension"] if findings else None,
            "primary_driver": findings[0]["biggest_driver"] if findings else None
        }

    def generate_insights(self, analysis):
        """Generate natural language insights from drill-down analysis."""
        insights = []

        if not analysis.get("findings"):
            return [u"未找到明显的维度异常"]

        primary = analysis["findings"][0]
        dim = primary["dimension"]
        driver = primary["biggest_driver"]
        value = primary["driver_value"]
        proportion = primary["driver_proportion"]

        insights.append(
            u"主要影响因素: %s - %s 贡献 %.2f (%.1f%%)" % (
                dim, driver, value, proportion * 100
            )
        )

        # Check concentration
        if proportion > 0.5:
            insights.append(u"%s 高度集中，占比超过50%%" % driver)
        elif proportion < 0.2:
            insights.append(u"%s 占比较低，可能拖累整体表现" % driver)

        # Additional dimensions
        if len(analysis["findings"]) > 1:
            secondary = analysis["findings"][1]
            insights.append(
                u"次要因素: %s - %s" % (
                    secondary["dimension"],
                    secondary["biggest_driver"]
                )
            )

        return insights


# Convenience functions
def detect_anomaly(metric, current, history):
    """Quick anomaly detection."""
    detector = AnomalyDetector()
    return detector.detect(metric, current, history)


def drill_down(metric, current, history, dimension_data):
    """Quick drill-down analysis."""
    drill = DimensionDrillDown()
    return drill.analyze(metric, current, history, dimension_data)


__all__ = [
    "AnomalyDetector", "DimensionDrillDown",
    "detect_anomaly", "drill_down",
]
