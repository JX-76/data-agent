# -*- coding: utf-8 -*-
"""Tests for governance facade."""

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, "src"))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def test_governance_blocks_dangerous_query():
    from governance import GovernanceFacade

    gov = GovernanceFacade(tenant_manager=None)
    decision = gov.check_query("delete from orders", identity="u1", trace_id="t1")

    assert decision.allowed is False
    assert decision.action == "block"
    assert "dangerous" in decision.reason
    assert decision.severity == "high"
    assert decision.policy_id == "governance.dangerous_query"
    assert decision.decision_type == "dangerous_query"
    data = decision.to_dict()
    assert data["allowed"] is False
    assert data["metadata"]["trace_id"] == "t1"


def test_governance_allows_normal_query():
    from governance import GovernanceFacade

    gov = GovernanceFacade(tenant_manager=None)
    decision = gov.check_query("昨天GMV是多少？", identity="u1", trace_id="t1")

    assert decision.allowed is True
    assert decision.action == "allow"
    assert decision.policy_id == "governance.allow"
    assert decision.to_dict()["decision_type"] == "allow"


def test_governance_blocks_quota_exceeded():
    from governance import GovernanceFacade, _InMemoryQuota

    gov = GovernanceFacade(tenant_manager=None, quota=_InMemoryQuota(daily_limit=1))
    first = gov.check_query("昨天GMV是多少？", identity="quota-user", trace_id="t1")
    second = gov.check_query("今天GMV是多少？", identity="quota-user", trace_id="t2")

    assert first.allowed is True
    assert second.allowed is False
    assert second.action == "quota_exceeded"
    assert second.policy_id == "governance.quota"


def test_governance_blocks_denied_model():
    from governance import GovernanceFacade, _TablePermissions

    gov = GovernanceFacade(tenant_manager=None, table_permissions=_TablePermissions(denied_models=["user_summary"]))
    decision = gov.check_query("看用户概览", identity="u1", trace_id="t1", plan={"model": "user_summary"})

    assert decision.allowed is False
    assert decision.decision_type == "model_access_denied"
    assert decision.policy_id == "governance.table_permissions"


def test_governance_redacts_rows():
    from governance import GovernanceFacade

    gov = GovernanceFacade(tenant_manager=None)
    rows = [{"user_id": "USR0001", "email": "zhangsan@example.com", "gmv": 100}]
    masked = gov.redact_rows(rows)

    assert masked[0]["user_id"].startswith("USR")
    assert "@" in masked[0]["email"]
    assert masked[0]["gmv"] == 100


if __name__ == "__main__":
    test_governance_blocks_dangerous_query()
    test_governance_allows_normal_query()
    test_governance_blocks_quota_exceeded()
    test_governance_blocks_denied_model()
    test_governance_redacts_rows()
    print("All governance tests passed!")
