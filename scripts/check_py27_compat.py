# -*- coding: utf-8 -*-
"""Lightweight Python 2.7 compatibility scanner for critical runtime files.

Phase 20-A2 expands coverage to include ReAct path (agent_loop, tool_dispatcher),
context modules, and MCP/data-source runtime.  Files that are intentionally
Python 3-only are declared in EXEMPTIONS with an explicit reason. The gate will:

- fail on findings from non-exempted files.
- keep exempted files visible as "exempt" warnings so their status is auditable.
"""
from __future__ import unicode_literals

import os
import re
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


def _p(*parts):
    return os.path.join(ROOT, *parts)


# Files that MUST stay Python 2.7 compatible.
DEFAULT_PATHS = [
    _p('src', 'agent_facade.py'),
    _p('src', 'agent_harness.py'),
    _p('src', 'schemas.py'),
    _p('src', 'contracts.py'),
    _p('src', 'router_core.py'),
    _p('src', 'dag_routing.py'),
    _p('src', 'observability.py'),
    _p('src', 'governance.py'),
    _p('src', 'execution_engine.py'),
    _p('src', 'memory_contracts.py'),
    _p('src', 'task_anchor.py'),
    _p('src', 'memory_policy.py'),
    _p('src', 'memory_store.py'),
    _p('src', 'result_cache.py'),
    _p('src', 'chart_policy.py'),
    _p('src', 'report_generator.py'),
    _p('src', 'report_templates.py'),
    _p('src', 'semantic_registry.py'),
    _p('src', 'semantic_utils.py'),
    _p('src', 'task_decomposer.py'),
    _p('src', 'result_merger.py'),
    _p('src', 'analysis_strategies.py'),
    _p('src', 'anomaly_detection.py'),
    _p('src', 'gmv_driver_analysis.py'),
    _p('src', 'contribution_analysis.py'),
    _p('src', 'intent_engine.py'),
    _p('src', 'data_source_gateway.py'),
    _p('src', 'mcp_adapter.py'),
    _p('src', 'mcp_stdio_server.py'),
    _p('src', 'context_manager.py'),
    _p('src', 'context', '__init__.py'),
    _p('src', 'context', 'token_budget.py'),
    _p('src', 'context', 'prefix_cache.py'),
    _p('src', 'context', 'result_trimmer.py'),
    _p('src', 'context', 'message_compactor.py'),
    _p('src', 'context', 'context_budget.py'),
    _p('src', 'context', 'rolling_summary.py'),
    _p('scripts', 'run_agent_harness.py'),
    _p('scripts', 'run_harness_gate.py'),
    _p('scripts', 'diff_harness_snapshot.py'),
    _p('scripts', 'update_harness_snapshots.py'),
]

# Files that are known Python 3-only and are being tracked for future migration.
# The gate will emit a warning per exempted file but will NOT fail on rule hits.
EXEMPTIONS = {
    _p('src', 'agent_loop.py'): 'ReAct experimental runtime; scheduled for Phase 21 governance rewrite.',
    _p('src', 'tool_dispatcher.py'): 'ReAct tool dispatcher; scheduled for Phase 21 rewrite (dataclass/f-string).',
}


TRIPLE_STRING_RE = re.compile(r'(?s)(""".*?"""|\'\'\'.*?\'\'\')')


RULES = [
    ('future_annotations', re.compile(r'^\s*from\s+__future__\s+import\s+annotations\b', re.M)),
    ('dataclass', re.compile(r'^\s*@dataclass\b|^\s*from\s+dataclasses\s+import\b|^\s*import\s+dataclasses\b', re.M)),
    ('f_string', re.compile(r'(^|[^A-Za-z0-9_])(?:f|F)([\"\"])', re.M)),
    ('function_annotation', re.compile(r'^\s*def\s+\w+\s*\([^\)]*:\s*[^\),]+', re.M)),
    ('return_annotation', re.compile(r'^\s*def\s+\w+\s*\([^\)]*\)\s*->\s*', re.M)),
    ('variable_annotation', re.compile(
        r'^\s*(?!(?:if|elif|else|try|except|finally|for|while|with|def|class|return|yield|raise|import|from|pass|break|continue|global|nonlocal|assert|lambda|and|or|not|in|is)\b)'
        r'[A-Za-z_][A-Za-z0-9_]*\s*:\s*[A-Za-z_][^=\n]*=', re.M)),
    ('pathlib_import', re.compile(r'^\s*from\s+pathlib\s+import\b|^\s*import\s+pathlib\b', re.M)),
    ('urllib_request_py3', re.compile(r'^\s*import\s+urllib\.request\b|^\s*from\s+urllib\.request\s+import\b', re.M)),
]


def _read(path):
    with open(path, 'rb') as f:
        data = f.read()
    try:
        return data.decode('utf-8')
    except Exception:
        return data.decode('utf-8', 'ignore')


def scan(paths):
    """Scan the given paths, returning enforcement-level findings only.

    Exempted paths are skipped here so the gate does not fail; callers that
    want a full audit should call ``scan_with_exemptions``.
    """
    findings = []
    for path in paths:
        if path in EXEMPTIONS:
            continue
        if not os.path.exists(path):
            findings.append({'path': path, 'rule': 'missing_file', 'line': 0, 'text': ''})
            continue
        text = TRIPLE_STRING_RE.sub(lambda m: '\n' * m.group(0).count('\n'), _read(path))
        for rule, pattern in RULES:
            for match in pattern.finditer(text):
                line = text[:match.start()].count('\n') + 1
                sample = text.splitlines()[line - 1].strip() if text.splitlines() else ''
                findings.append({'path': path, 'rule': rule, 'line': line, 'text': sample})
    return findings


def scan_with_exemptions(paths):
    """Return (findings, exempted_findings) so callers can audit both."""
    enforced = scan(paths)
    exempted = []
    for path in paths:
        if path not in EXEMPTIONS:
            continue
        if not os.path.exists(path):
            exempted.append({'path': path, 'rule': 'missing_file', 'line': 0, 'text': '', 'reason': EXEMPTIONS[path]})
            continue
        text = TRIPLE_STRING_RE.sub(lambda m: '\n' * m.group(0).count('\n'), _read(path))
        hit_rules = set()
        for rule, pattern in RULES:
            if pattern.search(text):
                hit_rules.add(rule)
        if hit_rules:
            exempted.append({
                'path': path,
                'rule': ','.join(sorted(hit_rules)),
                'line': 0,
                'text': '',
                'reason': EXEMPTIONS[path],
            })
    return enforced, exempted


def main(argv=None):
    argv = argv or sys.argv[1:]
    paths = [os.path.abspath(p) for p in argv] if argv else (DEFAULT_PATHS + list(EXEMPTIONS.keys()))
    findings, exempted = scan_with_exemptions(paths)
    print('PY27_COMPAT checked=%d findings=%d exempted=%d' % (len(paths), len(findings), len(exempted)))
    for item in findings[:50]:
        rel = os.path.relpath(item['path'], ROOT)
        print('%s:%s %s %s' % (rel, item['line'], item['rule'], item['text']))
    for item in exempted[:50]:
        rel = os.path.relpath(item['path'], ROOT)
        print('EXEMPT %s rules=%s reason=%s' % (rel, item['rule'], item['reason']))
    return 1 if findings else 0


if __name__ == '__main__':
    sys.exit(main())
