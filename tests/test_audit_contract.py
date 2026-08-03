# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); SRC = os.path.join(ROOT, "src")
if SRC not in sys.path: sys.path.insert(0, SRC)
from audit_contract import build_governance_audit_event


def test_governance_audit_contract_has_required_fields():
    event = build_governance_audit_event({"user_id": "u1", "role": "analyst", "tenant_id": "t1"},
                                         "GMV", "task", "trace", ["orders"], ["gmv"], "allowed", "ok")
    assert event["contract"] == "governance_audit_v1"
    assert event["user_id"] == "u1" and event["role"] == "analyst" and event["tenant_id"] == "t1"
    assert event["decision"] == "allowed" and event["tables"] == ["orders"]
