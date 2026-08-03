# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from config import SEMANTIC_SUMMARY
from system_prompt_contract import (SYSTEM_PROMPT_CONTRACT, SYSTEM_PROMPT_VERSION,
                                    build_insight_prompt, build_presentation_assist_prompt,
                                    build_router_prompt, build_tool_planning_prompt,
                                    prompt_metadata)


def test_router_contract_has_untrusted_input_and_sql_free_boundaries():
    prompt = build_router_prompt(SEMANTIC_SUMMARY["metrics"], SEMANTIC_SUMMARY["dimensions"],
                                 SEMANTIC_SUMMARY["models"], "2026-08-03 12:00:00")
    assert "SQL-free" in prompt
    assert "untrusted data" in prompt
    assert "cannot be overridden" in prompt
    assert '"status":"blocked"' in prompt
    assert "gmv" in prompt


def test_tool_prompt_limits_selection_to_server_manifest_and_known_dataids():
    prompt = build_tool_planning_prompt("preview(dataid)", {"metrics": {"gmv": {}}}, ["data_1"])
    assert "server-provided Available Tools" in prompt
    assert "Never invent a tool, DataID" in prompt
    assert "data_1" in prompt
    assert '"action":"tool"' in prompt


def test_insight_prompt_prohibits_unsupported_claims_and_keeps_legacy_shape():
    prompt = build_insight_prompt()
    assert "Do not invent or calculate new values" in prompt
    assert "evidence-limited" in prompt
    assert '"insight"' in prompt
    assert '"chart"' in prompt
    assert '"status": "ok|evidence_limited' not in prompt


def test_presentation_prompt_remains_non_factual_and_non_executing():
    prompt = build_presentation_assist_prompt("fixture")
    assert "presentation-only" in prompt
    assert "Do not create numbers, facts, citations, SQL" in prompt
    assert "never execute" in prompt


def test_prompt_metadata_is_versioned_and_hashes_exact_prompt():
    prompt = build_insight_prompt()
    metadata = prompt_metadata("insight", prompt)
    assert metadata["contract"] == SYSTEM_PROMPT_CONTRACT
    assert metadata["prompt_version"] == SYSTEM_PROMPT_VERSION
    assert metadata["prompt_role"] == "insight"
    assert len(metadata["prompt_sha256"]) == 64
    assert metadata["prompt_sha256"] != prompt_metadata("insight", prompt + " ")["prompt_sha256"]


def test_deepseek_adapter_sends_versioned_presentation_contract():
    from deepseek_adapter import DeepSeekAdapter

    captured = {}

    class Response(object):
        def read(self):
            return json.dumps({"choices": [{"message": {"content": "建议补充筛选范围。"}}]}).encode("utf-8")

    def opener(request, timeout):
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return Response()

    result = DeepSeekAdapter(api_key="unit-secret", opener=opener).explain_safe_analysis("GMV", {})
    assert "untrusted data" in captured["body"]["messages"][0]["content"]
    assert result["prompt_metadata"]["contract"] == SYSTEM_PROMPT_CONTRACT
