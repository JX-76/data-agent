"""Analysis layer: Pandas-based data analysis for the Data Agent.

Takes raw SQL execution results and produces structured insights:
- Descriptive statistics
- Trend detection
- TopN / ranking
- Anomaly detection
- Distribution analysis
- Insight extraction suitable for NL generation
"""


def analyze(results: list[dict], dimensions: list[str] | None = None, metric: str = "") -> dict:
    """Analyze query results and return structured insights.

    Args:
        results: List of dict rows from SQL execution (e.g. [{"channel":"online","gmv":568}, ...])
        dimensions: Dimension columns present in results (e.g. ["channel"])
        metric: Primary metric name (e.g. "gmv")

    Returns:
        Dict with keys: summary, trends, top_n, anomolies, distribution, recommendations
    """
    if not results:
        return _empty_analysis()

    columns = list(results[0].keys())
    dims = dimensions or _detect_dims(columns, metric)
    metrics = [c for c in columns if c not in dims or c == metric]

    return {
        "summary": _describe(results, dims, metrics),
        "trends": _detect_trends(results, dims, metrics),
        "top_n": _extract_topn(results, dims, metrics),
        "anomalies": _detect_anomalies(results, dims, metrics),
        "distribution": _distribution_shape(results, dims, metrics),
        "recommendations": _suggest_followups(results, dims, metrics),
    }


def _empty_analysis():
    return {"summary": "查询结果为空", "trends": None, "top_n": None, "anomalies": None, "distribution": None, "recommendations": None}


def _detect_dims(columns, metric):
    """Heuristic: columns that are not metric value columns are dimensions."""
    return [c for c in columns if c != metric and c not in ("row_count",)]


def _describe(results, dims, metrics):
    """Generate descriptive statistics."""
    if not results:
        return {}
    desc = {"row_count": len(results)}

    for m in metrics:
        values = [r[m] for r in results if r.get(m) is not None]
        if not values:
            continue
        try:
            numeric_vals = [float(v) for v in values]
            desc[m] = {
                "total": round(sum(numeric_vals), 2),
                "mean": round(sum(numeric_vals) / len(numeric_vals), 2),
                "min": round(min(numeric_vals), 2),
                "max": round(max(numeric_vals), 2),
                "p50": round(_percentile(numeric_vals, 0.5), 2),
            }
            if len(numeric_vals) > 1:
                desc[m]["range"] = round(max(numeric_vals) - min(numeric_vals), 2)
        except (ValueError, TypeError):
            desc[m] = {"type": "non_numeric", "unique_values": len(set(str(v) for v in values))}

    return desc


def _detect_trends(results, dims, metrics):
    """Detect monotonic trends when results contain a time dimension."""
    trends = {}
    # Only do trend detection for date-like dimensions
    date_dim = None
    for d in dims:
        if d in ("date",) or "date" in d.lower() or "time" in d.lower():
            date_dim = d
            break
    if not date_dim or len(results) < 3:
        return trends

    sorted_results = sorted(results, key=lambda r: str(r.get(date_dim, "")))
    for m in metrics:
        values = []
        for r in sorted_results:
            try:
                values.append(float(r.get(m, 0)))
            except (ValueError, TypeError):
                break
        if len(values) < 3:
            continue

        # Simple trend: compare first 3 vs last 3
        first_3_avg = sum(values[:3]) / 3
        last_3_avg = sum(values[-3:]) / 3
        if last_3_avg > first_3_avg * 1.05:
            trends[m] = {"direction": "up", "change_pct": round((last_3_avg - first_3_avg) / first_3_avg * 100, 1)}
        elif last_3_avg < first_3_avg * 0.95:
            trends[m] = {"direction": "down", "change_pct": round((first_3_avg - last_3_avg) / first_3_avg * 100, 1)}
        else:
            trends[m] = {"direction": "stable", "change_pct": round(abs(last_3_avg - first_3_avg) / first_3_avg * 100, 1)}

    return trends


def _extract_topn(results, dims, metrics, n=5):
    """Extract top and bottom N by each metric."""
    topn = {}
    for m in metrics:
        try:
            sorted_rows = sorted(
                [r for r in results if r.get(m) is not None],
                key=lambda r: float(r[m]), reverse=True
            )
            top_n = sorted_rows[:n]
            bottom_n = sorted_rows[-n:] if len(sorted_rows) > n else []
            topn[m] = {
                "top": [{_dim_labels(r, dims): r[m]} for r in top_n],
                "bottom": [{_dim_labels(r, dims): r[m]} for r in bottom_n],
            }
        except (ValueError, TypeError):
            continue
    return topn


def _dim_labels(row, dims):
    """Create a readable label from dimension values."""
    parts = []
    for d in dims:
        if d in row:
            parts.append(str(row[d]))
    return " / ".join(parts) if parts else "total"


def _detect_anomalies(results, dims, metrics):
    """Simple IQR-based anomaly detection."""
    anomalies = {}
    for m in metrics:
        try:
            values = sorted(float(r[m]) for r in results if r.get(m) is not None)
        except (ValueError, TypeError):
            continue
        if len(values) < 4:
            continue
        q1 = _percentile(values, 0.25)
        q3 = _percentile(values, 0.75)
        iqr = q3 - q1
        if iqr <= 0:
            continue
        lower = q1 - 1.5 * iqr
        upper = q3 + 1.5 * iqr
        outlier_rows = []
        for r in results:
            try:
                v = float(r.get(m, 0))
            except (ValueError, TypeError):
                continue
            if v < lower or v > upper:
                outlier_rows.append({_dim_labels(r, dims): v})
        if outlier_rows:
            anomalies[m] = {"method": "IQR", "bounds": {"q1": q1, "q3": q3, "lower": lower, "upper": upper}, "outliers": outlier_rows}
    return anomalies


def _distribution_shape(results, dims, metrics):
    """Characterise distribution shape."""
    dist = {}
    for m in metrics:
        try:
            values = sorted(float(r[m]) for r in results if r.get(m) is not None)
        except (ValueError, TypeError):
            continue
        if len(values) < 4:
            continue
        mean_val = sum(values) / len(values)
        p50 = _percentile(values, 0.5)
        # Skew indicator
        if mean_val > p50 * 1.1:
            skew = "right_skewed"
        elif mean_val < p50 * 0.9:
            skew = "left_skewed"
        else:
            skew = "approximately_symmetric"

        # Concentration: what % of total do top 3 account for?
        total = sum(values)
        top3_share = sum(values[-3:]) / total * 100 if total > 0 else 0

        dist[m] = {
            "skew": skew,
            "top3_concentration_pct": round(top3_share, 1),
            "cv": round(_std(values) / mean_val, 2) if mean_val != 0 else 0,
        }
    return dist


def _suggest_followups(results, dims, metrics):
    """Suggest follow-up drill-down questions based on analysis results."""
    suggestions = []
    if not results:
        return suggestions

    # If there are dimensions, suggest drilling into the top one
    if dims:
        for m in metrics:
            try:
                sorted_rows = sorted([r for r in results if r.get(m) is not None], key=lambda r: float(r[m]), reverse=True)
                if sorted_rows:
                    top_label = _dim_labels(sorted_rows[0], dims)
                    suggestions.append(f"钻取分析：{top_label} 的 {m} 最高，需要看它的时间趋势吗？")
            except (ValueError, TypeError):
                pass

    # If no dimensions, suggest breakdown
    if not dims:
        suggestions.append("按渠道拆分查看各渠道贡献")
        suggestions.append("查看最近7天趋势")
    elif len(dims) == 1:
        # Suggest a second dimension
        other_dims = [d for d in ["channel", "region", "category"] if d not in dims]
        if other_dims:
            suggestions.append(f"按 {other_dims[0]} 进一步拆分")

    return suggestions[:3]


def _percentile(sorted_values, p):
    """Compute p-th percentile from sorted list."""
    if not sorted_values:
        return 0
    k = (len(sorted_values) - 1) * p
    f = int(k)
    c = k - f
    if f + 1 < len(sorted_values):
        return sorted_values[f] + c * (sorted_values[f + 1] - sorted_values[f])
    return sorted_values[f]


def _std(values):
    """Sample standard deviation."""
    if len(values) < 2:
        return 0
    mean = sum(values) / len(values)
    variance = sum((x - mean) ** 2 for x in values) / (len(values) - 1)
    return variance ** 0.5


# ── P2-5: 异动归因集成 ──

def analyze_root_cause(current: list[dict], baseline: list[dict],
                       metric: str = "gmv", dimensions: list[str] | None = None) -> dict:
    """对两期数据进行异动归因分析。
    
    Args:
        current: 当前期数据
        baseline: 基准期数据
        metric: 指标列名
        dimensions: 维度列名（默认自动检测）
        
    Returns:
        归因分析结果字典
    """
    try:
        from root_cause import RootCauseEngine
        engine = RootCauseEngine()
        
        if dimensions is None and current:
            dims = _detect_dims(list(current[0].keys()), metric)
        else:
            dims = dimensions or []
        
        return engine.compare_two_periods(current, baseline, metric, dims)
    except ImportError:
        return {"error": "root_cause module not available"}
    except Exception as e:
        return {"error": f"Root cause analysis failed: {e}"}


def analyze_statistical(current: list[dict], baseline: list[dict],
                        metric: str = "gmv") -> dict:
    """对两期数据进行统计显著性检验。
    
    P2-7: 自动执行 t 检验 + 效应量
    
    Args:
        current: 当前期数据
        baseline: 基准期数据
        metric: 指标列名
        
    Returns:
        t检验结果字典
    """
    try:
        from stat_tests import compare_metric
        
        cur_vals = [float(r.get(metric, 0) or 0) for r in current]
        base_vals = [float(r.get(metric, 0) or 0) for r in baseline]
        
        return compare_metric(cur_vals, base_vals, metric)
    except ImportError:
        return {"error": "stat_tests module not available"}
    except Exception as e:
        return {"error": f"Statistical test failed: {e}"}
