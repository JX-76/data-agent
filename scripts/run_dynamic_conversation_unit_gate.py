# -*- coding: utf-8 -*-
from __future__ import print_function, unicode_literals
import json
import os
import sys
import traceback

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
TESTS = os.path.join(ROOT, "tests")
if TESTS not in sys.path:
    sys.path.insert(0, TESTS)


def main():
    import test_dynamic_conversation as module
    results = []
    for name in sorted(n for n in dir(module) if n.startswith("test_")):
        try:
            getattr(module, name)()
            results.append({"name": name, "passed": True})
        except Exception:
            results.append({"name": name, "passed": False, "error": traceback.format_exc()})
    report = {"contract": "dynamic_conversation_unit_gate_v1", "total": len(results),
              "failed": sum(1 for r in results if not r["passed"]), "results": results}
    report["passed"] = report["failed"] == 0
    print("DYNAMIC_CONVERSATION_UNIT_GATE " + json.dumps({"passed": report["passed"], "total": report["total"], "failed": report["failed"]}, sort_keys=True))
    if not report["passed"]:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
