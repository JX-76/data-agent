"""产品分析模板 - 品类、SKU、价格分析。

Features:
- 品类分析
- SKU分析
- 价格分析
- 库存分析
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

import structlog

logger = structlog.get_logger("product_analysis")


@dataclass
class CategoryAnalysis:
    """品类分析"""
    category: str
    gmv: float = 0.0
    order_count: int = 0
    percentage: float = 0.0
    growth: float = 0.0


@dataclass
class SKUAnalysis:
    """SKU分析"""
    sku_id: str
    sku_name: str
    gmv: float = 0.0
    order_count: int = 0
    avg_price: float = 0.0
    stock: int = 0
    status: str = "normal"  # normal, low, out_of_stock


@dataclass
class PriceAnalysis:
    """价格分析"""
    price_range: str
    gmv: float = 0.0
    order_count: int = 0
    percentage: float = 0.0


class ProductAnalysisTemplate:
    """产品分析模板"""
    
    def __init__(self, db_executor=None):
        self.db = db_executor
        self.logger = structlog.get_logger("product_analysis")
    
    def generate(self, date: Optional[str] = None) -> Dict[str, Any]:
        """生成产品分析报告"""
        if date is None:
            date = datetime.datetime.now().strftime("%Y-%m-%d")
        
        self.logger.info("generating_product_report", date=date)
        
        # 1. 品类分析
        categories = self._get_category_analysis(date)
        
        # 2. SKU分析
        skus = self._get_sku_analysis(date)
        
        # 3. 价格分析
        prices = self._get_price_analysis(date)
        
        return {
            "date": date,
            "categories": categories,
            "skus": skus,
            "prices": prices,
        }
    
    def _get_category_analysis(self, date: str) -> List[CategoryAnalysis]:
        """获取品类分析"""
        # 模拟数据
        return [
            CategoryAnalysis("服装", 5000000, 5000, 50.0, 0.10),
            CategoryAnalysis("鞋帽", 3000000, 3000, 30.0, 0.05),
            CategoryAnalysis("配饰", 2000000, 2000, 20.0, 0.15),
        ]
    
    def _get_sku_analysis(self, date: str) -> List[SKUAnalysis]:
        """获取SKU分析"""
        # 模拟数据
        return [
            SKUAnalysis("SKU001", "iPhone 15", 1000000, 1000, 1000, 100, "normal"),
            SKUAnalysis("SKU002", "iPhone 15 Pro", 800000, 800, 1000, 50, "low"),
            SKUAnalysis("SKU003", "AirPods Pro", 500000, 500, 1000, 200, "normal"),
            SKUAnalysis("SKU004", "iPad Pro", 300000, 300, 1000, 0, "out_of_stock"),
            SKUAnalysis("SKU005", "MacBook Pro", 200000, 200, 1000, 30, "low"),
        ]
    
    def _get_price_analysis(self, date: str) -> List[PriceAnalysis]:
        """获取价格分析"""
        # 模拟数据
        return [
            PriceAnalysis("0-100元", 1000000, 10000, 10.0),
            PriceAnalysis("100-500元", 3000000, 6000, 30.0),
            PriceAnalysis("500-1000元", 4000000, 4000, 40.0),
            PriceAnalysis("1000-5000元", 2000000, 500, 20.0),
            PriceAnalysis("5000元以上", 1000000, 100, 10.0),
        ]
    
    def render_markdown(self, data: Dict[str, Any]) -> str:
        """渲染Markdown报告"""
        categories = data["categories"]
        skus = data["skus"]
        prices = data["prices"]
        
        report = f"""# 📦 产品分析报告 - {data['date']}

## 【品类分析】

| 品类 | GMV | 订单量 | 占比 | 增长 |
|------|-----|--------|------|------|
"""
        
        for cat in categories:
            report += f"| {cat.category} | ¥{cat.gmv:,.2f} | {cat.order_count:,} | {cat.percentage:.1f}% | {'+' if cat.growth >= 0 else ''}{cat.growth*100:.1f}% |\n"
        
        report += "\n## 【SKU分析】\n\n| SKU | 名称 | GMV | 订单量 | 均价 | 库存 | 状态 |\n|-----|------|-----|--------|------|------|------|\n"
        
        for sku in skus:
            status_icon = "🟢" if sku.status == "normal" else "🟡" if sku.status == "low" else "🔴"
            report += f"| {sku.sku_id} | {sku.sku_name} | ¥{sku.gmv:,.2f} | {sku.order_count:,} | ¥{sku.avg_price:,.2f} | {sku.stock} | {status_icon} {sku.status} |\n"
        
        report += "\n## 【价格分析】\n\n| 价格区间 | GMV | 订单量 | 占比 |\n|----------|-----|--------|------|\n"
        
        for price in prices:
            report += f"| {price.price_range} | ¥{price.gmv:,.2f} | {price.order_count:,} | {price.percentage:.1f}% |\n"
        
        return report


# ── 快捷函数 ──

def generate_product_report(date: Optional[str] = None, db_executor=None) -> str:
    """生成产品分析报告"""
    template = ProductAnalysisTemplate(db_executor)
    data = template.generate(date)
    return template.render_markdown(data)


if __name__ == "__main__":
    # 测试
    report = generate_product_report()
    print(report)
