# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); SRC = os.path.join(ROOT, "src")
if SRC not in sys.path: sys.path.insert(0, SRC)
from permission_policy import PermissionPolicy


def test_explicit_allowed_table_and_field():
    p = PermissionPolicy()
    decision = p.evaluate({"user_id": "u", "role": "analyst", "permissions": {"allowed_tables": ["orders"], "allowed_fields": ["channel", "gmv"]}}, {"model": "orders", "metric": "gmv", "dimensions": ["channel"]})
    assert decision.allowed is True
    assert decision.decision == "allowed"


def test_denied_table_is_blocked():
    d = PermissionPolicy().evaluate({"permissions": {"denied_tables": ["orders"]}}, {"model": "orders"})
    assert d.allowed is False and d.decision == "blocked"


def test_denied_field_is_blocked():
    d = PermissionPolicy().evaluate({"permissions": {"denied_fields": ["region"]}}, {"model": "orders", "dimensions": ["region"]})
    assert d.allowed is False and "field_access_denied" in d.reason


def test_sensitive_request_requires_review_without_privileged_role():
    d = PermissionPolicy().evaluate({"role": "viewer"}, {"model": "orders"}, "导出 email 明细")
    assert d.decision == "pending_human_review" and d.requires_human_review


def test_tenant_filter_replaces_user_tenant_filter():
    plan = {"filters": [{"field": "tenant_id", "value": "other"}]}
    result = PermissionPolicy().inject_tenant_filter(plan, {"tenant_id": "tenant-a"})
    assert result["filters"] == [{"field": "tenant_id", "operator": "=", "value": "tenant-a", "source": "governance.tenant_filter"}]


def test_chinese_pii_aliases_require_human_review_for_non_privileged_roles():
    policy = PermissionPolicy()
    for query, expected_field in [
            ("导出所有用户的身份证号", "id_card"),
            ("导出客户手机号", "phone"),
            ("导出用户邮箱", "email"),
            ("导出客户联系地址", "address")]:
        decision = policy.evaluate({"role": "viewer"}, {"model": "orders"}, query)
        assert decision.allowed is False
        assert decision.decision == "pending_human_review"
        assert expected_field in decision.masked_fields


def test_privileged_pii_alias_is_allowed_only_with_masking_contract():
    decision = PermissionPolicy().evaluate({"role": "admin"}, {"model": "orders"}, "导出客户身份证号")
    assert decision.allowed is True
    assert decision.decision == "masked"
    assert decision.masked_fields == ["id_card"]
