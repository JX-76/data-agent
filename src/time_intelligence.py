"""Time Intelligence: YoY / MoM / trend calculations for the Data Agent.

Provides SQL templates and result post-processing for time-series analysis.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


@dataclass
class TimeConfig:
    """Parsed time configuration from natural language."""
    base_period: str  # "day", "week", "month", "quarter", "year"
    compare_type: str  # "yoy", "mom", "qoq", "wow", "custom"
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    periods: int = 30  # For trend analysis


class TimeIntelligence:
    """Time intelligence engine for Data Agent.
    
    Supports:
    - YoY (Year-over-Year): compare with same period last year
    - MoM (Month-over-Month): compare with previous period
    - Trend: time series over N periods
    - Cumulative: running total (YTD, QTD, MTD)
    """

    # SQL templates for different databases
    TEMPLATES = {
        "sqlite": {
            "yoy": """
WITH current_period AS (
    SELECT 
        strftime('{grain}', dt) as period,
        SUM({metric}) as current_val
    FROM fct_orders
    WHERE dt >= date('{start_date}') AND dt <= date('{end_date}')
    GROUP BY 1
),
last_year AS (
    SELECT 
        strftime('{grain}', dt) as period,
        SUM({metric}) as last_year_val
    FROM fct_orders
    WHERE dt >= date('{start_date}', '-1 year') AND dt <= date('{end_date}', '-1 year')
    GROUP BY 1
)
SELECT 
    c.period,
    c.current_val,
    l.last_year_val,
    CASE 
        WHEN l.last_year_val IS NULL OR l.last_year_val = 0 THEN NULL
        ELSE ROUND((c.current_val - l.last_year_val) * 100.0 / l.last_year_val, 2)
    END as yoy_rate_pct,
    CASE 
        WHEN l.last_year_val IS NULL OR l.last_year_val = 0 THEN NULL
        ELSE ROUND(c.current_val - l.last_year_val, 2)
    END as yoy_diff
FROM current_period c
LEFT JOIN last_year l ON c.period = l.period
ORDER BY c.period
""",
            "mom": """
WITH daily AS (
    SELECT 
        strftime('{grain}', dt) as period,
        SUM({metric}) as val
    FROM fct_orders
    WHERE dt >= date('{start_date}', '-{lookback} {grain}') AND dt <= date('{end_date}')
    GROUP BY 1
)
SELECT 
    period,
    val as current_val,
    LAG(val, 1) OVER (ORDER BY period) as prev_val,
    CASE 
        WHEN LAG(val, 1) OVER (ORDER BY period) IS NULL OR LAG(val, 1) OVER (ORDER BY period) = 0 THEN NULL
        ELSE ROUND((val - LAG(val, 1) OVER (ORDER BY period)) * 100.0 / LAG(val, 1) OVER (ORDER BY period), 2)
    END as mom_rate_pct,
    CASE 
        WHEN LAG(val, 1) OVER (ORDER BY period) IS NULL THEN NULL
        ELSE ROUND(val - LAG(val, 1) OVER (ORDER BY period), 2)
    END as mom_diff
FROM daily
WHERE period >= strftime('{grain}', date('{start_date}'))
ORDER BY period
""",
            "trend": """
SELECT 
    strftime('{grain}', dt) as period,
    SUM({metric}) as val
FROM fct_orders
WHERE dt >= date('{start_date}') AND dt <= date('{end_date}')
GROUP BY 1
ORDER BY 1
""",
            "cumulative": """
SELECT 
    strftime('{grain}', dt) as period,
    SUM({metric}) as period_val,
    SUM(SUM({metric})) OVER (ORDER BY strftime('{grain}', dt)) as cumulative_val
FROM fct_orders
WHERE dt >= date('{start_date}') AND dt <= date('{end_date}')
GROUP BY 1
ORDER BY 1
"""
        },
        "postgresql": {
            "yoy": """
WITH current_period AS (
    SELECT 
        date_trunc('{grain}', dt)::date as period,
        SUM({metric}) as current_val
    FROM fct_orders
    WHERE dt >= '{start_date}'::date AND dt <= '{end_date}'::date
    GROUP BY 1
),
last_year AS (
    SELECT 
        date_trunc('{grain}', dt)::date as period,
        SUM({metric}) as last_year_val
    FROM fct_orders
    WHERE dt >= '{start_date}'::date - INTERVAL '1 year' AND dt <= '{end_date}'::date - INTERVAL '1 year'
    GROUP BY 1
)
SELECT 
    c.period,
    c.current_val,
    l.last_year_val,
    CASE 
        WHEN l.last_year_val IS NULL OR l.last_year_val = 0 THEN NULL
        ELSE ROUND(((c.current_val - l.last_year_val) / l.last_year_val * 100)::numeric, 2)
    END as yoy_rate_pct,
    CASE 
        WHEN l.last_year_val IS NULL THEN NULL
        ELSE ROUND((c.current_val - l.last_year_val)::numeric, 2)
    END as yoy_diff
FROM current_period c
LEFT JOIN last_year l ON c.period = l.period + INTERVAL '1 year'
ORDER BY c.period
""",
            "mom": """
WITH daily AS (
    SELECT 
        date_trunc('{grain}', dt)::date as period,
        SUM({metric}) as val
    FROM fct_orders
    WHERE dt >= '{start_date}'::date - INTERVAL '{lookback} {grain}' AND dt <= '{end_date}'::date
    GROUP BY 1
)
SELECT 
    period,
    val as current_val,
    LAG(val, 1) OVER (ORDER BY period) as prev_val,
    CASE 
        WHEN LAG(val, 1) OVER (ORDER BY period) IS NULL OR LAG(val, 1) OVER (ORDER BY period) = 0 THEN NULL
        ELSE ROUND(((val - LAG(val, 1) OVER (ORDER BY period)) / LAG(val, 1) OVER (ORDER BY period) * 100)::numeric, 2)
    END as mom_rate_pct,
    CASE 
        WHEN LAG(val, 1) OVER (ORDER BY period) IS NULL THEN NULL
        ELSE ROUND((val - LAG(val, 1) OVER (ORDER BY period))::numeric, 2)
    END as mom_diff
FROM daily
WHERE period >= '{start_date}'::date
ORDER BY period
""",
            "trend": """
SELECT 
    date_trunc('{grain}', dt)::date as period,
    SUM({metric}) as val
FROM fct_orders
WHERE dt >= '{start_date}'::date AND dt <= '{end_date}'::date
GROUP BY 1
ORDER BY 1
""",
            "cumulative": """
SELECT 
    date_trunc('{grain}', dt)::date as period,
    SUM({metric}) as period_val,
    SUM(SUM({metric})) OVER (ORDER BY date_trunc('{grain}', dt)) as cumulative_val
FROM fct_orders
WHERE dt >= '{start_date}'::date AND dt <= '{end_date}'::date
GROUP BY 1
ORDER BY 1
"""
        }
    }

    # Time expression patterns for NL parsing
    TIME_PATTERNS = {
        # Base periods
        r"昨天|昨日": {"base_period": "day", "offset": -1},
        r"今天|今日": {"base_period": "day", "offset": 0},
        r"上周": {"base_period": "week", "offset": -1},
        r"本周": {"base_period": "week", "offset": 0},
        r"上月|上个月": {"base_period": "month", "offset": -1},
        r"本月|这个月": {"base_period": "month", "offset": 0},
        r"上季度": {"base_period": "quarter", "offset": -1},
        r"本季度": {"base_period": "quarter", "offset": 0},
        r"去年": {"base_period": "year", "offset": -1},
        r"今年": {"base_period": "year", "offset": 0},
        
        # Range patterns
        r"最近(\d+)天|近(\d+)天": {"base_period": "day", "range": "dynamic"},
        r"最近(\d+)周|近(\d+)周": {"base_period": "week", "range": "dynamic"},
        r"最近(\d+)月|近(\d+)个月": {"base_period": "month", "range": "dynamic"},
        r"最近(\d+)年|近(\d+)年": {"base_period": "year", "range": "dynamic"},
        
        # Specific date references
        r"(\d{4})年(\d{1,2})月": {"base_period": "month", "format": "YYYY-MM"},
        r"(\d{4})年": {"base_period": "year", "format": "YYYY"},
        r"(\d{1,2})月": {"base_period": "month", "format": "MM"},
    }

    # Compare type patterns
    COMPARE_PATTERNS = {
        r"同比|同期|比去年|较上年": "yoy",
        r"环比|比上月|较上月|比上周|较上周": "mom",
        r"趋势|走势|变化|波动": "trend",
        r"累计|累积|YTD|MTD|QTD": "cumulative",
    }

    def __init__(self, db_type: str = "sqlite"):
        self.db_type = db_type

    def parse_time_expression(self, query: str) -> TimeConfig:
        """Parse time expression from natural language query.
        
        Examples:
            "GMV同比" → TimeConfig(base_period="month", compare_type="yoy")
            "上周趋势" → TimeConfig(base_period="day", compare_type="trend", periods=7)
            "最近30天" → TimeConfig(base_period="day", compare_type="trend", periods=30)
        """
        config = TimeConfig(base_period="day", compare_type="trend")
        
        # Detect compare type
        for pattern, comp_type in self.COMPARE_PATTERNS.items():
            if re.search(pattern, query):
                config.compare_type = comp_type
                break
        
        # Detect base period and range
        for pattern, info in self.TIME_PATTERNS.items():
            match = re.search(pattern, query)
            if match:
                config.base_period = info["base_period"]
                
                if info.get("range") == "dynamic":
                    # Extract number from patterns like "最近30天"
                    num_match = re.search(r"(\d+)", query)
                    if num_match:
                        config.periods = int(num_match.group(1))
                elif "offset" in info:
                    # Fixed offset like "昨天", "上周"
                    config.periods = 1  # Single period
                
                break
        
        # Default periods based on compare type
        if config.compare_type == "trend" and config.periods == 30:
            # If no explicit range for trend, default to reasonable period
            if config.base_period == "day":
                config.periods = 30
            elif config.base_period == "week":
                config.periods = 12
            elif config.base_period == "month":
                config.periods = 12
            elif config.base_period == "quarter":
                config.periods = 4
            elif config.base_period == "year":
                config.periods = 3
        
        return config

    def build_sql(self, metric: str, time_config: TimeConfig, 
                  dimensions: Optional[List[str]] = None) -> str:
        """Build time intelligence SQL query.
        
        Args:
            metric: Metric column name (e.g., "gmv")
            time_config: Parsed time configuration
            dimensions: Optional dimension columns for grouping
        
        Returns:
            SQL query string
        """
        template = self.TEMPLATES[self.db_type].get(time_config.compare_type)
        if not template:
            raise ValueError(f"Unsupported compare type: {time_config.compare_type}")
        
        # Calculate date range
        start_date, end_date = self._calculate_date_range(time_config)
        
        # Determine granularity
        grain = self._get_grain(time_config.base_period)
        
        # Build dimension group by clause
        dim_group = ""
        dim_select = ""
        if dimensions:
            dim_select = ", " + ", ".join(dimensions)
            dim_group = ", " + ", ".join(dimensions)
        
        # Format SQL
        sql = template.format(
            metric=metric,
            grain=grain,
            start_date=start_date,
            end_date=end_date,
            lookback=time_config.periods,
            dim_select=dim_select,
            dim_group=dim_group
        )
        
        return sql

    def _calculate_date_range(self, config: TimeConfig) -> Tuple[str, str]:
        """Calculate date range based on time config.
        
        Returns:
            (start_date, end_date) as strings in YYYY-MM-DD format
        """
        from datetime import datetime, timedelta
        
        today = datetime.now().date()
        
        if config.base_period == "day":
            if config.periods == 1:
                # Single day (yesterday/today)
                end_date = today
                start_date = end_date
            else:
                # Range
                end_date = today
                start_date = today - timedelta(days=config.periods - 1)
        
        elif config.base_period == "week":
            end_date = today
            start_date = today - timedelta(weeks=config.periods - 1)
        
        elif config.base_period == "month":
            end_date = today
            # Approximate month calculation
            start_date = today - timedelta(days=config.periods * 30 - 1)
        
        elif config.base_period == "quarter":
            end_date = today
            start_date = today - timedelta(days=config.periods * 90 - 1)
        
        elif config.base_period == "year":
            end_date = today
            start_date = today - timedelta(days=config.periods * 365 - 1)
        
        return str(start_date), str(end_date)

    def _get_grain(self, base_period: str) -> str:
        """Get SQL date grain format."""
        grain_map = {
            "day": "%Y-%m-%d",
            "week": "%Y-%W",
            "month": "%Y-%m",
            "quarter": "%Y-Q" + "((CAST(strftime('%m', dt) AS INTEGER) + 2) / 3)",  # SQLite quarter hack
            "year": "%Y",
        }
        return grain_map.get(base_period, "%Y-%m-%d")

    def format_result(self, results: List[Dict], config: TimeConfig) -> Dict:
        """Format time intelligence results with insights.
        
        Returns:
            Dict with formatted data and insights
        """
        if not results:
            return {"data": [], "insights": "无数据"}
        
        insights = []
        
        if config.compare_type == "yoy":
            # Calculate average YoY rate
            rates = [r.get("yoy_rate_pct") for r in results if r.get("yoy_rate_pct") is not None]
            if rates:
                avg_rate = sum(rates) / len(rates)
                latest = results[-1]
                insights.append(f"平均同比增长率: {avg_rate:+.1f}%")
                if latest.get("yoy_rate_pct") is not None:
                    insights.append(f"最新周期 ({latest['period']}) 同比: {latest['yoy_rate_pct']:+.1f}%")
        
        elif config.compare_type == "mom":
            rates = [r.get("mom_rate_pct") for r in results if r.get("mom_rate_pct") is not None]
            if rates:
                avg_rate = sum(rates) / len(rates)
                insights.append(f"平均环比增长率: {avg_rate:+.1f}%")
        
        elif config.compare_type == "trend":
            values = [r.get("val", 0) for r in results]
            if len(values) >= 2:
                first_val = values[0]
                last_val = values[-1]
                change = (last_val - first_val) / first_val * 100 if first_val else 0
                insights.append(f"整体变化: {change:+.1f}%")
                insights.append(f"最高值: {max(values):.2f}, 最低值: {min(values):.2f}")
        
        return {
            "data": results,
            "insights": "; ".join(insights),
            "config": {
                "compare_type": config.compare_type,
                "base_period": config.base_period,
                "periods": config.periods
            }
        }


# Convenience function
def build_time_query(query: str, metric: str, db_type: str = "sqlite", 
                     dimensions: Optional[List[str]] = None) -> Tuple[str, TimeConfig]:
    """Build time intelligence SQL from natural language query.
    
    Args:
        query: Natural language query (e.g., "GMV同比")
        metric: Metric column name
        db_type: Database type ("sqlite" or "postgresql")
        dimensions: Optional dimension columns
    
    Returns:
        (sql, time_config) tuple
    """
    engine = TimeIntelligence(db_type)
    config = engine.parse_time_expression(query)
    sql = engine.build_sql(metric, config, dimensions)
    return sql, config
