"""生产质量模板 - 传感器数据监控与SPC控制图。

Features:
- 传感器数据监控
- SPC控制图
- 良率统计
- 缺陷分析
- 设备状态监控
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

import structlog

logger = structlog.get_logger("quality_control")


@dataclass
class QualityStats:
    """质量统计"""
    total_production: int = 0
    good_units: int = 0
    defect_units: int = 0
    yield_rate: float = 0.0
    defect_rate: float = 0.0
    cpk: float = 0.0


@dataclass
class DefectType:
    """缺陷类型"""
    defect_name: str
    count: int = 0
    percentage: float = 0.0
    severity: str = "minor"  # minor, major, critical


@dataclass
class EquipmentStatus:
    """设备状态"""
    equipment_id: str
    equipment_name: str
    status: str = "normal"  # normal, warning, error
    oee: float = 0.0
    uptime: float = 0.0


class QualityControlTemplate:
    """生产质量模板"""
    
    def __init__(self, db_executor=None):
        self.db = db_executor
        self.logger = structlog.get_logger("quality_control")
    
    def generate(self, date: Optional[str] = None) -> Dict[str, Any]:
        """生成质量报告"""
        if date is None:
            date = datetime.datetime.now().strftime("%Y-%m-%d")
        
        self.logger.info("generating_quality_report", date=date)
        
        # 1. 质量统计
        stats = self._get_quality_stats(date)
        
        # 2. 缺陷分析
        defects = self._get_defect_analysis(date)
        
        # 3. 设备状态
        equipment = self._get_equipment_status(date)
        
        return {
            "date": date,
            "stats": stats,
            "defects": defects,
            "equipment": equipment,
        }
    
    def _get_quality_stats(self, date: str) -> QualityStats:
        """获取质量统计"""
        # 模拟数据
        return QualityStats(
            total_production=10000,
            good_units=9800,
            defect_units=200,
            yield_rate=0.98,
            defect_rate=0.02,
            cpk=1.33,
        )
    
    def _get_defect_analysis(self, date: str) -> List[DefectType]:
        """获取缺陷分析"""
        # 模拟数据
        return [
            DefectType("外观缺陷", 100, 50.0, "minor"),
            DefectType("尺寸偏差", 50, 25.0, "major"),
            DefectType("功能异常", 30, 15.0, "critical"),
            DefectType("包装破损", 20, 10.0, "minor"),
        ]
    
    def _get_equipment_status(self, date: str) -> List[EquipmentStatus]:
        """获取设备状态"""
        # 模拟数据
        return [
            EquipmentStatus("EQ001", "注塑机A", "normal", 0.85, 95.0),
            EquipmentStatus("EQ002", "注塑机B", "warning", 0.75, 85.0),
            EquipmentStatus("EQ003", "冲压机A", "normal", 0.90, 98.0),
        ]
    
    def render_markdown(self, data: Dict[str, Any]) -> str:
        """渲染Markdown报告"""
        stats = data["stats"]
        defects = data["defects"]
        equipment = data["equipment"]
        
        report = f"""# 🏭 生产质量报告 - {data['date']}

## 【质量概览】

| 指标 | 数值 |
|------|------|
| 总产量 | {stats.total_production:,} |
| 良品数 | {stats.good_units:,} |
| 不良品数 | {stats.defect_units:,} |
| 良率 | {stats.yield_rate*100:.2f}% |
| 不良率 | {stats.defect_rate*100:.2f}% |
| CPK值 | {stats.cpk:.2f} |

## 【缺陷分析】

| 缺陷类型 | 数量 | 占比 | 严重程度 |
|----------|------|------|----------|
"""
        
        for defect in defects:
            severity_icon = "🔴" if defect.severity == "critical" else "🟡" if defect.severity == "major" else "🟢"
            report += f"| {defect.defect_name} | {defect.count} | {defect.percentage:.1f}% | {severity_icon} {defect.severity} |\n"
        
        report += "\n## 【设备状态】\n\n| 设备 | 名称 | 状态 | OEE | 运行时间 |\n|------|------|------|-----|----------|\n"
        
        for eq in equipment:
            status_icon = "🟢" if eq.status == "normal" else "🟡" if eq.status == "warning" else "🔴"
            report += f"| {eq.equipment_id} | {eq.equipment_name} | {status_icon} {eq.status} | {eq.oee*100:.1f}% | {eq.uptime:.1f}% |\n"
        
        return report


# ── 快捷函数 ──

def generate_quality_report(date: Optional[str] = None, db_executor=None) -> str:
    """生成生产质量报告"""
    template = QualityControlTemplate(db_executor)
    data = template.generate(date)
    return template.render_markdown(data)


if __name__ == "__main__":
    # 测试
    report = generate_quality_report()
    print(report)
