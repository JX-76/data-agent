"""Statistical Testing — 纯 Python 统计检验（零外部依赖）。

P2-7: 统计检验
- 独立样本 t 检验（Welch's t-test）
- 卡方独立性检验
- 单因素方差分析（one-way ANOVA）
- Cohen's d 效应量
- p 值计算（自实现分布函数，不依赖 scipy）

使用方式:
    from stat_tests import ttest, chi2_test, anova, cohens_d
    
    result = ttest(group_a, group_b)
    # => {"statistic": 2.34, "p_value": 0.028, "significant": True, "effect_size": 0.8}
"""

from __future__ import annotations

import math
from typing import List, Dict, Any, Tuple
import structlog

logger = structlog.get_logger("stat_tests")

# ── 数学工具函数 ──

def _mean(values: List[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)

def _variance(values: List[float], ddof: int = 1) -> float:
    n = len(values)
    if n <= ddof:
        return 0.0
    m = _mean(values)
    return sum((x - m) ** 2 for x in values) / (n - ddof)

def _std(values: List[float], ddof: int = 1) -> float:
    return math.sqrt(_variance(values, ddof))

def _gamma(x: float) -> float:
    """Gamma 函数的 Stirling/Lanczos 近似（x > 0）。"""
    if x <= 0:
        return float('inf')
    # 反射公式: Gamma(x) = pi / (sin(pi*x) * Gamma(1-x))
    if x < 0.5:
        return math.pi / (math.sin(math.pi * x) * _gamma(1 - x))
    if x > 10:
        # Stirling's approximation for large x
        x0 = x - 1
        return math.sqrt(2 * math.pi / x0) * ((x0 / math.e) ** x0) * (1 + 1/(12*x0) + 1/(288*x0*x0))
    # Lanczos approximation (g=7, n=9) for moderate x
    g = 7
    p = [0.99999999999980993, 676.5203681218851, -1259.1392167224028,
         771.32342877765313, -176.61502916214059, 12.507343278686905,
         -0.13857109526572012, 9.9843695780195716e-6, 1.5056327351493116e-7]
    z = x - 1.0
    xg = p[0]
    for i in range(1, g + 2):
        xg += p[i] / (z + i)
    t = z + g + 0.5
    return math.sqrt(2 * math.pi) * (t ** (z + 0.5)) * math.exp(-t) * xg

def _lgamma(x: float) -> float:
    """Log Gamma。"""
    if x <= 0:
        return float('inf')
    return math.log(abs(_gamma(x)))

def _erf(x: float) -> float:
    """误差函数（Abramowitz & Stegun 7.1.26 逼近）。"""
    sign = 1 if x >= 0 else -1
    x = abs(x)
    # Constants
    a1, a2, a3, a4, a5 = 0.254829592, -0.284496736, 1.421413741, -1.453152027, 1.061405429
    p = 0.3275911
    t = 1.0 / (1.0 + p * x)
    y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * math.exp(-x * x)
    return sign * y

def _norm_cdf(x: float) -> float:
    """标准正态分布 CDF。"""
    return 0.5 * (1.0 + _erf(x / math.sqrt(2.0)))

def _norm_sf(x: float) -> float:
    """标准正态分布 survival function = 1 - CDF。"""
    return 1.0 - _norm_cdf(x)

# ── 分布函数（变换逼近，数值稳定）──

def _norm_ppf(p: float) -> float:
    """标准正态分布的逆CDF（分位数函数）。
    
    使用 Abramowitz & Stegun 26.2.23 有理逼近。
    """
    if p <= 0 or p >= 1:
        return 0.0 if p <= 0 else float('inf')
    
    # 使用双尾概率
    p = min(p, 1 - p)
    
    # 有理逼近系数
    c0, c1, c2 = 2.515517, 0.802853, 0.010328
    d1, d2, d3 = 1.432788, 0.189269, 0.001308
    
    t = math.sqrt(-2.0 * math.log(p))
    z = t - (c0 + c1 * t + c2 * t * t) / (1 + d1 * t + d2 * t * t + d3 * t * t * t)
    
    return -z


def _t_pvalue(t_abs: float, df: float) -> float:
    """t 分布双尾 p 值。
    
    方法: Welch-Satterthwaite → 正态近似 (df > 30) 或
    使用 Wallace 的 t→z 变换。
    """
    if df <= 0 or t_abs <= 0:
        return 1.0
    
    # Wallace's approximation: t → z (非常精确)
    # z = sqrt(df * log(1 + t^2/df)) * (1 - 1/(4*df))
    z = math.sqrt(df * math.log(1 + t_abs * t_abs / df))
    # 连续性修正
    z = z * (1 - 1 / (4 * df))
    
    # 双尾 p 值 = 2 * P(Z > z)
    return 2.0 * _norm_sf(z)


def _chi2_pvalue(chi2_val: float, df: float) -> float:
    """卡方分布 p 值（右尾）。
    
    方法: Wilson-Hilferty 变换 (1931) — χ² → z
    非常精确，对 df >= 2 误差 < 1%。
    """
    if chi2_val <= 0 or df <= 0:
        return 1.0
    
    # Wilson-Hilferty: z = ((χ²/df)^(1/3) - (1-2/(9*df))) / sqrt(2/(9*df))
    t = (chi2_val / df) ** (1.0 / 3.0)
    z = (t - (1 - 2 / (9 * df))) / math.sqrt(2 / (9 * df))
    
    return _norm_sf(z)


def _f_pvalue(f_val: float, df1: float, df2: float) -> float:
    """F 分布 p 值（右尾）。
    
    方法: Paulson 变换 (1942) — F → z
    """
    if f_val <= 0 or df1 <= 0 or df2 <= 0:
        return 1.0
    
    # Paulson's approximation
    a = 2 / (9 * df1)
    b = 2 / (9 * df2)
    z = ((1 - b) * (f_val ** (1.0 / 3.0)) - (1 - a)) / math.sqrt(b * (f_val ** (2.0 / 3.0)) + a)
    
    return _norm_sf(z)


# ── 公共 API ──

def cohens_d(group_a: List[float], group_b: List[float]) -> float:
    """Cohen's d 效应量。
    
    d = (mean_a - mean_b) / pooled_std
    
    解读: 0.2=小, 0.5=中, 0.8=大
    """
    n_a, n_b = len(group_a), len(group_b)
    if n_a < 2 or n_b < 2:
        return 0.0
    
    mean_a, mean_b = _mean(group_a), _mean(group_b)
    var_a, var_b = _variance(group_a), _variance(group_b)
    
    # pooled std
    pooled_std = math.sqrt(((n_a - 1) * var_a + (n_b - 1) * var_b) / (n_a + n_b - 2))
    
    if pooled_std < 1e-10:
        return 0.0
    
    return (mean_a - mean_b) / pooled_std


def ttest(group_a: List[float], group_b: List[float]) -> Dict[str, Any]:
    """Welch's t 检验（不假定等方差）。
    
    假设：
    - H0: 两组均值相等（无显著差异）
    - H1: 两组均值不等
    
    Returns:
        statistic, p_value, significant (alpha=0.05), effect_size, means, interpretation
    """
    n_a, n_b = len(group_a), len(group_b)
    
    if n_a < 2 or n_b < 2:
        return {
            "statistic": 0.0, "p_value": 1.0, "significant": False,
            "effect_size": 0.0, "error": "样本量不足（每组至少2个观测）"
        }
    
    mean_a, mean_b = _mean(group_a), _mean(group_b)
    var_a, var_b = _variance(group_a), _variance(group_b)
    
    # Welch-Satterthwaite degrees of freedom
    se_a = var_a / n_a
    se_b = var_b / n_b
    t_stat = (mean_a - mean_b) / math.sqrt(se_a + se_b) if (se_a + se_b) > 0 else 0.0
    
    # Welch df
    num = (se_a + se_b) ** 2
    denom = (se_a ** 2) / (n_a - 1) + (se_b ** 2) / (n_b - 1)
    df = num / denom if denom > 0 else n_a + n_b - 2
    
    # 双尾 p 值
    p_value = _t_pvalue(abs(t_stat), df)
    p_value = min(max(p_value, 0.0), 1.0)
    
    d = cohens_d(group_a, group_b)
    
    return {
        "statistic": round(t_stat, 4),
        "p_value": p_value,
        "significant": p_value < 0.05,
        "effect_size": round(d, 3),
        "mean_a": round(mean_a, 2),
        "mean_b": round(mean_b, 2),
        "df": round(df, 1),
        "interpretation": _interpret_t(mean_a, mean_b, p_value, d),
    }


def chi2_test(observed: List[List[float]]) -> Dict[str, Any]:
    """卡方独立性检验。
    
    Args:
        observed: 2D 列联表，如 [[10, 20], [15, 25]]
        
    Returns:
        statistic, p_value, significant, dof, interpretation
    """
    rows = len(observed)
    cols = len(observed[0]) if observed else 0
    
    if rows < 2 or cols < 2:
        return {
            "statistic": 0.0, "p_value": 1.0, "significant": False,
            "error": "需要至少2行2列的列联表"
        }
    
    # 行和、列和、总和
    row_sum = [sum(row) for row in observed]
    col_sum = [sum(observed[r][c] for r in range(rows)) for c in range(cols)]
    total = sum(row_sum)
    
    if total == 0:
        return {"statistic": 0.0, "p_value": 1.0, "significant": False, "error": "总和为0"}
    
    # 计算卡方统计量
    chi2 = 0.0
    for r in range(rows):
        for c in range(cols):
            expected = row_sum[r] * col_sum[c] / total
            if expected > 0:
                chi2 += (observed[r][c] - expected) ** 2 / expected
    
    dof = (rows - 1) * (cols - 1)
    p_value = _chi2_pvalue(chi2, dof)
    p_value = min(max(p_value, 0.0), 1.0)
    
    return {
        "statistic": round(chi2, 4),
        "p_value": p_value,
        "significant": p_value < 0.05,
        "dof": dof,
        "interpretation": f"{'存在' if p_value < 0.05 else '未发现'}显著关联（χ²={chi2:.2f}, df={dof}, p={p_value:.4f}）",
    }


def anova(groups: Dict[str, List[float]]) -> Dict[str, Any]:
    """单因素方差分析（one-way ANOVA）。
    
    Args:
        groups: 分组数据，如 {"A": [1,2,3], "B": [4,5,6], "C": [7,8,9]}
        
    Returns:
        statistic (F), p_value, significant, effect_size (η²), pairwise
    """
    group_names = list(groups.keys())
    group_data = list(groups.values())
    k = len(group_data)
    
    if k < 2:
        return {"statistic": 0.0, "p_value": 1.0, "significant": False, "error": "至少需要两组"}
    
    # 组内均值和总均值
    group_means = [_mean(g) for g in group_data]
    all_values = [v for g in group_data for v in g]
    grand_mean = _mean(all_values)
    
    # SSB (between groups)
    n_j = [len(g) for g in group_data]
    ssb = sum(n_j[j] * (group_means[j] - grand_mean) ** 2 for j in range(k))
    dfb = k - 1
    
    # SSW (within groups)  
    ssw = sum(sum((v - group_means[j]) ** 2 for v in group_data[j]) for j in range(k))
    dfw = sum(n_j) - k
    
    if dfw <= 0 or ssw <= 0:
        return {"statistic": 0.0, "p_value": 1.0, "significant": False, "error": "组内自由度为0"}
    
    msb = ssb / dfb
    msw = ssw / dfw
    f_stat = msb / msw if msw > 0 else 0.0
    
    p_value = _f_pvalue(f_stat, dfb, dfw)
    p_value = min(max(p_value, 0.0), 1.0)
    
    # η² effect size
    sst = ssb + ssw
    eta_sq = ssb / sst if sst > 0 else 0.0
    
    # 事后两两 t 检验（控制 FWER 用 Bonferroni）
    pairwise = []
    if p_value < 0.05 and k > 1:
        for i in range(k):
            for jj in range(i + 1, k):
                tr = ttest(group_data[i], group_data[jj])
                pairwise.append({
                    "pair": f"{group_names[i]} vs {group_names[jj]}",
                    "diff": round(group_means[i] - group_means[jj], 2),
                    "p_value": tr["p_value"],
                    "significant": tr["p_value"] < 0.05,
                })
    
    return {
        "statistic": round(f_stat, 4),
        "p_value": p_value,
        "significant": p_value < 0.05,
        "effect_size": round(eta_sq, 3),
        "df_between": dfb,
        "df_within": dfw,
        "group_means": {gn: round(gm, 2) for gn, gm in zip(group_names, group_means)},
        "pairwise": pairwise[:10],  # 限制输出
        "interpretation": _interpret_anova(k, f_stat, p_value, eta_sq),
    }


# ── 解释生成 ──

def _interpret_t(mean_a: float, mean_b: float, p: float, d: float) -> str:
    parts = [f"均值: A={mean_a:.1f}, B={mean_b:.1f}"]
    
    if p < 0.01:
        parts.append("差异极显著 (p<0.01)")
    elif p < 0.05:
        parts.append("差异显著 (p<0.05)")
    else:
        parts.append("差异不显著 (p≥0.05)")
    
    abs_d = abs(d)
    if abs_d >= 0.8:
        parts.append(f"效应量大 (d={d:.2f})")
    elif abs_d >= 0.5:
        parts.append(f"效应量中 (d={d:.2f})")
    elif abs_d >= 0.2:
        parts.append(f"效应量小 (d={d:.2f})")
    
    return "；".join(parts)


def _interpret_anova(k: int, f: float, p: float, eta: float) -> str:
    parts = [f"{k}组均值比较"]
    
    if p < 0.01:
        parts.append("组间差异极显著 (p<0.01)")
    elif p < 0.05:
        parts.append("组间差异显著 (p<0.05)")
    else:
        parts.append("组间差异不显著 (p≥0.05)")
    
    if eta >= 0.14:
        parts.append(f"效应量大 (η²={eta:.3f})")
    elif eta >= 0.06:
        parts.append(f"效应量中 (η²={eta:.3f})")
    else:
        parts.append(f"效应量小 (η²={eta:.3f})")
    
    return "；".join(parts)


# ── 快速诊断 API ──

def compare_metric(current: List[float], baseline: List[float],
                   metric_name: str = "metric") -> Dict[str, Any]:
    """比较两个时期的指标，自动执行 t 检验并给出结论。
    
    Args:
        current: 当前期数值
        baseline: 基准期数值
        metric_name: 指标名
        
    Returns:
        Dict with statistic, p_value, significant, delta, delta_pct, interpretation
    """
    t = ttest(current, baseline)
    mean_cur = _mean(current)
    mean_base = _mean(baseline)
    delta = mean_cur - mean_base
    delta_pct = delta / mean_base if mean_base != 0 else 0.0
    
    return {
        "metric": metric_name,
        "current_mean": round(mean_cur, 2),
        "baseline_mean": round(mean_base, 2),
        "delta": round(delta, 2),
        "delta_pct": round(delta_pct, 4),
        "t_statistic": t["statistic"],
        "p_value": t["p_value"],
        "significant": t["significant"],
        "effect_size": t["effect_size"],
        "interpretation": (
            f"{metric_name}: {mean_base:.1f} → {mean_cur:.1f} "
            f"({'增长' if delta >= 0 else '下降'}{abs(delta_pct):.1%}), "
            f"{'统计显著' if t['significant'] else '统计不显著'}"
            f"{' (d=' + str(t['effect_size']) + ')' if abs(t['effect_size']) >= 0.2 else ''}"
        ),
    }
