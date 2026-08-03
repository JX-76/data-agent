# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, "src"))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from metadata_catalog import build_metadata_catalog, resolve_model_table
from schema_introspection import normalize_schema, table_columns


def test_normalize_schema_accepts_adapter_shape():
    schema = normalize_schema({"fct_orders": [{"name": "order_id", "type": "TEXT"}, {"name": "paid_at", "type": "TEXT"}]})
    assert schema["contract"] == "schema_introspection_v1"
    assert "fct_orders" in schema["tables"]
    assert "order_id" in table_columns(schema, "fct_orders")
    assert schema["fingerprint"]


def test_build_metadata_catalog_has_contract_and_fingerprint():
    catalog = build_metadata_catalog(physical_schema={"fct_orders": ["order_id", "paid_at", "sell_through", "channel"]})
    assert catalog["contract"] == "metadata_catalog_v1"
    assert catalog["semantic_version"]
    assert catalog["fingerprint"]
    assert "gmv" in catalog["metrics"]
    assert resolve_model_table(catalog, "order_detail") == "fct_orders"


if __name__ == "__main__":
    test_normalize_schema_accepts_adapter_shape()
    test_build_metadata_catalog_has_contract_and_fingerprint()
    print("All metadata_catalog R8 tests passed!")
