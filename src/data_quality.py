"""Data Quality — 数据质量检测与主动提示。

P2-2: 数据质量主动提示
- 空值率检测
- 数据延迟检测
- 异常值标记
- 质量报告嵌入查询结果
"""

from __future__ import annotations

from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
import structlog

logger = structlog.get_logger("data_quality")


@dataclass
class QualityReport:
    """数据质量报告。"""
    status: str = "ok"  # ok / warning / error
    checks: List[Dict[str, Any]] = field(default_factory=list)
    score: float = 1.0  # 0.0 ~ 1.0
    messages: List[str] = field(default_factory=list)


def check_results(results: List[Dict[str, Any]], 
                  columns: Optional[List[str]] = None,
                  freshness_hours: int = 24) -> QualityReport:
    """对查询结果进行数据质量检测。
    
    Args:
        results: 查询结果行列表
        columns: 关注的列（None=全部）
        freshness_hours: 数据新鲜度阈值（小时）
        
    Returns:
        质量报告
    """
    report = QualityReport()
    
    if not results:
        report.status = "ok"
        report.messages = ["查询结果为空，可能是数据尚未更新"]
        report.score = 0.8
        return report
    
    # 确定列
    if columns is None and results:
        columns = list(results[0].keys())
    
    # 1. 空值率检测
    for col in columns:
        null_count = sum(1 for r in results if r.get(col) is None or r.get(col) == "")
        null_rate = null_count / len(results)
        if null_rate > 0.5:
            report.checks.append({
                "type": "null_rate_high",
                "column": col,
                "null_rate": round(null_rate, 2),
                "severity": "warning",
            })
            report.messages.append(f"⚠️ 列'{col}'空值率 {null_rate:.0%}，数据可能不完整")
            report.score = min(report.score, 0.6)
        elif null_rate > 0.1:
            report.checks.append({
                "type": "null_rate_moderate",
                "column": col,
                "null_rate": round(null_rate, 2),
                "severity": "info",
            })
    
    # 2. 行数过少检测
    if len(results) < 5:
        report.checks.append({
            "type": "row_count_low",
            "rows": len(results),
            "severity": "info",
        })
        report.messages.append(f"💡 结果仅{len(results)}行，可能过滤条件过严格")
    
    # 3. 重复行检测
    total_rows = len(results)
    try:
        unique_rows = len(set(tuple(sorted(r.items())) for r in results))
        if unique_rows < total_rows and total_rows > 1:
            dup_rate = 1 - unique_rows / total_rows
            report.checks.append({
                "type": "duplicate_rows",
                "total": total_rows,
                "unique": unique_rows,
                "dup_rate": round(dup_rate, 2),
                "severity": "info",
            })
            if dup_rate > 0.5:
                report.messages.append(f"⚠️ 数据重复率 {dup_rate:.0%}，请检查查询逻辑")
                report.score = min(report.score, 0.7)
    except (TypeError, AttributeError):
        pass  # 某些类型不可哈希，跳过
    
    # 4. 数值异常检测（基于IQR）
    numeric_cols = []
    if results:
        for col in columns:
            vals = []
            for r in results:
                v = r.get(col)
                if isinstance(v, (int, float)) and v is not None:
                    vals.append(v)
            if len(vals) >= 4:
                numeric_cols.append((col, vals))
    
    for col, vals in numeric_cols:
        sorted_vals = sorted(vals)
        n = len(sorted_vals)
        q1 = sorted_vals[n // 4]
        q3 = sorted_vals[3 * n // 4]
        iqr = q3 - q1
        if iqr == 0:
            continue
        lower = q1 - 1.5 * iqr
        upper = q3 + 1.5 * iqr
        outliers = [v for v in vals if v < lower or v > upper]
        if outliers:
            report.checks.append({
                "type": "outliers",
                "column": col,
                "outlier_count": len(outliers),
                "outlier_rate": round(len(outliers) / len(vals), 2),
                "iqr_range": (lower, upper),
                "severity": "warning" if len(outliers) / len(vals) > 0.2 else "info",
            })
            if len(outliers) / len(vals) > 0.2:
                report.messages.append(f"⚠️ 列'{col}'存在 {len(outliers)} 个异常值（IQR方法）")
                report.score = min(report.score, 0.7)
    
    # 5. 综合判断
    if report.score >= 0.9:
        report.status = "ok"
    elif report.score >= 0.7:
        report.status = "warning"
    else:
        report.status = "error"
    
    if not report.messages:
        report.messages = ["✅ 数据质量良好"]
    
    return report


def format_quality_note(report: QualityReport) -> str:
    """将质量报告格式化为用户友好的提示文本。"""
    if not report.messages:
        return ""
    return "\n".join(report.messages)


# ── 快速检测函数（用于DAG集成）──

def quick_check(results: List[Dict[str, Any]], 
                columns: Optional[List[str]] = None) -> QualityReport:
    """快速数据质量检测（轻量版，用于高频调用）。"""
    if not results:
        return QualityReport(status="ok", messages=["查询结果为空"], score=1.0)
    
    if columns is None:
        columns = list(results[0].keys())
    
    report = QualityReport()
    
    # 仅做空值率检测（最快）
    for col in columns:
        null_count = sum(1 for r in results if r.get(col) is None or r.get(col) == "")
        null_rate = null_count / len(results)
        if null_rate > 0.5:
            report.messages.append(f"⚠️ 列'{col}'空值率 {null_rate:.0%}")
            report.score = 0.6
        elif null_rate > 0.1:
            report.messages.append(f"📊 列'{col}'空值率 {null_rate:.0%}")
    
    if not report.messages:
        report.messages = ["✅ 数据完整"]
    
    return report
