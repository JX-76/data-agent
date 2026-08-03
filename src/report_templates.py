# -*- coding: utf-8 -*-
"""Extensible product-report templates.

Templates turn a stable analysis payload into product-facing report fields.
The registry keeps task-specific language out of the AgentFacade and lets new
analysis capabilities add a template without changing the report contract.
"""
from __future__ import unicode_literals

from chart_spec import normalize_chart_spec, recommend_chart_for_task_type

try:
    unicode
except NameError:  # pragma: no cover - Python 3 compatibility
    unicode = str


FINAL_STATUSES = set(["ok", "blocked", "need_clarification", "fallback", "pending_human_review", "error"])


def _as_dict(data):
    if data is None:
        return {}
    if isinstance(data, dict):
        return data
    if hasattr(data, "to_dict"):
        return data.to_dict()
    return dict(getattr(data, "__dict__", {}) or {})


def _listify(value):
    if not value:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _text(value):
    if value is None:
        return u""
    if isinstance(value, unicode):
        return value
    try:
        return value.decode("utf-8")
    except Exception:
        return unicode(value)


def _append_unique(items, value):
    value = _text(value)
    if value and value not in items:
        items.append(value)


def _is_analysis_output_v1(data):
    return _as_dict(data).get("contract") == "analysis_output_v1"


def _analysis_output(data):
    data = _as_dict(data)
    if _is_analysis_output_v1(data):
        return data
    analysis = _as_dict(data.get("analysis") or {})
    if _is_analysis_output_v1(analysis):
        return analysis
    return {}


def _legacy_analysis(data, output):
    data = _as_dict(data)
    if output:
        raw = _as_dict(output.get("raw"))
        return _as_dict(raw.get("source_analysis"))
    return _as_dict(data.get("analysis") or {})


def _legacy_insight(data, output):
    data = _as_dict(data)
    if output:
        raw = _as_dict(output.get("raw"))
        return _as_dict(raw.get("source_insight"))
    return _as_dict(data.get("insight") or data.get("analysis") or {})


def _evidence_to_scope(data, evidence):
    data = _as_dict(data)
    evidence = _as_dict(evidence)
    return {
        "query": data.get("query"),
        "metric": evidence.get("metric") or data.get("metric"),
        "dimensions": list(evidence.get("dimensions") or data.get("dimensions") or []),
        "time_range": evidence.get("time_range") or data.get("time_range"),
        "source": evidence.get("source") or _as_dict(data.get("results_summary")).get("source"),
    }


class ReportTemplate(object):
    """Base template. Subclasses customize wording only; shape is stable."""

    task_type = "descriptive"
    headline_prefix = u"分析报告"
    methodology_text = u"基于标准化 analysis_output_v1 生成，口径以计划中的指标、维度、时间范围和过滤条件为准。"

    def render(self, data):
        data = _as_dict(data)
        output = _analysis_output(data)
        status = output.get("status") or data.get("status") or "ok"
        task_type = output.get("type") or data.get("task_type") or self.task_type or "descriptive"
        analysis = _legacy_analysis(data, output)
        insight = _legacy_insight(data, output)
        evidence = _as_dict(output.get("evidence")) if output else self._legacy_evidence(data, analysis)
        metric = evidence.get("metric") or data.get("metric")
        dimensions = evidence.get("dimensions") or data.get("dimensions") or []
        summary = output.get("summary") or insight.get("summary") or data.get("summary") or self.conclusion(status, metric, analysis)
        findings = list(output.get("key_findings") or []) if output else self.key_findings(data, insight, analysis, metric, dimensions)
        if not findings:
            findings = self.key_findings(data, insight, analysis, metric, dimensions)
        caveats = list(output.get("caveats") or []) + _listify(data.get("caveats"))
        recommendations = list(output.get("next_steps") or []) + _listify(insight.get("next_steps")) + _listify(insight.get("recommendations")) + _listify(data.get("recommendations")) + _listify(data.get("next_actions"))
        if not recommendations:
            recommendations = self.default_recommendations(status)
        chart = output.get("chart") or data.get("chart") or insight.get("chart") or data.get("chart_hint")
        chart = normalize_chart_spec(chart or recommend_chart_for_task_type(task_type, status=None if status == "ok" else status))
        headline = self.headline(status, metric, analysis, summary)
        methodology = self.methodology(data, output, evidence)
        raw = {"status": status, "diagnostics": data.get("diagnostics") or {}, "analysis": analysis, "analysis_output_contract": output.get("contract") if output else None}
        return {"headline": headline, "summary": _text(summary), "key_findings": findings, "evidence": evidence, "chart": chart, "caveats": caveats, "recommendations": recommendations, "methodology": methodology, "conclusion": headline, "data_scope": _evidence_to_scope(data, evidence), "next_actions": recommendations, "chart_hint": chart, "confidence": evidence.get("confidence") or data.get("confidence"), "task_type": task_type, "raw": raw}

    def _legacy_evidence(self, data, analysis):
        data = _as_dict(data)
        return {"metric": data.get("metric"), "dimensions": list(data.get("dimensions") or []), "time_range": data.get("time_range"), "filters": data.get("filters") or [], "row_count": _as_dict(data.get("results_summary")).get("row_count"), "source": data.get("source") or _as_dict(data.get("results_summary")).get("source"), "sql_available": bool(data.get("sql")), "quality": _as_dict(data.get("diagnostics")).get("quality") or {}, "confidence": data.get("confidence")}

    def headline(self, status, metric, analysis, summary):
        return self.conclusion(status, metric, analysis)

    def conclusion(self, status, metric, analysis):
        if status == "blocked": return u"本次请求已被安全策略拦截，无法执行。"
        if status in ("need_clarification", "clarification_needed"): return u"本次分析需要补充口径或范围后继续。"
        if status == "pending_human_review": return u"本次请求需要人工审核后继续。"
        if status == "fallback": return u"本次分析已进入降级路径。"
        if status == "error": return u"本次分析执行失败。"
        return u"分析已完成%s。" % (u"，核心指标是 %s" % metric if metric else u"")

    def key_findings(self, data, insight, analysis, metric, dimensions):
        findings = []
        if insight.get("summary"): findings.append(insight.get("summary"))
        elif analysis.get("summary"): findings.append(analysis.get("summary"))
        elif data.get("summary"): findings.append(data.get("summary"))
        if dimensions: findings.append(u"已按 %s 维度拆解。" % u"、".join(dimensions))
        row_count = _as_dict(analysis.get("summary_facts")).get("row_count", 0)
        if row_count > 0: findings.append(u"查询返回 %s 行数据。" % row_count)
        return findings

    def default_recommendations(self, status):
        if status == "ok": return [u"可以继续按维度下钻或切换时间窗口对比。"]
        if status in ("need_clarification", "clarification_needed"): return [u"请先补充分析口径或选择澄清选项。"]
        if status == "blocked": return [u"请改写为只读分析问题。"]
        if status == "error": return [u"请查看错误信息并重试，或缩小查询范围。"]
        return [u"请根据当前状态处理后再继续分析。"]

    def methodology(self, data, output, evidence):
        return self.methodology_text


class ComparisonReportTemplate(ReportTemplate):
    task_type = "comparison"
    headline_prefix = u"对比分析报告"
    methodology_text = u"基于统一分析输出对不同周期或分组的同一指标进行对比，重点关注绝对变化与相对变化。"

    def conclusion(self, status, metric, analysis):
        if status != "ok":
            return super(ComparisonReportTemplate, self).conclusion(status, metric, analysis)
        facts = _as_dict(analysis.get("summary_facts"))
        delta = facts.get("delta")
        current = analysis.get("current_value") if analysis.get("current_value") is not None else facts.get("current_value")
        previous = analysis.get("previous_value") if analysis.get("previous_value") is not None else facts.get("previous_value")
        if delta is None:
            return u"对比分析已完成，当前值%s，上一周期值%s，数据量不足以计算变化。" % (current, previous)
        direction = u"增长" if delta >= 0 else u"下降"
        return u"对比分析完成：当前值%s，上一周期值%s，核心指标 %s %s %s。" % (current, previous, metric, direction, abs(delta))

    def key_findings(self, data, insight, analysis, metric, dimensions):
        findings = super(ComparisonReportTemplate, self).key_findings(data, insight, analysis, metric, dimensions)
        facts = _as_dict(analysis.get("summary_facts"))
        current = analysis.get("current_value") if analysis.get("current_value") is not None else facts.get("current_value")
        previous = analysis.get("previous_value") if analysis.get("previous_value") is not None else facts.get("previous_value")
        if current is not None or previous is not None:
            findings.append(u"当前值为 %s，上一周期值为 %s。" % (current, previous))
        if facts.get("delta") is not None:
            findings.append(u"与上一周期相比，%s 变化量为 %s。" % (metric, facts.get("delta")))
        return findings


class AnomalyReportTemplate(ReportTemplate):
    task_type = "anomaly"
    headline_prefix = u"异常检测报告"
    methodology_text = u"基于统一分析输出识别时间序列或指标结果中的异常点，并结合证据字段说明影响范围。"

    def conclusion(self, status, metric, analysis):
        if status != "ok":
            return super(AnomalyReportTemplate, self).conclusion(status, metric, analysis)
        facts = _as_dict(analysis.get("summary_facts"))
        count = facts.get("anomaly_count", 0)
        severity = _as_dict(analysis.get("severity_summary")).get("max_severity") or facts.get("max_severity") or u"none"
        return u"异常检测完成：识别出 %s 个异常点，严重度为 %s。" % (count, severity)

    def key_findings(self, data, insight, analysis, metric, dimensions):
        findings = super(AnomalyReportTemplate, self).key_findings(data, insight, analysis, metric, dimensions)
        count = _as_dict(analysis.get("summary_facts")).get("anomaly_count", 0)
        severity = _as_dict(analysis.get("severity_summary")).get("max_severity") or u"none"
        findings.append(u"异常严重度为 %s。" % severity)
        if count:
            findings.append(u"共识别 %s 个异常点。" % count)
        return findings


class AttributionReportTemplate(ReportTemplate):
    task_type = "attribution"
    headline_prefix = u"归因分析报告"
    methodology_text = u"基于统一分析输出拆解指标变化来源，优先展示贡献度最高的驱动因素。"

    def conclusion(self, status, metric, analysis):
        if status != "ok":
            return super(AttributionReportTemplate, self).conclusion(status, metric, analysis)
        drivers = analysis.get("top_drivers") or []
        if drivers:
            return u"归因分析完成：首要驱动因素是「%s」。" % (_as_dict(drivers[0]).get("dimension") or u"未知")
        return u"归因分析已完成，数据量不足以识别驱动因素。"

    def key_findings(self, data, insight, analysis, metric, dimensions):
        findings = super(AttributionReportTemplate, self).key_findings(data, insight, analysis, metric, dimensions)
        drivers = analysis.get("top_drivers") or []
        if drivers:
            first = _as_dict(drivers[0])
            dim = first.get("dimension") or u"未知"
            share = first.get("share_pct")
            if share is None:
                shares = _as_dict(_as_dict(analysis.get("contribution")).get("shares"))
            findings.append(u"首要驱动因素来自「%s」。" % dim)
            pareto = _as_dict(analysis.get("pareto"))
            cutoff = pareto.get("pareto_cutoff")
            if cutoff is not None:
                findings.append(u"按 80%% Pareto 阈值，前 %s 个驱动因素覆盖主要贡献。" % cutoff)
            else:
                findings.append(u"按 80% 贡献阈值复核主要驱动因素。")
        return findings


class RetentionReportTemplate(ReportTemplate):
    task_type = "retention"
    headline_prefix = u"留存分析报告"
    methodology_text = u"按 cohort 首次事件分组，分母为 cohort 内去重实体数，分子为观察周期内发生活跃事件的去重实体数；仅展示聚合结果。"


class FunnelReportTemplate(ReportTemplate):
    task_type = "funnel"
    headline_prefix = u"漏斗分析报告"
    methodology_text = u"基于统一分析输出按稳定阶段字段展示漏斗转化，当前模板可作为占位报告，字段保持稳定。"

    def conclusion(self, status, metric, analysis):
        if status != "ok":
            return super(FunnelReportTemplate, self).conclusion(status, metric, analysis)
        return u"漏斗分析已完成，当前输出为稳定占位模板。"

    def default_recommendations(self, status):
        if status == "ok":
            return [u"建议补充阶段定义与漏斗来源口径后进一步下钻。"]
        return super(FunnelReportTemplate, self).default_recommendations(status)


class ForecastReportTemplate(ReportTemplate):
    task_type = "forecast"
    headline_prefix = u"预测分析报告"
    methodology_text = u"基于统一分析输出，使用受控时序预测模型（seasonal_naive 等）对历史数据进行拟合，结果为统计估计，不构成业务承诺，不具备因果解释能力。"

    def headline(self, status, metric, analysis, summary):
        return u"%s：%s" % (self.headline_prefix, self.conclusion(status, metric, analysis))

    def methodology(self, data, output, evidence):
        facts = _as_dict(_as_dict(_as_dict(data).get("analysis")).get("summary_facts"))
        method = facts.get("method")
        version = facts.get("method_version") or _as_dict(_as_dict(_as_dict(data).get("analysis")).get("definition")).get("method_version")
        parts = [self.methodology_text]
        if method:
            parts.append(u"算法：%s，版本：%s。" % (method, version or u"unknown"))
        return u" ".join(parts)


class ReportTemplateRegistry(object):
    """Maps task types to templates and supplies a safe fallback."""

    def __init__(self, fallback=None):
        self.fallback = fallback or ReportTemplate()
        self._templates = {}

    def register(self, template, task_type=None):
        key = task_type or getattr(template, "task_type", None)
        if not key:
            raise ValueError("report template requires task_type")
        self._templates[key] = template
        return template

    def resolve(self, task_type):
        return self._templates.get(task_type) or self.fallback

    def registered_task_types(self):
        return sorted(self._templates.keys())

    def get_template_metadata(self):
        return {key: getattr(template, "methodology_text", u"") for key, template in self._templates.items()}

    def render(self, data):
        data = _as_dict(data)
        output = _analysis_output(data)
        task_type = output.get("type") or data.get("task_type") or "descriptive"
        return self.resolve(task_type).render(data)


def build_default_registry():
    registry = ReportTemplateRegistry()
    registry.register(ReportTemplate(), "descriptive")
    registry.register(ComparisonReportTemplate())
    registry.register(AnomalyReportTemplate())
    registry.register(AttributionReportTemplate())
    registry.register(RetentionReportTemplate())
    registry.register(FunnelReportTemplate())
    registry.register(ForecastReportTemplate())
    return registry


DEFAULT_REPORT_TEMPLATE_REGISTRY = build_default_registry()


__all__ = ["ReportTemplate", "ComparisonReportTemplate", "AnomalyReportTemplate", "AttributionReportTemplate", "RetentionReportTemplate", "FunnelReportTemplate", "ForecastReportTemplate", "ReportTemplateRegistry", "build_default_registry", "DEFAULT_REPORT_TEMPLATE_REGISTRY"]
