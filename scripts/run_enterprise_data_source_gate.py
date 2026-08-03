# -*- coding: utf-8 -*-
"""Focused quality gate for governed enterprise data-source integration."""
from __future__ import print_function

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEST = os.path.join(ROOT, "tests", "test_enterprise_data_source.py")


def main():
    env = dict(os.environ)
    env["PYTHONPATH"] = os.path.join(ROOT, "src") + os.pathsep + env.get("PYTHONPATH", "")
    # The checked-in test package is legacy-style; third-party collection
    # plugins can be version-incompatible with the local pytest runtime.
    env.setdefault("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    command = [sys.executable, "-m", "pytest", "-q", TEST]
    print("[enterprise-data-source-gate] %s" % " ".join(command))
    try:
        return subprocess.call(command, cwd=ROOT, env=env)
    except OSError as exc:
        print("[enterprise-data-source-gate] failed to start pytest: %s" % exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
