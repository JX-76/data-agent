# -*- coding: utf-8 -*-
"""Export badcases from benchmark or harness reports."""
from __future__ import print_function

import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
SRC = os.path.join(ROOT, 'src')
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from harness_snapshot import load_json, save_json
from badcase_store import extract_badcases_from_harness_report, extract_badcases_from_quality_report, summarize_badcases


def _load_report(path):
    return load_json(path)

def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        print('usage: py -3 scripts/export_badcases.py <report.json> [output.json]')
        return 1
    report_path = os.path.abspath(argv[0])
    output_path = os.path.abspath(argv[1]) if len(argv) > 1 else os.path.join(ROOT, 'harness', 'reports', 'badcases_latest.json')
    report = _load_report(report_path)
    rows = extract_badcases_from_harness_report(report)
    if not rows:
        rows = extract_badcases_from_quality_report(report)
    payload = {
        'source_report': report_path,
        'suite': report.get('suite'),
        'summary': summarize_badcases(rows),
        'rows': rows,
    }
    save_json(output_path, payload)
    print(json.dumps({'output_path': output_path, 'rows': len(rows), 'summary': payload['summary']}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
