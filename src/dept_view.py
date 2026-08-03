"""Department View Isolation: multi-tenant integration for the DAG.

Provides per-department data views, metric aliases, and access control.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger("dept_view")


@dataclass
class DepartmentView:
    """A department's isolated view of data."""
    dept_id: str
    name: str
    allowed_models: list[str] = field(default_factory=list)
    allowed_metrics: list[str] = field(default_factory=list)
    metric_aliases: dict[str, str] = field(default_factory=dict)
    dimension_aliases: dict[str, str] = field(default_factory=dict)
    blocked_queries: list[str] = field(default_factory=list)


# ── Department Registry ──

_DEPT_VIEWS: dict[str, DepartmentView] = {}


def register_department(view: DepartmentView) -> None:
    """Register a department view."""
    _DEPT_VIEWS[view.dept_id] = view
    logger.info("dept_registered", dept_id=view.dept_id, name=view.name)


def get_department_view(dept_id: str) -> DepartmentView | None:
    """Get a department view by ID."""
    return _DEPT_VIEWS.get(dept_id)


def list_departments() -> list[str]:
    """List all registered department IDs."""
    return list(_DEPT_VIEWS.keys())


def is_model_allowed(dept_id: str, model: str) -> bool:
    """Check if a model is accessible by a department."""
    view = get_department_view(dept_id)
    if not view:
        return True  # Default: allow all
    return model in view.allowed_models


def is_metric_allowed(dept_id: str, metric: str) -> bool:
    """Check if a metric is accessible by a department."""
    view = get_department_view(dept_id)
    if not view:
        return True
    return metric in view.allowed_metrics


def resolve_metric_alias(dept_id: str, alias: str) -> str:
    """Resolve a metric alias for a department."""
    view = get_department_view(dept_id)
    if not view:
        return alias
    return view.metric_aliases.get(alias, alias)


def resolve_dimension_alias(dept_id: str, alias: str) -> str:
    """Resolve a dimension alias for a department."""
    view = get_department_view(dept_id)
    if not view:
        return alias
    return view.dimension_aliases.get(alias, alias)


# ── Default Departments ──

SALES_DEPT = DepartmentView(
    dept_id="sales",
    name="销售部",
    allowed_models=["order_detail", "user_summary"],
    allowed_metrics=["gmv", "order_count", "aov", "conversion_rate"],
    metric_aliases={
        "销售额": "gmv",
        "订单": "order_count",
        "客单": "aov",
    },
    dimension_aliases={
        "渠道": "channel",
        "大区": "region",
        "品类": "category",
    },
)
register_department(SALES_DEPT)

MARKETING_DEPT = DepartmentView(
    dept_id="marketing",
    name="市场部",
    allowed_models=["order_detail", "product_analysis"],
    allowed_metrics=["gmv", "conversion_rate", "roi", "cac"],
    metric_aliases={
        "ROI": "roi",
        "CAC": "cac",
        "转化率": "conversion_rate",
    },
    dimension_aliases={
        "渠道": "channel",
        "活动": "campaign",
        "品类": "category",
    },
)
register_department(MARKETING_DEPT)

PRODUCT_DEPT = DepartmentView(
    dept_id="product",
    name="产品部",
    allowed_models=["product_analysis", "user_summary"],
    allowed_metrics=["gmv", "order_count", "retention_rate", "churn_rate"],
    metric_aliases={
        "留存": "retention_rate",
        "流失": "churn_rate",
    },
    dimension_aliases={
        "品类": "category",
        "SKU": "sku",
        "用户群": "user_segment",
    },
)
register_department(PRODUCT_DEPT)


# ── Integration Helpers ──

def filter_by_department(data: dict, dept_id: str) -> dict:
    """Filter query results by department access rules.
    
    Returns a copy of data with only allowed models/metrics.
    """
    view = get_department_view(dept_id)
    if not view:
        return data
    
    filtered = dict(data)
    
    # Filter models
    if "model" in filtered and not is_model_allowed(dept_id, filtered["model"]):
        filtered["model"] = "order_detail"  # Default fallback
        filtered["_dept_fallback"] = True
    
    # Filter metrics
    if "metric" in filtered and not is_metric_allowed(dept_id, filtered["metric"]):
        filtered["metric"] = "gmv"  # Default fallback
        filtered["_dept_fallback"] = True
    
    return filtered


def enrich_with_aliases(query: str, dept_id: str) -> str:
    """Replace department-specific aliases in a query with canonical names.
    
    Example:
        "销售部按渠道查销售额" → "sales按channel查gmv"
    """
    view = get_department_view(dept_id)
    if not view:
        return query
    
    enriched = query
    for alias, canonical in view.metric_aliases.items():
        enriched = enriched.replace(alias, canonical)
    for alias, canonical in view.dimension_aliases.items():
        enriched = enriched.replace(alias, canonical)
    
    return enriched
