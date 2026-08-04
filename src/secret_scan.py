# -*- coding: utf-8 -*-
"""Lightweight secret leak scanner for release gates."""

import os
import re


PLACEHOLDER = r"(?!changeme|example|your[-_]|xxx|todo|\$\{|<|\s*$)"
SECRET_PATTERNS = [
    ("deepseek_key", re.compile(r"DEEPSEEK_API_KEY\s*=\s*['\"]?" + PLACEHOLDER + r"[A-Za-z0-9_\-]{16,}", re.I)),
    ("generic_secret", re.compile(r"(secret|token|api[_-]?key|password)\s*=\s*['\"]?" + PLACEHOLDER + r"[A-Za-z0-9_\-]{20,}", re.I)),
    ("private_key", re.compile(r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----")),
]

# Test fixtures contain deliberately fake credential-shaped values used to
# validate masking.  Release scanning focuses on shippable source/config/docs.
DEFAULT_EXCLUDES = set([".git", "__pycache__", ".pytest_cache", "node_modules", ".venv", "venv", "sessions", "downloads", "flywheel_data", "tests"])
TEXT_EXTS = set([".py", ".yml", ".yaml", ".json", ".env", ".md", ".txt", ".toml", ".sql"])
TEMPLATE_SUFFIXES = (".example", ".sample", ".template", ".tmpl", ".dist")


def scan_path(root, excludes=None):
    root = os.path.abspath(root)
    excludes = set(excludes or []) | DEFAULT_EXCLUDES
    findings = []
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in excludes]
        for filename in files:
            path = os.path.join(base, filename)
            low_name = filename.lower()
            # Local .env is explicitly gitignored and must never be treated as
            # release content.  Templates/examples are expected to hold
            # placeholders and are likewise not release secrets.
            if (low_name == ".env" or any(low_name.endswith(suffix) for suffix in TEMPLATE_SUFFIXES)
                    or low_name in ("tenants.example.json",)):
                continue
            ext = os.path.splitext(filename)[1].lower()
            if ext not in TEXT_EXTS and low_name != ".env":
                continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    lines = f.readlines()
            except TypeError:
                with open(path, "r") as f:
                    lines = f.readlines()
            except Exception:
                continue
            for idx, line in enumerate(lines, 1):
                for name, pattern in SECRET_PATTERNS:
                    if pattern.search(line):
                        findings.append({"type": name, "path": os.path.relpath(path, root), "line": idx})
    return findings


def assert_no_secrets(root):
    findings = scan_path(root)
    if findings:
        raise AssertionError("secret findings: %s" % findings[:10])
    return True


__all__ = ["scan_path", "assert_no_secrets", "SECRET_PATTERNS"]
