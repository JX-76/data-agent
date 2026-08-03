# -*- coding: utf-8 -*-
"""Gate for the public provider-configuration entrypoint.

This gate verifies that GitHub/download users get a safe configuration slot
without committed local secrets, and that the provider config contract tests pass.
"""
from __future__ import print_function, unicode_literals

import json
import os
import subprocess
import sys
import traceback

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


def _python_cmd():
    return sys.executable or "python"


def _run_pytest(results):
    env = dict(os.environ)
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    cmd = [_python_cmd(), "-m", "pytest", "tests/test_user_provider_config.py", "tests/test_deepseek_adapter.py", "-q"]
    try:
        proc = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        out, _ = proc.communicate()
        if not isinstance(out, str):
            out = out.decode("utf-8", "replace")
        results.append({"name": "pytest_provider_contracts", "passed": proc.returncode == 0,
                        "returncode": proc.returncode, "output_tail": out[-2000:]})
    except Exception:
        results.append({"name": "pytest_provider_contracts", "passed": False,
                        "error": traceback.format_exc()})


def _read(path):
    handle = open(path, "r", encoding="utf-8")
    try:
        return handle.read()
    finally:
        handle.close()


def _check_public_files(results):
    try:
        env = _read(os.path.join(ROOT, ".env.example"))
        launcher = _read(os.path.join(ROOT, "scripts", "start_local.bat"))
        readme = _read(os.path.join(ROOT, "README.md"))
        gitignore = _read(os.path.join(ROOT, ".gitignore"))
        assert "DEEPSEEK_API_KEY=" in env
        assert "DEEPSEEK_API_KEY=sk-" not in env
        assert "your-deepseek-api-key" not in env
        assert ".data_agent_provider_config.json" in env
        assert ".env" in gitignore
        assert ".env.*" in gitignore
        assert "!.env.example" in gitignore
        assert ".data_agent_provider_config.json" in gitignore
        assert "模型/API 设置" in launcher
        assert "模型/API 设置" in readme
        assert ".env.example" in readme
        assert "不要提交到 Git" in readme
        results.append({"name": "public_entrypoint_has_blank_user_config_slots", "passed": True})
    except Exception:
        results.append({"name": "public_entrypoint_has_blank_user_config_slots", "passed": False,
                        "error": traceback.format_exc()})


def main():
    results = []
    _run_pytest(results)
    _check_public_files(results)
    report = {"contract": "provider_configuration_gate_v1", "total": len(results),
              "failed": sum(1 for item in results if not item["passed"]), "results": results}
    report["passed"] = report["failed"] == 0
    print("PROVIDER_CONFIGURATION_GATE " + json.dumps(
        {"passed": report["passed"], "total": report["total"], "failed": report["failed"]}, sort_keys=True))
    if not report["passed"]:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
