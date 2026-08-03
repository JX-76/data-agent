# -*- coding: utf-8 -*-
"""Versioned semantic-layer registry.

This module provides a lightweight indirection layer over the static semantic
YAML files so the agent can evolve toward runtime updates without hard-coding
business semantics into routing/execution entrypoints.
"""

import copy

from semantic_utils import load_semantic_layer, yaml


def _fallback_semantic_payload():
    return {
        "metrics": {
            "gmv": {"id": "gmv", "name": "GMV", "description": "已支付订单成交总额，未扣除退款。", "unit": "CNY", "allowed_dimensions": ["date", "channel", "region", "category"], "default_filters": ["order_status paid/completed"]},
            "order_count": {"id": "order_count", "name": "订单数", "description": "有效订单数量。", "unit": "count", "allowed_dimensions": ["date", "channel", "region", "category"]},
            "aov": {"id": "aov", "name": "客单价", "description": "GMV / 订单数。", "unit": "CNY", "allowed_dimensions": ["date", "channel", "region", "category"]},
            "avg_price": {"id": "avg_price", "name": "商品均价", "description": "商品平均单价。", "unit": "CNY", "allowed_dimensions": ["date", "category"]},
            "conversion_rate": {"id": "conversion_rate", "name": "转化率", "description": "转化事件数 / 访问或曝光基数。", "unit": "%", "allowed_dimensions": ["date", "channel", "region", "category"]},
            "roi": {"id": "roi", "name": "ROI", "description": "投资回报率。", "unit": "ratio", "allowed_dimensions": ["date", "channel", "region", "category"]},
            "cpa": {"id": "cpa", "name": "CPA", "description": "单次获客成本。", "unit": "CNY", "allowed_dimensions": ["date", "channel", "region", "category"]},
            "ctr": {"id": "ctr", "name": "CTR", "description": "点击率。", "unit": "%", "allowed_dimensions": ["date", "channel", "region", "category"]},
            "impressions": {"id": "impressions", "name": "曝光量", "description": "广告或内容曝光次数。", "unit": "count", "allowed_dimensions": ["date", "channel", "region", "category"]},
            # 扩展指标：用户分析
            "user_count": {"id": "user_count", "name": "用户数", "description": "去重活跃用户数。", "unit": "count", "allowed_dimensions": ["date", "channel", "region", "user_type"]},
            "new_users": {"id": "new_users", "name": "新增用户", "description": "首次下单用户数。", "unit": "count", "allowed_dimensions": ["date", "channel", "region"]},
            "user_ltv": {"id": "user_ltv", "name": "用户LTV", "description": "用户生命周期价值。", "unit": "CNY", "allowed_dimensions": ["date", "channel", "region", "user_type"]},
            "repurchase_rate": {"id": "repurchase_rate", "name": "复购率", "description": "重复购买用户占比。", "unit": "%", "allowed_dimensions": ["date", "channel", "region"]},
            # 扩展指标：产品分析
            "product_sales": {"id": "product_sales", "name": "产品销量", "description": "产品销售数量。", "unit": "count", "allowed_dimensions": ["date", "channel", "category", "product_name"]},
            "sku_count": {"id": "sku_count", "name": "SKU数", "description": "销售SKU数量。", "unit": "count", "allowed_dimensions": ["date", "channel", "category"]},
            # 扩展指标：营销分析
            "marketing_spend": {"id": "marketing_spend", "name": "营销费用", "description": "营销活动总花费。", "unit": "CNY", "allowed_dimensions": ["date", "campaign", "ad_channel"]},
            # 扩展指标：供应链分析
            "fulfillment_rate": {"id": "fulfillment_rate", "name": "履约率", "description": "按时发货订单占比。", "unit": "%", "allowed_dimensions": ["date", "warehouse", "logistics_provider"]},
            "return_rate": {"id": "return_rate", "name": "退货率", "description": "退货订单占比。", "unit": "%", "allowed_dimensions": ["date", "category", "warehouse"]},
            "avg_delivery_days": {"id": "avg_delivery_days", "name": "平均配送天数", "description": "从下单到签收平均天数。", "unit": "days", "allowed_dimensions": ["date", "warehouse", "logistics_provider", "region"]},
            "inventory_turnover": {"id": "inventory_turnover", "name": "库存周转率", "description": "销售成本 / 平均库存。", "unit": "times", "allowed_dimensions": ["date", "category", "warehouse"]},
        },
        "dimensions": {
            "date": {"id": "date", "name": "日期"},
            "channel": {"id": "channel", "name": "渠道"},
            "region": {"id": "region", "name": "区域"},
            "category": {"id": "category", "name": "品类"},
            # 扩展维度：用户分析
            "user_type": {"id": "user_type", "name": "用户类型"},
            "user_age_group": {"id": "user_age_group", "name": "用户年龄段"},
            "user_gender": {"id": "user_gender", "name": "用户性别"},
            "user_city": {"id": "user_city", "name": "用户城市"},
            # 扩展维度：产品分析
            "product_name": {"id": "product_name", "name": "商品名称"},
            "brand": {"id": "brand", "name": "品牌"},
            "price_range": {"id": "price_range", "name": "价格区间"},
            # 扩展维度：营销分析
            "campaign": {"id": "campaign", "name": "活动"},
            "ad_channel": {"id": "ad_channel", "name": "广告渠道"},
            # 扩展维度：供应链分析
            "warehouse": {"id": "warehouse", "name": "仓库"},
            "logistics_provider": {"id": "logistics_provider", "name": "物流商"},
            "payment_method": {"id": "payment_method", "name": "支付方式"},
        },
        "models": {
            "order_detail": {"id": "order_detail", "name": "订单明细模型", "visible_dimensions": ["date", "channel", "region"]},
            "user_summary": {"id": "user_summary", "name": "用户概览模型", "visible_dimensions": ["date", "channel"]},
            "product_analysis": {"id": "product_analysis", "name": "商品分析模型", "visible_dimensions": ["date", "channel", "category"]},
        },
        "tables": {},
        "joins": {},
    }



class SemanticSnapshot(object):
    def __init__(self, version, payload, parent_version=None, notes=""):
        self.version = version
        self.payload = payload
        self.parent_version = parent_version
        self.notes = notes


class SemanticValidationResult(object):
    def __init__(self, ok=True, errors=None, warnings=None, metadata=None):
        self.ok = ok
        self.errors = errors or []
        self.warnings = warnings or []
        self.metadata = metadata or {}

    def to_dict(self):
        return {
            "ok": self.ok,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "metadata": copy.deepcopy(self.metadata),
        }


class SemanticRegistry(object):
    """In-memory semantic registry with snapshot / rollback semantics."""

    def __init__(self):
        self.history = []
        if yaml:
            try:
                payload = load_semantic_layer(table_index=True)
            except Exception:
                payload = _fallback_semantic_payload()
        else:
            payload = _fallback_semantic_payload()
        self.current = SemanticSnapshot(
            version="v1",
            payload=payload,
            parent_version=None,
            notes="bootstrap",
        )

    def snapshot(self):
        return copy.deepcopy(self.current)

    def get(self):
        return copy.deepcopy(self.current.payload)

    def get_version(self):
        return self.current.version

    def update(self, payload, notes=""):
        next_version = "v%d" % (len(self.history) + 2)
        snap = SemanticSnapshot(
            version=next_version,
            payload=copy.deepcopy(payload),
            parent_version=self.current.version,
            notes=notes,
        )
        self.history.append(copy.deepcopy(self.current))
        self.current = snap
        return self.snapshot()

    def rollback(self):
        if not self.history:
            return self.snapshot()
        self.current = self.history.pop()
        return self.snapshot()

    def get_metric(self, metric_id):
        return copy.deepcopy((self.current.payload.get("metrics") or {}).get(metric_id))

    def get_dimension(self, dimension_id):
        return copy.deepcopy((self.current.payload.get("dimensions") or {}).get(dimension_id))

    def get_model(self, model_id):
        return copy.deepcopy((self.current.payload.get("models") or {}).get(model_id))

    def attach_physical_schema(self, schema, notes="physical schema introspection"):
        """Attach an introspected physical schema as a readonly semantic view.

        This intentionally does not replace business semantics such as metrics,
        dimensions, or models. It only refreshes the payload["tables"] view so
        execution diagnostics can detect schema drift.
        """
        payload = self.get()
        payload["tables"] = copy.deepcopy(schema or {})
        return self.update(payload, notes=notes)

    def validate_schema_drift(self, plan):
        plan = plan or {}
        warnings = []
        payload = self.current.payload or {}
        tables = payload.get("tables") or {}
        model_id = plan.get("model") or "order_detail"
        metric_id = plan.get("metric") or "gmv"
        dimensions = plan.get("dimensions") or []
        model = self.get_model(model_id) or {}
        table_name = model.get("table") or model.get("base_table") or model_id
        table = tables.get(table_name)
        # Physical schema may be keyed by either model id or physical table name,
        # depending on how the introspector attached it. Fall back to the model
        # id so callers can attach a schema keyed by "order_detail" and still
        # get column-level drift warnings.
        if table is None and table_name != model_id:
            table = tables.get(model_id)
            if table is not None:
                table_name = model_id

        if not tables:
            return warnings
        if table is None:
            warnings.append({"code": "schema_table_missing", "model": model_id, "table": table_name})
            return warnings

        columns = table.get("columns") if isinstance(table, dict) else table
        column_names = set()
        for col in columns or []:
            if isinstance(col, dict):
                column_names.add(col.get("name"))
            else:
                column_names.add(col)

        metric = self.get_metric(metric_id) or {}
        metric_field = metric.get("field") or metric.get("source_field") or metric.get("column")
        if metric_field and metric_field not in column_names:
            warnings.append({"code": "schema_metric_field_missing", "metric": metric_id, "field": metric_field, "table": table_name})
        for dim in dimensions:
            dim_def = self.get_dimension(dim) or {}
            dim_field = dim_def.get("field") or dim_def.get("source_field") or dim_def.get("column") or dim
            if dim_field not in column_names:
                warnings.append({"code": "schema_dimension_field_missing", "dimension": dim, "field": dim_field, "table": table_name})
        return warnings

    def list_metrics(self):
        return sorted((self.current.payload.get("metrics") or {}).keys())

    def list_dimensions(self):
        return sorted((self.current.payload.get("dimensions") or {}).keys())

    def metric_metadata(self, metric_id):
        metric = self.get_metric(metric_id)
        if not metric:
            return None
        return {
            "id": metric_id,
            "name": metric.get("name", metric_id),
            "description": metric.get("description", ""),
            "expression": metric.get("expression"),
            "base_table": metric.get("base_table"),
            "time_field": metric.get("time_field"),
            "default_filters": list(metric.get("default_filters", []) or []),
            "allowed_dimensions": list(metric.get("allowed_dimensions", []) or []),
            "unit": metric.get("unit"),
            "note": metric.get("note", ""),
            "synonyms": list(metric.get("synonyms", []) or []),
        }

    def validate_plan(self, plan):
        plan = plan or {}
        errors = []
        warnings = []
        metric_id = plan.get("metric") or "gmv"
        model_id = plan.get("model") or "order_detail"
        dimensions = plan.get("dimensions") or []

        metric = self.get_metric(metric_id)
        model = self.get_model(model_id)
        if not metric:
            errors.append({"code": "unknown_metric", "field": "metric", "value": metric_id})
        if not model:
            errors.append({"code": "unknown_model", "field": "model", "value": model_id})

        allowed_by_metric = set(metric.get("allowed_dimensions", []) or []) if metric else set()
        visible_by_model = set(model.get("visible_dimensions", []) or []) if model else set()
        dimension_meta = []
        for dim in dimensions:
            dim_def = self.get_dimension(dim)
            if not dim_def:
                errors.append({"code": "unknown_dimension", "field": "dimensions", "value": dim})
                continue
            dimension_meta.append(dim_def)
            if allowed_by_metric and dim not in allowed_by_metric:
                errors.append({"code": "dimension_not_allowed_for_metric", "field": "dimensions", "value": dim, "metric": metric_id})
            if visible_by_model and dim not in visible_by_model:
                errors.append({"code": "dimension_not_visible_in_model", "field": "dimensions", "value": dim, "model": model_id})

        warnings.extend(self.validate_schema_drift(plan))
        metadata = {
            "semantic_version": self.get_version(),
            "metric": self.metric_metadata(metric_id),
            "model": model,
            "dimensions": dimension_meta,
            "schema_drift_warnings": list(warnings),
        }
        return SemanticValidationResult(ok=len(errors) == 0, errors=errors, warnings=warnings, metadata=metadata)


_REGISTRY = SemanticRegistry()


def get_semantic_registry():
    return _REGISTRY


def get_semantic_view():
    return _REGISTRY.get()


def get_semantic_version():
    return _REGISTRY.get_version()


def get_metric_definition(metric_id):
    return _REGISTRY.get_metric(metric_id)


def get_dimension_definition(dimension_id):
    return _REGISTRY.get_dimension(dimension_id)


def get_model_definition(model_id):
    return _REGISTRY.get_model(model_id)


def get_metric_metadata(metric_id):
    return _REGISTRY.metric_metadata(metric_id)


def validate_plan_semantics(plan):
    return _REGISTRY.validate_plan(plan)


def attach_physical_schema(schema, notes="physical schema introspection"):
    return _REGISTRY.attach_physical_schema(schema, notes=notes)


def validate_schema_drift(plan):
    return _REGISTRY.validate_schema_drift(plan)


__all__ = [
    "SemanticValidationResult",
    "SemanticSnapshot",
    "SemanticRegistry",
    "get_semantic_registry",
    "get_semantic_view",
    "get_semantic_version",
    "get_metric_definition",
    "get_dimension_definition",
    "get_model_definition",
    "get_metric_metadata",
    "validate_plan_semantics",
    "attach_physical_schema",
    "validate_schema_drift",
]
