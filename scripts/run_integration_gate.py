# -*- coding: utf-8 -*-
"""Fail-closed sandbox integration gate. It never opens a real connection."""
from __future__ import unicode_literals

import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.join(ROOT, 'src'))

from integration_contracts import ConnectorSpec, validate_real_connection
from sandbox_connectors import SandboxWarehouseConnector


def check(name, passed, detail=None):
    return {'name': name, 'passed': bool(passed), 'detail': detail or {}}


def run_gate():
    connector = SandboxWarehouseConnector()
    checks = []
    health = connector.health_check()
    checks.append(check('sandbox_health', health.get('status') == 'ok', health))
    schema = connector.describe_schema()
    checks.append(check('schema_contract', set(['orders', 'products', 'users']).issubset(set(schema.keys())), {'tables': sorted(schema.keys())}))
    result = connector.query('SELECT channel, SUM(gmv) AS gmv FROM orders GROUP BY channel ORDER BY channel', limit=10)
    checks.append(check('readonly_query_contract', result.get('row_count') == 3 and not result.get('error_type'), result))
    blocked = connector.query('DELETE FROM orders', limit=1)
    checks.append(check('write_rejected', blocked.get('error_type') == 'readonly_violation', blocked))
    real = ConnectorSpec('enterprise.placeholder', environment='real', read_only=True, approved=False)
    decision = validate_real_connection(real, {})
    checks.append(check('real_connection_fail_closed', not decision.get('allowed'), decision))
    passed = all(item['passed'] for item in checks)
    return {'suite': 'sandbox_integration', 'passed': passed, 'total': len(checks),
            'passed_count': len([item for item in checks if item['passed']]), 'checks': checks}


def main():
    report = run_gate()
    print('INTEGRATION_GATE suite=%s passed=%s total=%s passed_count=%s' % (
        report['suite'], report['passed'], report['total'], report['passed_count']))
    print('REPORT %s' % json.dumps(report, ensure_ascii=False, sort_keys=True, default=str))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
