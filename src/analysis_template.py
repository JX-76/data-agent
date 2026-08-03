"""Analysis Template Engine — 分析模板引擎，将DAG输出接入可视化。

Features:
- 模板注册与管理
- 数据映射与转换
- 图表配置生成
- 多视图支持
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Union
from pathlib import Path


def detect_template_from_query(query: str) -> str | None:
    """Backward-compatible helper used by dag_agent.route_and_plan()."""
    q = query.lower()
    if any(k in q for k in ["日报", "周报", "月报", "看板", "dashboard"]):
        return "daily_report"
    if any(k in q for k in ["库存", "缺货", "售罄", "周转"]):
        return "inventory_monitor"
    if any(k in q for k in ["销售", "gmv", "订单", "渠道", "aov", "客单价"]):
        return "sales_analysis"
    return None

import structlog

logger = structlog.get_logger("analysis_template")


@dataclass
class ChartConfig:
    """图表配置"""
    chart_type: str  # line, bar, pie, table, metric, funnel, scatter
    title: str = ""
    x_axis: str = ""
    y_axis: str = ""
    dimensions: List[str] = field(default_factory=list)
    metrics: List[str] = field(default_factory=list)
    colors: List[str] = field(default_factory=list)
    options: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ViewConfig:
    """视图配置"""
    view_id: str
    name: str
    description: str = ""
    charts: List[ChartConfig] = field(default_factory=list)
    layout: str = "grid"  # grid, tabs, dashboard


@dataclass
class AnalysisTemplate:
    """分析模板"""
    template_id: str
    name: str
    description: str = ""
    views: List[ViewConfig] = field(default_factory=list)
    data_transforms: Dict[str, Callable] = field(default_factory=dict)


class TemplateRegistry:
    """模板注册中心"""
    
    def __init__(self):
        self.templates: Dict[str, AnalysisTemplate] = {}
        self.logger = structlog.get_logger("template_registry")
    
    def register(self, template: AnalysisTemplate) -> None:
        """注册模板"""
        self.templates[template.template_id] = template
        self.logger.info("template_registered", template_id=template.template_id)
    
    def get(self, template_id: str) -> Optional[AnalysisTemplate]:
        """获取模板"""
        return self.templates.get(template_id)
    
    def list_templates(self) -> List[str]:
        """列出所有模板"""
        return list(self.templates.keys())


class AnalysisTemplateEngine:
    """分析模板引擎"""
    
    def __init__(self):
        self.registry = TemplateRegistry()
        self.logger = structlog.get_logger("analysis_template_engine")
        self._register_default_templates()
    
    def _register_default_templates(self) -> None:
        """注册默认模板"""
        # 运营日报模板
        daily_report = AnalysisTemplate(
            template_id="daily_report",
            name="运营日报",
            description="每日运营数据报告",
            views=[
                ViewConfig(
                    view_id="overview",
                    name="概览",
                    charts=[
                        ChartConfig(
                            chart_type="metric",
                            title="核心指标",
                            metrics=["gmv", "order_count", "aov"],
                        ),
                        ChartConfig(
                            chart_type="line",
                            title="GMV趋势",
                            x_axis="date",
                            y_axis="gmv",
                        ),
                    ],
                ),
                ViewConfig(
                    view_id="channel",
                    name="渠道分析",
                    charts=[
                        ChartConfig(
                            chart_type="pie",
                            title="渠道分布",
                            dimensions=["channel"],
                            metrics=["gmv"],
                        ),
                    ],
                ),
            ],
        )
        self.registry.register(daily_report)
        
        # 库存监控模板
        inventory_monitor = AnalysisTemplate(
            template_id="inventory_monitor",
            name="库存监控",
            description="实时库存监控与预警",
            views=[
                ViewConfig(
                    view_id="overview",
                    name="库存概览",
                    charts=[
                        ChartConfig(
                            chart_type="metric",
                            title="库存指标",
                            metrics=["total_skus", "total_stock_value"],
                        ),
                        ChartConfig(
                            chart_type="bar",
                            title="库存状态分布",
                            x_axis="status",
                            y_axis="count",
                        ),
                    ],
                ),
            ],
        )
        self.registry.register(inventory_monitor)
        
        # 销售分析模板
        sales_analysis = AnalysisTemplate(
            template_id="sales_analysis",
            name="销售分析",
            description="多维度销售分析",
            views=[
                ViewConfig(
                    view_id="trend",
                    name="销售趋势",
                    charts=[
                        ChartConfig(
                            chart_type="line",
                            title="GMV趋势",
                            x_axis="date",
                            y_axis="gmv",
                        ),
                    ],
                ),
                ViewConfig(
                    view_id="channel",
                    name="渠道分析",
                    charts=[
                        ChartConfig(
                            chart_type="bar",
                            title="渠道GMV",
                            x_axis="channel",
                            y_axis="gmv",
                        ),
                    ],
                ),
            ],
        )
        self.registry.register(sales_analysis)
    
    def render(self, template_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """渲染模板"""
        template = self.registry.get(template_id)
        if not template:
            self.logger.error("template_not_found", template_id=template_id)
            return {}
        
        self.logger.info("rendering_template", template_id=template_id)
        
        result = {
            "template_id": template_id,
            "name": template.name,
            "views": [],
        }
        
        for view in template.views:
            view_data = {
                "view_id": view.view_id,
                "name": view.name,
                "charts": [],
            }
            
            for chart in view.charts:
                chart_data = self._render_chart(chart, data)
                view_data["charts"].append(chart_data)
            
            result["views"].append(view_data)
        
        return result
    
    def _render_chart(self, chart: ChartConfig, data: Dict[str, Any]) -> Dict[str, Any]:
        """渲染单个图表"""
        return {
            "chart_type": chart.chart_type,
            "title": chart.title,
            "data": data,
            "options": chart.options,
        }
    
    def generate_dashboard_html(self, template_id: str, data: Dict[str, Any]) -> str:
        """生成Dashboard HTML"""
        template = self.registry.get(template_id)
        if not template:
            return "<p>Template not found</p>"
        
        rendered = self.render(template_id, data)
        
        # 生成HTML
        html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>{template.name}</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 0; padding: 20px; background: #0d1117; color: #c9d1d9; }}
        .container {{ max-width: 1400px; margin: 0 auto; }}
        h1 {{ color: #58a6ff; }}
        .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 20px; }}
        .card {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 20px; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>{template.name}</h1>
        <div class="grid">
"""
        
        for view in rendered.get("views", []):
            for chart in view.get("charts", []):
                html += f"""
            <div class="card">
            <h3>{chart['title']}</h3>
            <div id="chart-{chart['chart_type']}"></div>
        </div>
"""
        
        html += """
        </div>
    </div>
</body>
</html>
"""
        
        return html


# ── 快捷函数 ──

def create_analysis_engine() -> AnalysisTemplateEngine:
    """创建分析模板引擎"""
    return AnalysisTemplateEngine()


def render_template(template_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """渲染分析模板"""
    engine = create_analysis_engine()
    return engine.render(template_id, data)


def generate_dashboard(template_id: str, data: Dict[str, Any]) -> str:
    """生成Dashboard HTML"""
    engine = create_analysis_engine()
    return engine.generate_dashboard_html(template_id, data)


if __name__ == "__main__":
    # 测试
    engine = create_analysis_engine()
    
    # 渲染运营日报
    data = {
        "gmv": 1234567.89,
        "order_count": 1234,
        "aov": 1000,
    }
    result = engine.render("daily_report", data)
    print(json.dumps(result, indent=2, ensure_ascii=False))
