# -*- coding: utf-8 -*-
"""Shared semantic-layer utilities."""

import codecs
import os

try:
    import yaml as _yaml
except ImportError:  # pragma: no cover - depends on optional environment
    _yaml = None

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DANGEROUS = [u"删除", u"更新", u"修改", u"写入", u"drop", u"delete", u"update", u"insert", u"alter", u"truncate", u"删库",
             u"改成", u"调价", u"暂停", u"刷单", u"刷一下", u"发短信"]
SENSITIVE = [u"salary", u"user_phone", u"phone", u"id_card", u"身份证", u"手机号", u"工资"]


_METRICS_FALLBACK = {
    "version": "1.0",
    "metrics": [
        {
            "id": "gmv",
            "name": "GMV",
            "description": "已支付订单成交总额，未扣除退款。",
            "synonyms": ["GMV", "销售额", "成交额", "业绩", "revenue"],
            "expression": "SUM(fct_orders.sell_through)",
            "base_table": "fct_orders",
            "time_field": "fct_orders.paid_at",
            "default_filters": ["fct_orders.order_status IN ('paid', 'completed')"],
            "allowed_dimensions": ["date", "channel", "region", "category"],
            "unit": "CNY",
            "note": "GMV 未扣除退款。",
        },
        {
            "id": "order_count",
            "name": "订单数",
            "description": "有效订单数量。",
            "synonyms": ["订单数", "交易笔数", "单量", "orders"],
            "expression": "COUNT(DISTINCT fct_orders.order_id)",
            "base_table": "fct_orders",
            "time_field": "fct_orders.paid_at",
            "default_filters": ["fct_orders.order_status IN ('paid', 'completed')"],
            "allowed_dimensions": ["date", "channel", "region", "category"],
            "unit": "count",
        },
        {
            "id": "aov",
            "name": "客单价",
            "description": "GMV / 订单数。",
            "synonyms": ["客单价", "AOV", "平均订单金额"],
            "expression": "SUM(fct_orders.sell_through) / NULLIF(COUNT(DISTINCT fct_orders.order_id), 0)",
            "base_table": "fct_orders",
            "time_field": "fct_orders.paid_at",
            "default_filters": ["fct_orders.order_status IN ('paid', 'completed')"],
            "allowed_dimensions": ["date", "channel", "region", "category"],
            "unit": "CNY",
        },
        {
            "id": "avg_price",
            "name": "商品均价",
            "description": "商品平均单价。",
            "synonyms": ["均价", "平均价格", "商品均价", "average price"],
            "expression": "AVG(dim_product.unit_price)",
            "base_table": "fct_orders",
            "time_field": "fct_orders.paid_at",
            "default_filters": ["fct_orders.order_status IN ('paid', 'completed')"],
            "allowed_dimensions": ["date", "category"],
            "unit": "CNY",
        },
        {
            "id": "conversion_rate",
            "name": "转化率",
            "description": "转化事件数 / 访问或曝光基数，用于衡量业务漏斗转化效果。",
            "synonyms": ["转化率", "转化", "CVR", "conversion_rate", "conversion rate"],
            "expression": "COUNT(DISTINCT fct_orders.order_id) * 1.0 / NULLIF(COUNT(DISTINCT fct_orders.user_id), 0)",
            "base_table": "fct_orders",
            "time_field": "fct_orders.paid_at",
            "default_filters": ["fct_orders.order_status IN ('paid', 'completed')"],
            "allowed_dimensions": ["date", "channel", "region", "category"],
            "unit": "%",
            "note": "MVP 口径使用订单用户转化近似，生产环境应绑定明确漏斗事件表。",
        },
        {
            "id": "roi",
            "name": "ROI",
            "description": "投资回报率。",
            "synonyms": ["ROI", "roi", "投产比", "投入产出比"],
            "expression": "SUM(fct_orders.sell_through) / NULLIF(SUM(fct_orders.ad_cost), 0)",
            "base_table": "fct_orders",
            "time_field": "fct_orders.paid_at",
            "default_filters": ["fct_orders.order_status IN ('paid', 'completed')"],
            "allowed_dimensions": ["date", "channel", "region", "category"],
            "unit": "ratio",
        },
        {
            "id": "cpa",
            "name": "CPA",
            "description": "单次获客成本。",
            "synonyms": ["CPA", "cpa", "获客成本", "拉新成本"],
            "expression": "SUM(fct_orders.ad_cost) / NULLIF(COUNT(DISTINCT fct_orders.user_id), 0)",
            "base_table": "fct_orders",
            "time_field": "fct_orders.paid_at",
            "default_filters": ["fct_orders.order_status IN ('paid', 'completed')"],
            "allowed_dimensions": ["date", "channel", "region", "category"],
            "unit": "CNY",
        },
        {
            "id": "ctr",
            "name": "CTR",
            "description": "点击率。",
            "synonyms": ["CTR", "ctr", "点击率", "点击转化率", "click through rate"],
            "expression": "COUNT(DISTINCT fct_events.click_id) * 1.0 / NULLIF(COUNT(DISTINCT fct_events.impression_id), 0)",
            "base_table": "fct_events",
            "time_field": "fct_events.event_time",
            "default_filters": [],
            "allowed_dimensions": ["date", "channel", "region", "category"],
            "unit": "%",
        },
        {
            "id": "impressions",
            "name": "曝光量",
            "description": "广告或内容曝光次数。",
            "synonyms": ["曝光量", "展示量", "impressions", "展现量"],
            "expression": "COUNT(*)",
            "base_table": "fct_events",
            "time_field": "fct_events.event_time",
            "default_filters": [],
            "allowed_dimensions": ["date", "channel", "region", "category"],
            "unit": "count",
        },
        # -- 扩展指标：用户分析 --
        {
            "id": "user_count",
            "name": "用户数",
            "description": "去重活跃用户数。",
            "synonyms": ["用户数", "用户量", "UV", "活跃用户数", "访客数"],
            "expression": "COUNT(DISTINCT fct_orders.user_id)",
            "base_table": "fct_orders",
            "time_field": "fct_orders.paid_at",
            "default_filters": ["fct_orders.order_status IN ('paid', 'completed')"],
            "allowed_dimensions": ["date", "channel", "region", "user_type"],
            "unit": "count",
        },
        {
            "id": "new_users",
            "name": "新增用户",
            "description": "首次下单用户数。",
            "synonyms": ["新增用户", "新客", "新用户", "首次购买用户"],
            "expression": "COUNT(DISTINCT CASE WHEN fct_orders.is_first_order = 1 THEN fct_orders.user_id END)",
            "base_table": "fct_orders",
            "time_field": "fct_orders.paid_at",
            "default_filters": ["fct_orders.order_status IN ('paid', 'completed')"],
            "allowed_dimensions": ["date", "channel", "region"],
            "unit": "count",
        },
        {
            "id": "user_ltv",
            "name": "用户LTV",
            "description": "用户生命周期价值。",
            "synonyms": ["LTV", "用户价值", "生命周期价值", "用户终身价值"],
            "expression": "SUM(fct_orders.sell_through) / COUNT(DISTINCT fct_orders.user_id)",
            "base_table": "fct_orders",
            "time_field": "fct_orders.paid_at",
            "default_filters": ["fct_orders.order_status IN ('paid', 'completed')"],
            "allowed_dimensions": ["date", "channel", "region", "user_type"],
            "unit": "CNY",
        },
        {
            "id": "repurchase_rate",
            "name": "复购率",
            "description": "重复购买用户占比。",
            "synonyms": ["复购率", "重复购买率", "回头客比例"],
            "expression": "COUNT(DISTINCT CASE WHEN order_count > 1 THEN user_id END) * 1.0 / COUNT(DISTINCT user_id)",
            "base_table": "fct_orders",
            "time_field": "fct_orders.paid_at",
            "default_filters": ["fct_orders.order_status IN ('paid', 'completed')"],
            "allowed_dimensions": ["date", "channel", "region"],
            "unit": "%",
        },
        # -- 扩展指标：产品分析 --
        {
            "id": "product_sales",
            "name": "产品销量",
            "description": "产品销售数量。",
            "synonyms": ["销量", "销售数量", "件数", "销售件数"],
            "expression": "SUM(fct_orders.quantity)",
            "base_table": "fct_orders",
            "time_field": "fct_orders.paid_at",
            "default_filters": ["fct_orders.order_status IN ('paid', 'completed')"],
            "allowed_dimensions": ["date", "channel", "category", "product_name"],
            "unit": "count",
        },
        {
            "id": "sku_count",
            "name": "SKU数",
            "description": "销售SKU数量。",
            "synonyms": ["SKU数", "SKU数量", "商品种类数"],
            "expression": "COUNT(DISTINCT fct_orders.product_id)",
            "base_table": "fct_orders",
            "time_field": "fct_orders.paid_at",
            "default_filters": ["fct_orders.order_status IN ('paid', 'completed')"],
            "allowed_dimensions": ["date", "channel", "category"],
            "unit": "count",
        },
        # -- 扩展指标：营销分析 --
        {
            "id": "marketing_spend",
            "name": "营销费用",
            "description": "营销活动总花费。",
            "synonyms": ["营销费用", "广告费", "推广费", "投放费用", "marketing spend"],
            "expression": "SUM(fct_marketing.spend)",
            "base_table": "fct_marketing",
            "time_field": "fct_marketing.date",
            "allowed_dimensions": ["date", "campaign", "ad_channel"],
            "unit": "CNY",
        },
        # -- 扩展指标：供应链分析 --
        {
            "id": "fulfillment_rate",
            "name": "履约率",
            "description": "按时发货订单占比。",
            "synonyms": ["履约率", "发货率", "按时发货率"],
            "expression": "SUM(CASE WHEN fct_orders.shipped_on_time = 1 THEN 1 ELSE 0 END) * 1.0 / COUNT(*)",
            "base_table": "fct_orders",
            "time_field": "fct_orders.paid_at",
            "allowed_dimensions": ["date", "warehouse", "logistics_provider"],
            "unit": "%",
        },
        {
            "id": "return_rate",
            "name": "退货率",
            "description": "退货订单占比。",
            "synonyms": ["退货率", "退款率", "退换货率"],
            "expression": "SUM(CASE WHEN fct_orders.order_status = 'returned' THEN 1 ELSE 0 END) * 1.0 / COUNT(*)",
            "base_table": "fct_orders",
            "time_field": "fct_orders.paid_at",
            "allowed_dimensions": ["date", "category", "warehouse"],
            "unit": "%",
        },
        {
            "id": "avg_delivery_days",
            "name": "平均配送天数",
            "description": "从下单到签收平均天数。",
            "synonyms": ["配送天数", "物流时效", "配送时效", "平均配送时间"],
            "expression": "AVG(JULIANDAY(fct_orders.delivered_at) - JULIANDAY(fct_orders.paid_at))",
            "base_table": "fct_orders",
            "time_field": "fct_orders.paid_at",
            "allowed_dimensions": ["date", "warehouse", "logistics_provider", "region"],
            "unit": "days",
        },
        {
            "id": "inventory_turnover",
            "name": "库存周转率",
            "description": "销售成本 / 平均库存。",
            "synonyms": ["周转率", "库存周转", "库存周转率"],
            "expression": "SUM(fct_orders.cost) / NULLIF(AVG(dim_product.inventory), 0)",
            "base_table": "fct_orders",
            "time_field": "fct_orders.paid_at",
            "allowed_dimensions": ["date", "category", "warehouse"],
            "unit": "times",
            "note": "周转次数，越高越好。",
        },
    ],
}


_DIMENSIONS_FALLBACK = {
    "version": "1.0",
    "dimensions": [
        {
            "id": "date",
            "name": "日期",
            "field": "DATE(fct_orders.paid_at)",
            "table": "fct_orders",
            "synonyms": ["日期", "时间", "每天", "按天"],
        },
        {
            "id": "channel",
            "name": "渠道",
            "field": "fct_orders.channel",
            "table": "fct_orders",
            "synonyms": ["渠道", "来源"],
        },
        {
            "id": "region",
            "name": "区域",
            "field": "dim_store.region",
            "table": "dim_store",
            "join": "orders_to_store",
            "synonyms": ["区域", "地区", "大区"],
        },
        {
            "id": "category",
            "name": "品类",
            "field": "dim_product.category",
            "table": "dim_product",
            "join": "orders_to_product",
            "synonyms": ["品类", "类目", "分类", "商品品类"],
        },
        # -- 扩展维度：用户分析 --
        {
            "id": "user_type",
            "name": "用户类型",
            "field": "CASE WHEN fct_orders.is_first_order = 1 THEN '新客' ELSE '老客' END",
            "table": "fct_orders",
            "synonyms": ["用户类型", "新老客", "客户类型", "新客老客"],
        },
        {
            "id": "user_age_group",
            "name": "用户年龄段",
            "field": "CASE WHEN dim_user.age < 18 THEN '18岁以下' WHEN dim_user.age < 25 THEN '18-25岁' WHEN dim_user.age < 35 THEN '25-35岁' WHEN dim_user.age < 45 THEN '35-45岁' ELSE '45岁以上' END",
            "table": "dim_user",
            "join": "orders_to_user",
            "synonyms": ["年龄段", "年龄组", "用户年龄"],
        },
        {
            "id": "user_gender",
            "name": "用户性别",
            "field": "dim_user.gender",
            "table": "dim_user",
            "join": "orders_to_user",
            "synonyms": ["性别", "用户性别"],
        },
        {
            "id": "user_city",
            "name": "用户城市",
            "field": "dim_user.city",
            "table": "dim_user",
            "join": "orders_to_user",
            "synonyms": ["城市", "用户城市", "所在城市"],
        },
        # -- 扩展维度：产品分析 --
        {
            "id": "product_name",
            "name": "商品名称",
            "field": "dim_product.product_name",
            "table": "dim_product",
            "join": "orders_to_product",
            "synonyms": ["商品", "产品", "SKU", "商品名称", "产品名称"],
        },
        {
            "id": "brand",
            "name": "品牌",
            "field": "dim_product.brand",
            "table": "dim_product",
            "join": "orders_to_product",
            "synonyms": ["品牌", "商标", "牌子"],
        },
        {
            "id": "price_range",
            "name": "价格区间",
            "field": "CASE WHEN dim_product.unit_price < 50 THEN '0-50元' WHEN dim_product.unit_price < 100 THEN '50-100元' WHEN dim_product.unit_price < 200 THEN '100-200元' WHEN dim_product.unit_price < 500 THEN '200-500元' ELSE '500元以上' END",
            "table": "dim_product",
            "join": "orders_to_product",
            "synonyms": ["价格区间", "价格段", "价位"],
        },
        # -- 扩展维度：营销分析 --
        {
            "id": "campaign",
            "name": "活动",
            "field": "fct_marketing.campaign_name",
            "table": "fct_marketing",
            "join": "orders_to_marketing",
            "synonyms": ["活动", "campaign", "营销活动", "促销活动"],
        },
        {
            "id": "ad_channel",
            "name": "广告渠道",
            "field": "fct_marketing.channel",
            "table": "fct_marketing",
            "join": "orders_to_marketing",
            "synonyms": ["广告渠道", "投放渠道", "推广渠道"],
        },
        # -- 扩展维度：供应链分析 --
        {
            "id": "warehouse",
            "name": "仓库",
            "field": "dim_warehouse.warehouse_name",
            "table": "dim_warehouse",
            "join": "orders_to_warehouse",
            "synonyms": ["仓库", "配送中心", "仓储"],
        },
        {
            "id": "logistics_provider",
            "name": "物流商",
            "field": "fct_orders.logistics_provider",
            "table": "fct_orders",
            "synonyms": ["物流商", "快递公司", "物流"],
        },
        {
            "id": "payment_method",
            "name": "支付方式",
            "field": "fct_orders.payment_method",
            "table": "fct_orders",
            "synonyms": ["支付方式", "付款方式", "支付渠道"],
        },
    ],
}


_TABLES_FALLBACK = {
    "version": "1.0",
    "tables": [
        {
            "name": "fct_orders",
            "allowed": True,
            "primary_key": "order_id",
            "time_fields": ["paid_at"],
            "columns": ["order_id", "store_id", "product_id", "channel", "order_status", "sell_through", "paid_at"],
        },
        {
            "name": "dim_store",
            "allowed": True,
            "primary_key": "store_id",
            "columns": ["store_id", "region"],
        },
        {
            "name": "dim_product",
            "allowed": True,
            "primary_key": "product_id",
            "columns": ["product_id", "product_name", "category", "unit_price"],
        },
        # -- 扩展表 --
        {
            "name": "dim_user",
            "allowed": True,
            "primary_key": "user_id",
            "columns": ["user_id", "age", "gender", "city", "registration_date", "user_level", "user_source"],
        },
        {
            "name": "fct_marketing",
            "allowed": True,
            "primary_key": "marketing_id",
            "time_fields": ["date"],
            "columns": ["marketing_id", "order_id", "campaign_name", "channel", "ad_type", "creative_name", "spend", "impressions", "clicks", "date"],
        },
        {
            "name": "fct_traffic",
            "allowed": True,
            "primary_key": "traffic_id",
            "time_fields": ["date"],
            "columns": ["traffic_id", "user_id", "channel", "visits", "page_views", "bounce_rate", "date"],
        },
        {
            "name": "dim_warehouse",
            "allowed": True,
            "primary_key": "warehouse_id",
            "columns": ["warehouse_id", "warehouse_name", "region", "city", "warehouse_type", "capacity"],
        },
        {
            "name": "fct_inventory",
            "allowed": True,
            "primary_key": "inventory_id",
            "time_fields": ["date"],
            "columns": ["inventory_id", "product_id", "warehouse_id", "quantity", "cost", "date"],
        },
        {
            "name": "fct_logistics",
            "allowed": True,
            "primary_key": "logistics_id",
            "time_fields": ["created_at"],
            "columns": ["logistics_id", "order_id", "warehouse_id", "logistics_provider", "status", "created_at", "shipped_at", "delivered_at"],
        },
    ],
    "joins": [
        {
            "id": "orders_to_store",
            "left_table": "fct_orders",
            "right_table": "dim_store",
            "condition": "fct_orders.store_id = dim_store.store_id",
        },
        {
            "id": "orders_to_product",
            "left_table": "fct_orders",
            "right_table": "dim_product",
            "condition": "fct_orders.product_id = dim_product.product_id",
        },
        # -- 扩展 join --
        {
            "id": "orders_to_user",
            "left_table": "fct_orders",
            "right_table": "dim_user",
            "condition": "fct_orders.user_id = dim_user.user_id",
        },
        {
            "id": "orders_to_marketing",
            "left_table": "fct_orders",
            "right_table": "fct_marketing",
            "condition": "fct_orders.order_id = fct_marketing.order_id",
        },
        {
            "id": "orders_to_warehouse",
            "left_table": "fct_orders",
            "right_table": "dim_warehouse",
            "condition": "fct_orders.store_id = dim_warehouse.warehouse_id",
        },
        {
            "id": "product_to_inventory",
            "left_table": "dim_product",
            "right_table": "fct_inventory",
            "condition": "dim_product.product_id = fct_inventory.product_id",
        },
        {
            "id": "orders_to_logistics",
            "left_table": "fct_orders",
            "right_table": "fct_logistics",
            "condition": "fct_orders.order_id = fct_logistics.order_id",
        },
    ],
}



# Keep the public name `yaml` truthy in environments without PyYAML so callers
# that gate on `if yaml` can still build the semantic layer.
yaml = _yaml if _yaml is not None else object()


def load_yaml_rel(path):
    rel = path.replace("\\", "/")
    if _yaml is None:
        if rel.endswith("semantic/metrics.yaml"):
            return _METRICS_FALLBACK
        if rel.endswith("semantic/dimensions.yaml"):
            return _DIMENSIONS_FALLBACK
        if rel.endswith("semantic/tables.yaml"):
            return _TABLES_FALLBACK
        if rel.endswith("semantic/models.yaml"):
            return {
                "version": "1.0",
                "models": [
                    {
                        "id": "order_detail",
                        "name": "订单明细模型",
                        "description": "面向 AI 调用层的单表语义视图，一人一单粒度。",
                        "base_table": "fct_orders",
                        "joins": ["orders_to_store"],
                        "visible_dimensions": ["date", "channel", "region"],
                        "default_time_field": "fct_orders.paid_at",
                        "default_filters": ["fct_orders.order_status IN ('paid', 'completed')"],
                        "notes": ["Agent 只能看到这个逻辑单表视图，不直接面对底层 join。"],
                    },
                    {
                        "id": "user_summary",
                        "name": "用户概览模型",
                        "description": "面向用户口径确认与概览分析的第二语义视角。",
                        "base_table": "fct_orders",
                        "joins": ["orders_to_store"],
                        "visible_dimensions": ["date", "channel"],
                        "default_time_field": "fct_orders.paid_at",
                        "default_filters": ["fct_orders.order_status IN ('paid', 'completed')"],
                        "notes": ["用于验证 switch() 是否能真正切换语义视角。"],
                    },
                    {
                        "id": "product_analysis",
                        "name": "商品分析模型",
                        "description": "面向商品维度的分析视角，连接产品维表，支持品类分析。",
                        "base_table": "fct_orders",
                        "joins": ["orders_to_store", "orders_to_product"],
                        "visible_dimensions": ["date", "channel", "category"],
                        "default_time_field": "fct_orders.paid_at",
                        "default_filters": ["fct_orders.order_status IN ('paid', 'completed')"],
                        "notes": ["与 order_detail 不同：底层 join 了 dim_product，暴露 category 维度。", "真正不同的主题域，验证 switch() 跨视角能力。"],
                    },
                ],
            }
    with codecs.open(os.path.join(BASE, path), "r", encoding="utf-8") as f:
        return _yaml.safe_load(f.read()) if _yaml is not None else None


def index_by(items, key="id"):
    return dict((item[key], item) for item in items)


def _merge_yaml_lists(base, extended, key="metrics"):
    """Merge extended YAML items into base, deduplicating by 'id'."""
    base_items = base.get(key, []) if isinstance(base, dict) else (base or [])
    ext_items = extended.get(key, []) if isinstance(extended, dict) else (extended or [])
    seen = set()
    merged = []
    for item in base_items:
        item_id = item.get("id") if isinstance(item, dict) else None
        if item_id:
            seen.add(item_id)
        merged.append(item)
    for item in ext_items:
        item_id = item.get("id") if isinstance(item, dict) else None
        if item_id and item_id not in seen:
            seen.add(item_id)
            merged.append(item)
    return merged



def load_semantic_layer(table_index=False):
    # Load base YAML
    base_metrics = load_yaml_rel("semantic/metrics.yaml")
    base_dimensions = load_yaml_rel("semantic/dimensions.yaml")
    base_tables = load_yaml_rel("semantic/tables.yaml")
    base_models = load_yaml_rel("semantic/models.yaml")

    # Load extended YAML (merge if exists)
    ext_metrics = load_yaml_rel("semantic/metrics_extended.yaml")
    ext_dimensions = load_yaml_rel("semantic/dimensions_extended.yaml")
    ext_tables = load_yaml_rel("semantic/tables_extended.yaml")

    # Merge metrics
    merged_metrics_list = _merge_yaml_lists(base_metrics, ext_metrics, key="metrics")
    metrics = index_by(merged_metrics_list)

    # Merge dimensions
    merged_dims_list = _merge_yaml_lists(base_dimensions, ext_dimensions, key="dimensions")
    dimensions = index_by(merged_dims_list)

    # Merge tables
    merged_tables_list = _merge_yaml_lists(base_tables, ext_tables, key="tables")
    tables_cfg = {"tables": merged_tables_list, "joins": []}

    # Merge joins
    base_joins = base_tables.get("joins", []) if isinstance(base_tables, dict) else []
    ext_joins = ext_tables.get("joins", []) if isinstance(ext_tables, dict) else []
    merged_joins = _merge_yaml_lists({"joins": base_joins}, {"joins": ext_joins}, key="joins")
    tables_cfg["joins"] = merged_joins
    joins = index_by(merged_joins)

    models = index_by(base_models["models"])
    tables = dict((t["name"], t) for t in merged_tables_list) if table_index else tables_cfg
    return {
        "metrics": metrics,
        "dimensions": dimensions,
        "tables": tables,
        "joins": joins,
        "models": models,
    }



__all__ = ["BASE", "DANGEROUS", "SENSITIVE", "index_by", "load_semantic_layer", "load_yaml_rel", "yaml"]
