# -*- coding: utf-8 -*-
"""R23 recursive output masking boundary."""
from __future__ import unicode_literals

import re

from masking import get_masker
from permission_policy import SENSITIVE_FIELDS, SENSITIVE_FIELD_ALIASES

_EMAIL_RE = re.compile(r"([A-Za-z0-9._%+\-])[A-Za-z0-9._%+\-]*(@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})")
_PHONE_RE = re.compile(r"(?<!\d)(1[3-9]\d{9}|\+?\d[\d\-\s]{7,}\d)(?!\d)")
_TOKEN_RE = re.compile(r"(?i)\b(api[_-]?key|token|secret|password)\s*[:=]\s*[^\s,;]+")
_ID_CARD_RE = re.compile(r"(?<!\d)(\d{6}(?:19|20)\d{2}\d{7}[0-9Xx])(?!\d)")
_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_CORRELATION_ID_KEYS = set([
    "trace_id", "task_id", "query_id", "tool_call_id", "evidence_id",
    "event_id", "parent_event_id", "session_id", "request_id", "correlation_id",
])


def _is_sensitive(key, extra=None):
    low = str(key).lower()
    if low in set(extra or []).union(SENSITIVE_FIELDS):
        return True
    if any(alias.lower() in low for aliases in SENSITIVE_FIELD_ALIASES.values() for alias in aliases):
        return True
    return any(token in low for token in (
        "email", "phone", "mobile", "password", "secret", "token", "api_key",
        "ip_address", "client_ip", "id_card", "idcard", "身份证", "地址", "address",
    ))


def sanitize_text(value):
    """Mask PII-like literals in free text such as query/sql/log fields."""
    if not isinstance(value, basestring):
        return value
    # Trace/replay identifiers are often UUIDs. The generic phone regex is
    # intentionally broad for free text, but UUIDs must remain stable so traces
    # can be correlated and replayed across facade, harness and quality gates.
    if _UUID_RE.match(value):
        return value
    text = value
    text = _EMAIL_RE.sub(lambda m: m.group(1) + "***" + m.group(2), text)
    text = _PHONE_RE.sub("***PHONE***", text)
    text = _ID_CARD_RE.sub("***ID_CARD***", text)
    text = _TOKEN_RE.sub(lambda m: m.group(1) + "=***", text)
    return text


def sanitize_output(value, masked_fields=None):
    """Recursively mask sensitive keyed values, including raw/debug payloads."""
    masked_fields = set(masked_fields or [])
    masker = get_masker()
    if isinstance(value, list):
        return [sanitize_output(item, masked_fields) for item in value]
    if isinstance(value, tuple):
        return [sanitize_output(item, masked_fields) for item in value]
    if isinstance(value, basestring):
        return sanitize_text(value)
    if not isinstance(value, dict):
        return value
    safe = {}
    for key, item in value.items():
        key_text = str(key).lower()
        if key_text in _CORRELATION_ID_KEYS:
            # Correlation identifiers are not user data facts; masking them
            # breaks trace lookup/replay. Still sanitize nested structures if a
            # non-scalar is accidentally attached under an id-like key.
            if isinstance(item, (dict, list, tuple)):
                safe[key] = sanitize_output(item, masked_fields)
            else:
                safe[key] = item
        elif _is_sensitive(key, masked_fields):
            # DataMasker owns consistent value representation.
            safe[key] = masker.mask_dict({key: item}).get(key)
            if safe[key] == item:
                safe[key] = "***"
        else:
            safe[key] = sanitize_output(item, masked_fields)
    return safe


def sanitize_agent_payload(payload, masked_fields=None):
    return sanitize_output(payload or {}, masked_fields=masked_fields)


try:
    basestring
except NameError:  # pragma: no cover
    basestring = str


__all__ = ["sanitize_output", "sanitize_agent_payload", "sanitize_text"]
