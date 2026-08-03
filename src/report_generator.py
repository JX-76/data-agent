# -*- coding: utf-8 -*-
"""ReportGenerator: Generates structured reports from analysis results.

Supports multiple output formats:
- Markdown
- HTML
- PDF (via weasyprint or similar)
- JSON

Phase R17: ProductReport now exposes the full product-facing field set:
  headline, summary, key_findings, evidence, chart, caveats,
  recommendations, methodology (R17 additions).
  All prior fields (conclusion, data_scope, next_actions, chart_hint,
  confidence, task_type, raw) remain for backward compatibility.

  ``build_product_report`` detects ``analysis_output_v1`` input and
  routes through the template registry so report_generator no longer
  needs to guess scattered internal fields.
"""
from __future__ import unicode_literals

import json

from chart_spec import normalize_chart_spec
from report_templates import DEFAULT_REPORT_TEMPLATE_REGISTRY

try:
    unicode
except NameError:  # pragma: no cover - Python 3 compatibility
    unicode = str


# ---------------------------------------------------------------------------
# Stable ProductReport field set (R17 additions are marked)
# ---------------------------------------------------------------------------
_PRODUCT_REPORT_KEYS_LEGACY = frozenset([
    "conclusion", "key_findings", "data_scope", "caveats",
    "next_actions", "chart_hint", "confidence", "task_type", "raw",
])
_PRODUCT_REPORT_KEYS_R17 = frozenset([
    "headline", "summary", "evidence", "chart", "recommendations", "methodology",
])
PRODUCT_REPORT_KEYS = _PRODUCT_REPORT_KEYS_LEGACY | _PRODUCT_REPORT_KEYS_R17


def _ensure_unicode(value):
    if value is None:
        return u""
    if isinstance(value, unicode):
        return value
    try:
        return value.decode("utf-8")
    except Exception:
        try:
            return unicode(value)
        except Exception:
            return u"%s" % value


def _normalize_for_json(value):
    if isinstance(value, dict):
        return dict((_ensure_unicode(k), _normalize_for_json(v)) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return [_normalize_for_json(v) for v in value]
    return _ensure_unicode(value) if isinstance(value, str) else value


def _safe_json_dumps(value, **kwargs):
    kwargs.setdefault("ensure_ascii", True)
    kwargs.setdefault("default", _ensure_unicode)
    return json.dumps(_normalize_for_json(value), **kwargs)


try:
    import structlog
    logger = structlog.get_logger("report_generator")
except ImportError:
    class _Logger(object):
        def info(self, *args, **kwargs):
            return None
    logger = _Logger()


class ReportSection(object):
    """A section in a report."""

    def __init__(self, title, content, type="text", metadata=None):
        self.title = title
        self.content = content
        self.type = type
        self.metadata = metadata or {}

    def to_dict(self):
        return {
            "title": self.title,
            "content": self.content,
            "type": self.type,
            "metadata": dict(self.metadata),
        }


class ProductReport(object):
    """Stable product-facing report schema for Data Agent responses.

    R17 added fields: headline, summary, evidence, chart, recommendations,
    methodology.  All legacy fields (conclusion, data_scope, next_actions,
    chart_hint, confidence, task_type, raw) are preserved for backward compat.
    """

    def __init__(self, conclusion="", key_findings=None, data_scope=None, caveats=None,
                 next_actions=None, chart_hint=None, confidence=None, task_type="descriptive",
                 raw=None,
                 # R17 additions
                 headline=None, summary=None, evidence=None, chart=None,
                 recommendations=None, methodology=None):
        self.conclusion = conclusion
        self.key_findings = key_findings or []
        self.data_scope = data_scope or {}
        self.caveats = caveats or []
        self.next_actions = next_actions or []
        self.chart_hint = normalize_chart_spec(chart_hint or {"type": "none"})
        self.confidence = confidence
        self.task_type = task_type or "descriptive"
        self.raw = raw or {}
        # R17 product fields
        self.headline = headline if headline is not None else conclusion
        self.summary = summary if summary is not None else conclusion
        self.evidence = evidence if evidence is not None else dict(data_scope or {})
        self.chart = normalize_chart_spec(chart or chart_hint or {"type": "none"})
        self.recommendations = recommendations if recommendations is not None else list(next_actions or [])
        self.methodology = methodology or u""

    def to_dict(self):
        return {
            # Legacy fields (stable)
            "conclusion": self.conclusion,
            "key_findings": list(self.key_findings),
            "data_scope": dict(self.data_scope),
            "caveats": list(self.caveats),
            "next_actions": list(self.next_actions),
            "chart_hint": dict(self.chart_hint),
            "confidence": self.confidence,
            "task_type": self.task_type,
            "raw": dict(self.raw),
            # R17 product fields
            "headline": self.headline,
            "summary": self.summary,
            "evidence": dict(self.evidence) if isinstance(self.evidence, dict) else self.evidence,
            "chart": dict(self.chart),
            "recommendations": list(self.recommendations),
            "methodology": self.methodology,
        }


class Report(object):
    """A generated report."""

    def __init__(self, title, sections=None, metadata=None):
        self.title = title
        self.sections = sections or []
        self.metadata = metadata or {}

    def __contains__(self, item):
        if item in ("headline", "evidence"):
            return True
        return item in self.to_dict()

    def __getitem__(self, item):
        if item == "headline":
            return self.title
        if item == "evidence":
            return [section.to_dict() for section in self.sections]
        return self.to_dict()[item]

    def to_markdown(self):
        lines = [u"# %s\n" % _ensure_unicode(self.title)]
        for section in self.sections:
            lines.append(u"\n## %s\n" % _ensure_unicode(section.title))
            lines.append(_ensure_unicode(section.content))
            lines.append(u"\n")
        return u"\n".join(lines)

    def to_html(self):
        html = "<html><head><title>%s</title></head><body>" % self.title
        html += "<h1>%s</h1>" % self.title
        for section in self.sections:
            html += "<h2>%s</h2>" % section.title
            if section.type == "table":
                html += "<pre>%s</pre>" % section.content
            elif section.type == "code":
                html += "<pre><code>%s</code></pre>" % section.content
            else:
                html += "<p>%s</p>" % section.content
        html += "</body></html>"
        return html

    def to_dict(self):
        return {
            "title": self.title,
            "sections": [s.to_dict() for s in self.sections],
            "metadata": dict(self.metadata),
        }

    def to_json(self):
        return self.to_dict()


class ReportGenerator(object):
    """Generates reports from analysis results."""

    def __init__(self):
        self.templates = {}

    def generate(self, data, template=None):
        if isinstance(template, str) and template in self.templates:
            return self.templates[template](data)
        return self._default_generate(data)

    def generate_product_report(self, data):
        return build_product_report(data)

    def _default_generate(self, data):
        report = Report(title=data.get("title", "Analysis Report"))
        # Prefer analysis_output_v1 summary if available
        ao = _as_dict(data.get("analysis") or {})
        summary = data.get("summary") or (ao.get("summary") if ao.get("contract") == "analysis_output_v1" else None)
        if summary:
            report.sections.append(ReportSection(title="Summary", content=summary, type="text"))
        elif "summary" in data:
            report.sections.append(ReportSection(title="Summary", content=data["summary"], type="text"))
        if "chart" in data and data["chart"]:
            report.sections.append(ReportSection(title="Chart", content=_safe_json_dumps(data["chart"], indent=2), type="code"))
        if "analysis" in data and data["analysis"]:
            analysis = _as_dict(data["analysis"])
            report.sections.append(ReportSection(
                title="Analysis",
                content=_safe_json_dumps(analysis, indent=2),
                type="code",
                metadata={"analysis_type": analysis.get("type"), "analysis_status": analysis.get("status")},
            ))
        if "results" in data:
            report.sections.append(ReportSection(title="Results", content=_safe_json_dumps(data["results"], indent=2), type="code"))
        if "insights" in data:
            insights = data["insights"]
            if isinstance(insights, list):
                content = u"\n".join([u"- %s" % _ensure_unicode(insight) for insight in insights])
            else:
                content = _ensure_unicode(insights)
            report.sections.append(ReportSection(title="Insights", content=content, type="text"))
        if "recommendations" in data:
            recommendations = data["recommendations"]
            if isinstance(recommendations, list):
                content = u"\n".join([u"- %s" % _ensure_unicode(rec) for rec in recommendations])
            else:
                content = _ensure_unicode(recommendations)
            report.sections.append(ReportSection(title="Recommendations", content=content, type="text"))
        report.metadata = {
            "source": data.get("source", "agent_facade"),
            "status": data.get("status"),
            "query": data.get("query"),
            "diagnostics": dict(data.get("diagnostics") or {}),
            "caveats": list(data.get("caveats") or []),
        }
        return report

    def register_template(self, name, func):
        self.templates[name] = func
        logger.info("template_registered", name=name)


def _as_dict(data):
    if data is None:
        return {}
    if isinstance(data, dict):
        return data
    if hasattr(data, "to_dict"):
        return data.to_dict()
    return dict(getattr(data, "__dict__", {}) or {})


def _is_analysis_output_v1(data):
    return _as_dict(data).get("contract") == "analysis_output_v1"


def build_product_report_from_analysis_output(analysis_output, registry=None, task_type=None):
    """Build ProductReport directly from an analysis_output_v1 payload.

    This is the preferred path in Phase R17 onwards.  The template registry
    is used for task-type-specific wording; every field is sourced from the
    stable analysis_output_v1 contract rather than scattered internal dicts.
    """
    ao = _as_dict(analysis_output)
    effective_task_type = task_type or ao.get("type") or "descriptive"
    # Wrap as a data dict that report_templates can recognise
    payload = dict(ao)
    payload.setdefault("task_type", effective_task_type)
    result = (registry or DEFAULT_REPORT_TEMPLATE_REGISTRY).render(payload)
    return _payload_to_product_report(result)


def build_product_report(data, registry=None):
    """Build the stable ProductReport contract through a task-type template.

    ``registry`` is optional for dependency injection in tests or deployments;
    callers that use the long-standing one-argument API continue to use the
    default registry.

    R17: if ``data`` is (or embeds) an ``analysis_output_v1`` payload, the
    template receives it directly so no internal-field guessing is needed.
    """
    data = _as_dict(data)
    # Detect analysis_output_v1 as direct input
    if _is_analysis_output_v1(data):
        return build_product_report_from_analysis_output(data, registry=registry)
    # Detect analysis_output_v1 embedded under "analysis"
    embedded = _as_dict(data.get("analysis") or {})
    if _is_analysis_output_v1(embedded):
        task_type = embedded.get("type") or data.get("task_type")
        return build_product_report_from_analysis_output(embedded, registry=registry, task_type=task_type)
    # Legacy path: infer task_type from insight/plan fallbacks
    if not data.get("task_type"):
        insight = _as_dict(data.get("insight") or data.get("analysis") or {})
        data["task_type"] = insight.get("task_type") or _as_dict(data.get("plan")).get("task_type") or "descriptive"
    payload = (registry or DEFAULT_REPORT_TEMPLATE_REGISTRY).render(data)
    return _payload_to_product_report(payload)


def _payload_to_product_report(payload):
    return ProductReport(
        conclusion=payload.get("conclusion", ""),
        key_findings=payload.get("key_findings"),
        data_scope=payload.get("data_scope"),
        caveats=payload.get("caveats"),
        next_actions=payload.get("next_actions"),
        chart_hint=payload.get("chart_hint"),
        confidence=payload.get("confidence"),
        task_type=payload.get("task_type"),
        raw=payload.get("raw"),
        # R17
        headline=payload.get("headline"),
        summary=payload.get("summary"),
        evidence=payload.get("evidence"),
        chart=payload.get("chart"),
        recommendations=payload.get("recommendations"),
        methodology=payload.get("methodology"),
    )


def generate_product_report(data):
    return build_product_report(data)


def generate_report(data, template=None):
    generator = ReportGenerator()
    return generator.generate(data, template)


__all__ = [
    "ReportSection", "Report", "ProductReport", "ReportGenerator",
    "build_product_report", "build_product_report_from_analysis_output",
    "generate_product_report", "generate_report",
    "PRODUCT_REPORT_KEYS",
]
