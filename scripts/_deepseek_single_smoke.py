# -*- coding: utf-8 -*-
from __future__ import print_function, unicode_literals
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from deepseek_adapter import DeepSeekAdapter, DeepSeekError


def main():
    adapter = DeepSeekAdapter()
    health = adapter.health()
    result = {
        "contract": "deepseek_single_smoke_result_v1",
        "provider": "deepseek",
        "model": health.get("model"),
        "ready": bool(health.get("ready")),
        "secret_exposed": False,
    }
    if not health.get("ready"):
        result.update({"status": "failed", "error_code": "missing_api_key"})
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 2
    try:
        assist = adapter.explain_safe_analysis(
            "请为已验证数据提供一条阅读建议。",
            {"metrics": {"metric": "GMV", "time_range": "最近7天"},
             "report_sections": {"summary": "已验证摘要：当前数据范围已受 evidence gate 保护。"}}
        )
        result.update({
            "status": assist.get("status"),
            "latency_ms": assist.get("latency_ms"),
            "text_length": len(assist.get("text") or ""),
        })
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0 if assist.get("status") == "ok" else 1
    except DeepSeekError as exc:
        result.update({"status": "failed", "error_code": exc.code})
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
