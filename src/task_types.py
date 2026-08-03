# -*- coding: utf-8 -*-
"""Canonical analysis task types for routing and execution dispatch."""

DESCRIPTIVE = "descriptive"
COMPARISON = "comparison"
ATTRIBUTION = "attribution"
FUNNEL = "funnel"
RETENTION = "retention"
ANOMALY = "anomaly"
FORECAST = "forecast"
EXPERIMENT = "experiment"
UNSUPPORTED = "unsupported"

SUPPORTED_EXECUTION_TASK_TYPES = (DESCRIPTIVE, COMPARISON, ANOMALY, ATTRIBUTION, RETENTION)
ALL_TASK_TYPES = (DESCRIPTIVE, COMPARISON, ATTRIBUTION, FUNNEL, RETENTION, ANOMALY, FORECAST, EXPERIMENT, UNSUPPORTED)


try:
    unicode
except NameError:  # pragma: no cover - Python 3 compatibility
    unicode = str


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
            return u""


def infer_task_type(query, intent=None):
    text = _ensure_unicode(query or u"").lower()
    if any(token in text for token in [u"\u6f0f\u6597", u"funnel"]):
        return FUNNEL
    if any(token in text for token in [u"\u7559\u5b58", u"cohort", u"retention"]):
        return RETENTION
    if any(token in text for token in [u"\u4e3a\u4ec0\u4e48", u"\u539f\u56e0", u"\u5f52\u56e0", u"\u8d21\u732e", u"driver", u"attribution", u"\u62c9\u4f4e", u"\u62c9\u52a8", u"\u9a71\u52a8"]):
        return ATTRIBUTION
    if any(token in text for token in [u"\u5f02\u5e38", u"\u7a81\u589e", u"\u7a81\u964d", u"\u6ce2\u52a8", u"\u504f\u9ad8", u"\u504f\u4f4e", u"anomaly"]):
        return ANOMALY
    if (u"\u4e0a\u5468" in text or u"\u4e0a\u6708" in text or u"\u672c\u5468" in text or u"\u672c\u6708" in text or u"\u8fd9\u5468" in text or u"\u8fd9\u6708" in text) and any(token in text for token in [u"\u4e3a\u4ec0\u4e48", u"\u539f\u56e0", u"\u4e0b\u964d", u"\u589e\u957f", u"\u51cf\u5c11", u"\u63d0\u5347", u"\u6ce2\u52a8"]):
        return COMPARISON
    if any(token in text for token in [u"\u5bf9\u6bd4", u"\u6bd4\u8f83", u"\u540c\u6bd4", u"\u73af\u6bd4", u" vs ", u"vs.", u" versus "]):
        return COMPARISON
    if any(token in text for token in [u"ab\u5b9e\u9a8c", u"a/b", u"\u5b9e\u9a8c\u7ec4", u"\u5bf9\u7167\u7ec4", u"experiment"]):
        return EXPERIMENT
    if any(token in text for token in [u"\u9884\u6d4b", u"forecast", u"\u672a\u6765"]):
        return FORECAST
    if (u"\u4e0a\u5468" in text or u"\u4e0a\u6708" in text) and any(token in text for token in [u"\u548c", u"\u4e0e", u"\u8ddf"]) and (u"\u672c\u5468" in text or u"\u672c\u6708" in text or u"\u8fd9\u5468" in text or u"\u8fd9\u6708" in text):
        return COMPARISON
    if intent in ("comparison", "compare"):
        return COMPARISON
    return DESCRIPTIVE


def is_supported_execution(task_type):
    return (task_type or DESCRIPTIVE) in SUPPORTED_EXECUTION_TASK_TYPES


__all__ = [
    "DESCRIPTIVE", "COMPARISON", "ATTRIBUTION", "FUNNEL", "RETENTION", "ANOMALY", "FORECAST", "EXPERIMENT", "UNSUPPORTED",
    "SUPPORTED_EXECUTION_TASK_TYPES", "ALL_TASK_TYPES", "infer_task_type", "is_supported_execution",
]
