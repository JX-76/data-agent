# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.join(ROOT, 'src'))

from harness_coverage import calculate_coverage, load_jsonl

CASES_DIR = os.path.join(ROOT, 'harness', 'cases')
DEFAULT_SUITE_PATHS = {
    'base': os.path.join(CASES_DIR, 'base.jsonl'),
    'phase8': os.path.join(CASES_DIR, 'phase8.jsonl'),
    'ecommerce': os.path.join(CASES_DIR, 'ecommerce_smoke.jsonl'),
}


def _resolve_path(suite_or_path):
    if os.path.isfile(suite_or_path):
        return suite_or_path
    path = DEFAULT_SUITE_PATHS.get(suite_or_path)
    if path and os.path.exists(path):
        return path
    if suite_or_path == 'all':
        return None
    raise IOError('Unknown suite or missing case file: %s' % suite_or_path)


def _load_cases(suite_or_path):
    path = _resolve_path(suite_or_path)
    if path:
        return load_jsonl(path)
    cases = []
    for name in ['base', 'phase8', 'ecommerce']:
        p = DEFAULT_SUITE_PATHS.get(name)
        if p and os.path.exists(p):
            cases.extend(load_jsonl(p))
    return cases


def main(argv=None):
    argv = argv or sys.argv[1:]
    suite = argv[0] if argv else 'all'
    cases = _load_cases(suite)
    coverage = calculate_coverage(cases)
    print('HARNESS_COVERAGE suite=%s total=%d' % (suite, coverage.get('total', 0)))
    print('COVERAGE %s' % json.dumps(coverage, ensure_ascii=False, sort_keys=True, default=str))
    return 0


if __name__ == '__main__':
    sys.exit(main())
