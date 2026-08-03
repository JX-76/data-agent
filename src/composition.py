"""Composition Analysis: percentage, distribution, and structure analysis.

Handles queries like:
- "各渠道占比"
- "品类构成"
- "区域分布"
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass
class CompositionResult:
    """Result of composition analysis."""
    dimension: str
    metric: str
    total: float
    items: List[Dict]
    
    def to_chart_data(self) -> List[Dict]:
        """Convert to pie chart data format."""
        return [
            {"name": item["label"], "value": item["value"]}
            for item in self.items
        ]
    
    def to_table_data(self) -> List[Dict]:
        """Convert to table format with percentages."""
        return self.items


class CompositionAnalyzer:
    """Analyzer for composition/percentage queries."""

    def analyze(self, results: List[Dict], dimension: str, metric: str) -> CompositionResult:
        """Analyze composition from query results.
        
        Args:
            results: Query results with dimension and metric columns
            dimension: Dimension column name
            metric: Metric column name
        
        Returns:
            CompositionResult with percentages and insights
        """
        if not results:
            return CompositionResult(dimension=dimension, metric=metric, total=0, items=[])
        
        # Calculate total
        total = sum(float(r.get(metric, 0) or 0) for r in results)
        
        # Build items with percentages
        items = []
        for r in results:
            value = float(r.get(metric, 0) or 0)
            pct = (value / total * 100) if total > 0 else 0
            
            items.append({
                "label": str(r.get(dimension, "未知")),
                "value": round(value, 2),
                "percentage": round(pct, 2),
                "raw": r
            })
        
        # Sort by value descending
        items.sort(key=lambda x: x["value"], reverse=True)
        
        # Add rank
        for i, item in enumerate(items, 1):
            item["rank"] = i
        
        return CompositionResult(
            dimension=dimension,
            metric=metric,
            total=round(total, 2),
            items=items
        )
    
    def generate_insights(self, result: CompositionResult) -> List[str]:
        """Generate natural language insights from composition."""
        insights = []
        
        if not result.items:
            return ["无数据"]
        
        # Top contributor
        top = result.items[0]
        insights.append(
            f"{top['label']} 占比最高，达到 {top['percentage']:.1f}%"
            f"（{top['value']:.2f}）"
        )
        
        # Concentration analysis
        top3_pct = sum(item["percentage"] for item in result.items[:3])
        if top3_pct > 80:
            insights.append(f"前3项合计占比 {top3_pct:.1f}%，高度集中")
        elif top3_pct < 50:
            insights.append(f"前3项合计仅占 {top3_pct:.1f}%，分布较为分散")
        
        # Long tail
        if len(result.items) > 5:
            others_pct = sum(item["percentage"] for item in result.items[5:])
            insights.append(f"其余 {len(result.items) - 5} 项合计占比 {others_pct:.1f}%")
        
        # Comparison
        if len(result.items) >= 2:
            first = result.items[0]
            second = result.items[1]
            ratio = first["value"] / second["value"] if second["value"] > 0 else float('inf')
            if ratio > 2:
                insights.append(
                    f"{first['label']} 是 {second['label']} 的 {ratio:.1f} 倍"
                )
        
        return insights


# SQL templates for composition queries
COMPOSITION_SQL_TEMPLATE = """
SELECT 
    {dimension} as dim,
    SUM({metric}) as total_{metric}
FROM fct_orders
{where_clause}
GROUP BY {dimension}
ORDER BY total_{metric} DESC
"""


def build_composition_sql(dimension: str, metric: str, 
                          where_clause: str = "") -> str:
    """Build SQL for composition analysis.
    
    Args:
        dimension: Dimension column
        metric: Metric column
        where_clause: Optional WHERE clause
    
    Returns:
        SQL query string
    """
    return COMPOSITION_SQL_TEMPLATE.format(
        dimension=dimension,
        metric=metric,
        where_clause=where_clause
    )


# Convenience function
def analyze_composition(results: List[Dict], dimension: str, 
                       metric: str) -> Dict:
    """Analyze composition and return formatted result.
    
    Returns:
        Dict with data, insights, and chart config
    """
    analyzer = CompositionAnalyzer()
    result = analyzer.analyze(results, dimension, metric)
    insights = analyzer.generate_insights(result)
    
    return {
        "dimension": result.dimension,
        "metric": result.metric,
        "total": result.total,
        "data": result.to_table_data(),
        "chart_data": result.to_chart_data(),
        "insights": insights,
        "item_count": len(result.items)
    }
