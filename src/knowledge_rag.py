# -*- coding: utf-8 -*-
"""Knowledge documents for database/schema/method/scenario RAG.

This module turns stable data-agent knowledge into retrievable evidence:
- database metadata from semantic/tables.yaml
- metric calculation methods from semantic/metrics.yaml
- dimensions from semantic/dimensions.yaml
- business scenario cards from built-in SOPs

It deliberately does not vectorize raw fact rows. Real-time numbers should still
come from SQL/database execution; RAG provides the knowledge needed to plan and
explain the computation.
"""

from __future__ import unicode_literals

import io
import os

try:
    import yaml
except Exception:  # pragma: no cover - fallback for minimal environments
    yaml = None


class KnowledgeDocument(object):
    def __init__(self, id, type, title, content, source, metadata=None):
        self.id = id
        self.type = type
        self.title = title
        self.content = content
        self.source = source
        self.metadata = dict(metadata or {})

    def to_evidence(self):
        return {
            "id": self.id,
            "type": self.type,
            "title": self.title,
            "content": self.content,
            "source": self.source,
            "metadata": dict(self.metadata or {}),
        }

    def to_dict(self):
        payload = self.to_evidence()
        payload["source_type"] = self.type
        return payload


class KnowledgeDocumentBuilder(object):
    """Build retrievable knowledge documents from semantic configs."""

    def __init__(self, semantic_dir="semantic"):
        self.semantic_dir = semantic_dir

    def build(self):
        docs = []
        docs.extend(self._build_table_docs())
        docs.extend(self._build_metric_docs())
        docs.extend(self._build_dimension_docs())
        docs.extend(self._build_scenario_docs())
        docs.extend(self._build_sop_docs())
        return docs

    def build_evidence_store(self):
        return [doc.to_evidence() for doc in self.build()]

    def _load_yaml(self, filename):
        path = os.path.join(self.semantic_dir, filename)
        if not os.path.exists(path):
            return {}
        if yaml is None:
            return self._load_yaml_minimal(path)
        with io.open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def _load_yaml_minimal(self, path):
        # Minimal fallback is intentionally conservative. Production should use
        # PyYAML.  Keep the governed SOP corpus available in stripped-down
        # runtime/test environments so the RAG safety path does not silently
        # lose its analysis procedures.
        filename = os.path.basename(path)
        if filename == "tables.yaml":
            return {"tables": [
                {"name": "fct_orders", "allowed": True, "table_role": "fact", "grain": ["order_id"], "primary_key": "order_id", "time_fields": ["paid_at"], "columns": ["order_id", "store_id", "product_id", "channel", "order_status", "sell_through", "paid_at"]},
                {"name": "dim_store", "allowed": True, "table_role": "dimension", "grain": ["store_id"], "primary_key": "store_id", "columns": ["store_id", "region"]},
                {"name": "dim_product", "allowed": True, "table_role": "dimension", "grain": ["product_id"], "primary_key": "product_id", "columns": ["product_id", "product_name", "category", "unit_price"]},
            ], "joins": [
                {"id": "orders_to_store", "left_table": "fct_orders", "right_table": "dim_store", "condition": "fct_orders.store_id = dim_store.store_id"},
                {"id": "orders_to_product", "left_table": "fct_orders", "right_table": "dim_product", "condition": "fct_orders.product_id = dim_product.product_id"},
            ]}
        if filename == "metrics.yaml":
            return {"metrics": [
                {"id": "gmv", "name": "GMV", "description": "已支付订单成交总额，未扣除退款。", "synonyms": ["GMV", "销售额", "成交额", "业绩", "revenue"], "expression": "SUM(fct_orders.sell_through)", "aggregation_type": "additive", "base_table": "fct_orders", "time_field": "fct_orders.paid_at", "default_filters": ["fct_orders.order_status IN ('paid', 'completed')"], "allowed_dimensions": ["date", "channel", "region", "category"], "unit": "CNY", "note": "GMV 未扣除退款。"},
                {"id": "roi", "name": "ROI", "description": "投资回报率。", "synonyms": ["ROI", "roi", "投产比", "投入产出比"], "expression": "SUM(fct_orders.sell_through) / NULLIF(SUM(fct_orders.ad_cost), 0)", "base_table": "fct_orders", "time_field": "fct_orders.paid_at", "default_filters": ["fct_orders.order_status IN ('paid', 'completed')"], "allowed_dimensions": ["date", "channel", "region", "category"], "unit": "ratio"},
            ]}
        if filename == "sandbox_metrics.yaml":
            return {"metrics": [
                {"id": "sandbox_gmv", "name": "GMV", "synonyms": ["GMV", "销售额", "成交额"], "expression": "SUM(orders.gmv)", "base_table": "orders", "time_field": "order_date", "default_filters": ["orders.status = 'paid'"], "allowed_dimensions": ["date", "channel", "region", "category"], "unit": "yuan", "caveats": ["GMV 不扣退款；如果问净收入，应使用 net_gmv。"]},
                {"id": "net_gmv", "name": "净GMV", "synonyms": ["净收入", "净GMV", "扣退款销售额"], "expression": "SUM(orders.gmv - orders.refund_amount)", "base_table": "orders", "time_field": "order_date", "default_filters": ["orders.status IN ('paid', 'refunded')"], "allowed_dimensions": ["date", "channel", "region", "category"], "unit": "yuan"},
                {"id": "sandbox_roi", "name": "ROI", "synonyms": ["ROI", "投产比", "投入产出比"], "expression": "SUM(orders.gmv) / NULLIF(SUM(orders.ad_cost), 0)", "base_table": "orders", "time_field": "order_date", "default_filters": ["orders.status = 'paid'", "orders.ad_cost > 0"], "allowed_dimensions": ["date", "channel", "region"], "unit": "ratio", "caveats": ["ROI 依赖 ad_cost，非投放渠道可能没有广告成本。"]},
            ]}
        if filename == "ecommerce_sops.yaml":
            return {"sops": [
                {
                    "id": "gmv_drop_diagnosis",
                    "title": "GMV 下滑诊断 SOP",
                    "intents": ["attribution", "anomaly", "root_cause", "comparison"],
                    "trigger_terms": ["GMV", "销售额", "成交额", "下滑", "下降", "异常", "原因"],
                    "required_metrics": ["sandbox_gmv", "net_gmv"],
                    "recommended_metrics": ["order_count", "aov", "sandbox_roi"],
                    "required_dimensions": ["date"],
                    "recommended_dimensions": ["channel", "region", "category"],
                    "preconditions": [
                        "先确认分析时间范围和对比基期。",
                        "确认用户问题中的 GMV 是否需要扣除退款；净收入问题必须使用 net_gmv。",
                    ],
                    "ordered_steps": [
                        "按日期查看 GMV 与对比期变化，确认异常发生时间。",
                        "按渠道、区域、品类拆分变化贡献。",
                        "将 GMV 拆为订单数与客单价，并检查退款。",
                        "涉及投放渠道时查询 ROI 和广告成本。",
                        "所有归因结论必须由查询结果验证；未验证项只能表述为待排查假设。",
                    ],
                    "recommended_tools": ["sql_query", "anomaly_detection", "contribution_analysis"],
                    "caveats": [
                        "SOP 仅规定排查路径，不能证明某一渠道或因素是实际原因。",
                        "活动、库存、履约等外部原因没有数据证据时不得写成已确认结论。",
                    ],
                },
                {
                    "id": "roi_review",
                    "title": "投放 ROI 与 CPA 复盘 SOP",
                    "intents": ["attribution", "comparison", "anomaly"],
                    "trigger_terms": ["ROI", "投产比", "CPA", "广告", "投放", "获客成本"],
                    "required_metrics": ["sandbox_roi"],
                    "recommended_metrics": ["sandbox_gmv", "cpa", "ctr", "conversion_rate"],
                    "required_dimensions": ["date", "channel"],
                    "recommended_dimensions": ["region"],
                    "preconditions": ["确认 ad_cost 是否覆盖目标渠道和目标时间范围。", "非投放渠道可能没有广告成本，不得将空值解释为 ROI 为零。"],
                    "ordered_steps": ["按日期和渠道比较 ROI、广告成本与 GMV。", "识别高成本低产出渠道，再查看 CPA、CTR、转化率。", "区分预算变化、流量变化和转化变化；每项需数据支持。"],
                    "recommended_tools": ["sql_query", "comparison_analysis", "contribution_analysis"],
                    "caveats": ["ROI 反映口径内投入产出，不能直接推断增量因果。"],
                },
                {
                    "id": "conversion_funnel",
                    "title": "转化率与漏斗分析 SOP",
                    "intents": ["funnel", "attribution", "anomaly"],
                    "trigger_terms": ["转化率", "CVR", "漏斗", "点击率", "CTR", "曝光"],
                    "required_metrics": ["conversion_rate"],
                    "recommended_metrics": ["ctr", "impressions", "order_count"],
                    "required_dimensions": ["date"],
                    "recommended_dimensions": ["channel", "category", "region"],
                    "preconditions": ["确认分子、分母和事件表是否覆盖同一人群与时间窗口。"],
                    "ordered_steps": ["先复核转化率分母口径。", "按渠道、品类和日期拆解漏斗各环节。", "定位下降发生在曝光、点击、访问还是支付环节。"],
                    "recommended_tools": ["sql_query", "funnel_analysis"],
                    "caveats": ["当前 sandbox 的转化率为近似口径，生产环境应使用明确事件漏斗。"],
                },
                {
                    "id": "retention_cohort",
                    "title": "留存与复购 Cohort 分析 SOP",
                    "intents": ["retention", "cohort", "repurchase"],
                    "trigger_terms": ["留存", "复购", "回购", "cohort", "用户留存"],
                    "required_metrics": [],
                    "recommended_metrics": ["order_count"],
                    "required_dimensions": ["cohort_period"],
                    "recommended_dimensions": ["channel", "region"],
                    "preconditions": ["明确 cohort 起点、观察窗口、活跃/复购事件定义。"],
                    "ordered_steps": ["按首购或注册时间建立 cohort。", "计算各 cohort 在固定窗口内的留存/复购比例。", "比较 cohort 时保持观察窗口一致。"],
                    "recommended_tools": ["cohort_analysis", "sql_query"],
                    "caveats": ["不得把不同观察期的 cohort 比例直接横向比较。"],
                },
                {
                    "id": "experiment_ab",
                    "title": "A/B 实验结果判读 SOP",
                    "intents": ["experiment", "ab_test"],
                    "trigger_terms": ["A/B", "AB实验", "实验组", "对照组", "显著性"],
                    "required_metrics": [],
                    "recommended_metrics": ["sandbox_gmv", "conversion_rate"],
                    "required_dimensions": [],
                    "recommended_dimensions": ["channel", "region"],
                    "preconditions": ["确认实验 ID、实验分流、主指标和实验时间窗。"],
                    "ordered_steps": ["检查样本量和分流比例。", "比较实验组与对照组主指标及置信区间/显著性。", "检查护栏指标，避免只根据单一指标下结论。"],
                    "recommended_tools": ["experiment_analysis", "sql_query"],
                    "caveats": ["统计显著不自动等于业务显著；未达显著性不得宣称实验有效。"],
                },
            ]}
        return {}

    def _build_table_docs(self):
        payload = self._load_yaml("tables.yaml")
        tables = payload.get("tables") or []
        joins = payload.get("joins") or []
        join_by_table = {}
        for join in joins:
            join_by_table.setdefault(join.get("left_table"), []).append(join)
            join_by_table.setdefault(join.get("right_table"), []).append(join)

        docs = []
        for table in tables:
            name = table.get("name", "")
            content = "\n".join([
                "数据表: %s" % name,
                "角色: %s" % table.get("table_role", "unknown"),
                "粒度: %s" % ", ".join(table.get("grain") or []),
                "主键: %s" % table.get("primary_key", ""),
                "时间字段: %s" % ", ".join(table.get("time_fields") or []),
                "字段: %s" % ", ".join(table.get("columns") or []),
                "可用关联: %s" % "; ".join(["%s: %s" % (j.get("id"), j.get("condition")) for j in join_by_table.get(name, [])]),
                "用途: 用于 SQL 规划、字段选择、join 选择和粒度安全校验。",
            ])
            docs.append(KnowledgeDocument(
                id="table:%s" % name,
                type="database_schema",
                title="数据表 %s" % name,
                content=content,
                source="semantic/tables.yaml",
                metadata={"table": name, "grain": table.get("grain") or [], "allowed": table.get("allowed", True)},
            ))
        return docs

    def _build_metric_docs(self):
        payload = self._load_yaml("metrics.yaml")
        metrics = payload.get("metrics") or []
        docs = []
        for metric in metrics:
            mid = metric.get("id", "")
            content = "\n".join([
                "指标: %s (%s)" % (metric.get("name", ""), mid),
                "描述: %s" % metric.get("description", ""),
                "同义词: %s" % ", ".join(metric.get("synonyms") or []),
                "计算表达式: %s" % metric.get("expression", ""),
                "聚合类型: %s" % metric.get("aggregation_type", metric.get("aggregation", "")),
                "基础表: %s" % metric.get("base_table", ""),
                "时间字段: %s" % metric.get("time_field", ""),
                "默认过滤: %s" % "; ".join(metric.get("default_filters") or []),
                "允许维度: %s" % ", ".join(metric.get("allowed_dimensions") or []),
                "单位: %s" % metric.get("unit", ""),
                "注意事项: %s" % metric.get("note", ""),
            ])
            docs.append(KnowledgeDocument(
                id="metric:%s" % mid,
                type="metric_method",
                title="指标口径 %s" % metric.get("name", mid),
                content=content,
                source="semantic/metrics.yaml",
                metadata={
                    "metric_id": mid,
                    "metric_refs": [mid, metric.get("name", "")] + list(metric.get("synonyms") or []),
                    "base_table": metric.get("base_table"),
                    "table_refs": [metric.get("base_table")] if metric.get("base_table") else [],
                    "time_field": metric.get("time_field"),
                    "allowed_dimensions": metric.get("allowed_dimensions") or [],
                    "authority": "semantic_config",
                    "knowledge_scope": "analysis_constraint",
                },
            ))
        return docs

    def _build_dimension_docs(self):
        payload = self._load_yaml("dimensions.yaml")
        dimensions = payload.get("dimensions") or []
        docs = []
        for dim in dimensions:
            did = dim.get("id", "")
            content = "\n".join([
                "维度: %s (%s)" % (dim.get("name", ""), did),
                "字段表达式: %s" % dim.get("field", ""),
                "所在表: %s" % dim.get("table", ""),
                "需要关联: %s" % dim.get("join", ""),
                "同义词: %s" % ", ".join(dim.get("synonyms") or []),
            ])
            docs.append(KnowledgeDocument(
                id="dimension:%s" % did,
                type="dimension_method",
                title="分析维度 %s" % dim.get("name", did),
                content=content,
                source="semantic/dimensions.yaml",
                metadata={"dimension_id": did, "table": dim.get("table"), "join": dim.get("join")},
            ))
        return docs

    def _build_sop_docs(self):
        """Load governed, structured ecommerce procedures for analysis planning.

        SOPs are reference material, never data facts.  The metadata drives typed
        retrieval and allows downstream guards to distinguish a procedure from a
        metric/schema constraint.
        """
        payload = self._load_yaml("ecommerce_sops.yaml")
        docs = []
        for sop in payload.get("sops") or []:
            sid = sop.get("id", "")
            if not sid:
                continue
            content = "\n".join([
                "适用意图: %s" % ", ".join(sop.get("intents") or []),
                "触发词: %s" % ", ".join(sop.get("trigger_terms") or []),
                "前置条件: %s" % "；".join(sop.get("preconditions") or []),
                "执行步骤: %s" % "；".join(sop.get("ordered_steps") or []),
                "推荐工具: %s" % ", ".join(sop.get("recommended_tools") or []),
                "注意事项: %s" % "；".join(sop.get("caveats") or []),
            ])
            docs.append(KnowledgeDocument(
                id="sop:%s" % sid,
                type="analysis_sop",
                title=sop.get("title") or sid,
                content=content,
                source="semantic/ecommerce_sops.yaml",
                metadata={
                    "sop_id": sid,
                    "intents": sop.get("intents") or [],
                    "trigger_terms": sop.get("trigger_terms") or [],
                    "metric_refs": list(sop.get("required_metrics") or []) + list(sop.get("recommended_metrics") or []),
                    "required_metrics": sop.get("required_metrics") or [],
                    "recommended_metrics": sop.get("recommended_metrics") or [],
                    "required_dimensions": sop.get("required_dimensions") or [],
                    "recommended_dimensions": sop.get("recommended_dimensions") or [],
                    "recommended_tools": sop.get("recommended_tools") or [],
                    "authority": "governed_sop",
                    "knowledge_scope": "analysis_procedure",
                    "tenant_id": "global",
                    "acl_roles": ["analyst", "admin"],
                },
            ))
        return docs

    def _build_scenario_docs(self):
        cards = [
            {
                "id": "scenario:gmv_diagnosis",
                "title": "GMV 下滑诊断 SOP",
                "keywords": ["GMV", "销售额", "下滑", "归因", "诊断"],
                "content": "GMV 下滑优先按时间、渠道、区域、品类拆解；再检查订单数、客单价、转化率、ROI、CPA。注意 GMV 未扣除退款，若问题涉及净收入需引入退款口径。",
                "recommended_metrics": ["gmv", "order_count", "aov", "conversion_rate", "roi", "cpa"],
                "recommended_dimensions": ["date", "channel", "region", "category"],
            },
            {
                "id": "scenario:conversion_analysis",
                "title": "转化率分析 SOP",
                "keywords": ["转化率", "CVR", "漏斗", "转化"],
                "content": "转化率问题先确认分母口径，再按渠道、品类、区域和时间趋势拆解。当前 MVP 口径用订单用户转化近似，生产应绑定明确漏斗事件表。",
                "recommended_metrics": ["conversion_rate", "order_count", "ctr", "impressions"],
                "recommended_dimensions": ["date", "channel", "category"],
            },
            {
                "id": "scenario:ad_roi_review",
                "title": "投放 ROI 复盘 SOP",
                "keywords": ["ROI", "投产比", "投放", "广告", "CPA"],
                "content": "投放复盘关注 ROI、CPA、GMV、订单数和曝光点击指标。需要按渠道和时间拆解，识别高成本低转化渠道。",
                "recommended_metrics": ["roi", "cpa", "gmv", "order_count", "ctr", "impressions"],
                "recommended_dimensions": ["date", "channel", "region"],
            },
        ]
        docs = []
        for card in cards:
            content = "\n".join([
                card["title"],
                "关键词: %s" % ", ".join(card.get("keywords") or []),
                "分析方法: %s" % card.get("content", ""),
                "推荐指标: %s" % ", ".join(card.get("recommended_metrics") or []),
                "推荐维度: %s" % ", ".join(card.get("recommended_dimensions") or []),
            ])
            docs.append(KnowledgeDocument(
                id=card["id"],
                type="business_scenario",
                title=card["title"],
                content=content,
                source="builtin/scenario_cards",
                metadata={
                    "recommended_metrics": card.get("recommended_metrics") or [],
                    "recommended_dimensions": card.get("recommended_dimensions") or [],
                },
            ))
        return docs


class KnowledgeRecall(object):
    """Keyword recall over generated knowledge documents."""

    def __init__(self, documents=None):
        self.documents = list(documents or [])

    @classmethod
    def from_semantic_dir(cls, semantic_dir="semantic"):
        return cls(KnowledgeDocumentBuilder(semantic_dir=semantic_dir).build())

    def recall(self, query, top_k=5, types=None):
        allowed_types = set(types or [])
        scored = []
        for doc in self.documents:
            if allowed_types and doc.type not in allowed_types:
                continue
            score = self._score(query, doc)
            if score > 0:
                scored.append((doc, score))
        scored.sort(key=lambda item: item[1], reverse=True)
        return [(doc, score) for doc, score in scored[:top_k]]

    def _score(self, query, doc):
        q = (query or "").lower()
        content = (doc.title + "\n" + doc.content).lower()
        metadata_text = " ".join([str(v) for v in doc.metadata.values()]).lower()
        if not q:
            return 0.0
        if q in content:
            return 1.0
        words = [w for w in q.replace("，", " ").replace("？", " ").replace("?", " ").split() if w]
        if not words:
            return 0.0
        hit = 0
        for word in words:
            if word in content or word in metadata_text:
                hit += 1
        return min(1.0, float(hit) / float(len(words)))


def build_knowledge_evidence_store(semantic_dir="semantic"):
    return KnowledgeDocumentBuilder(semantic_dir=semantic_dir).build_evidence_store()


__all__ = ["KnowledgeDocument", "KnowledgeDocumentBuilder", "KnowledgeRecall", "build_knowledge_evidence_store"]
