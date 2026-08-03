# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); SRC = os.path.join(ROOT, "src")
if SRC not in sys.path: sys.path.insert(0, SRC)
from masking_policy import sanitize_agent_payload, sanitize_output, sanitize_text


def test_recursive_output_masking_covers_results_analysis_report_and_raw():
    payload = {"results": [{"user_id": "USR1234", "email": "alice@example.com"}],
               "analysis": {"contract": "analysis_output_v1", "raw": {"email": "alice@example.com"}},
               "report": {"raw": {"phone": "13812345678"}}}
    safe = sanitize_agent_payload(payload)
    text = repr(safe)
    assert "alice@example.com" not in text
    assert "13812345678" not in text
    assert "USR1234" not in text
    assert safe["analysis"]["contract"] == "analysis_output_v1"


def test_recursive_output_masking_covers_chinese_pii_aliases_in_all_product_payloads():
    payload = {
        "results": [{"身份证号": "110101199001011234", "手机号": "13812345678"}],
        "analysis": {"raw": {"邮箱": "alice@example.com", "联系地址": "北京市朝阳区"}},
        "report": {"debug": {"address": "北京市朝阳区"}},
    }
    safe = sanitize_agent_payload(payload, masked_fields=["id_card", "phone", "email", "address"])
    text = repr(safe)
    for raw_value in ("110101199001011234", "13812345678", "alice@example.com", "北京市朝阳区"):
        assert raw_value not in text


def test_free_text_masking_covers_prompt_log_sql_literals():
    text = "用户 alice@example.com 手机 13812345678 token=abc123 身份证 110101199001011234"
    safe = sanitize_text(text)
    assert "alice@example.com" not in safe
    assert "13812345678" not in safe
    assert "abc123" not in safe
    assert "110101199001011234" not in safe
    assert "***PHONE***" in safe


def test_trace_correlation_ids_are_not_masked_as_phone_numbers():
    trace_id = "179dd00f-1234-4a56-9abc-a2a74502950c"
    task_id = "92d2157b-b19c-4329-b275-bf727abb9013"
    payload = {
        "trace_id": trace_id,
        "task_id": task_id,
        "metadata": {"phone": "13800138000"},
    }
    masked = sanitize_output(payload)
    assert masked["trace_id"] == trace_id
    assert masked["task_id"] == task_id
    assert masked["metadata"]["phone"] != "13800138000"
