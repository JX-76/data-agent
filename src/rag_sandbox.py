# -*- coding: utf-8 -*-
"""Deterministic virtual-database RAG used before a production database exists.

The sandbox deliberately separates facts (SQLite tables) from retrievable
knowledge (schema, metric definitions, SOPs and governance chunks).
"""
from __future__ import unicode_literals

import io
import math
import os
import re

try:
    import yaml
except Exception:
    yaml = None


class KnowledgeChunk(object):
    def __init__(self, chunk_id, parent_id, knowledge_type, section_type, title, content, metadata=None):
        self.chunk_id = chunk_id
        self.parent_id = parent_id
        self.knowledge_type = knowledge_type
        self.section_type = section_type
        self.title = title
        self.content = content
        self.metadata = dict(metadata or {})

    def to_dict(self, score=0.0, channels=None):
        return {"id": self.chunk_id, "parent_id": self.parent_id,
                "type": self.knowledge_type, "section_type": self.section_type,
                "title": self.title, "content": self.content,
                "metadata": dict(self.metadata), "relevance_score": score,
                "retrieval_channels": list(channels or [])}


def _tokens(text):
    text = (text or "").lower()
    ascii_words = re.findall(r"[a-z0-9_]+", text)
    chinese_terms = re.findall(r"[\u4e00-\u9fff]{2,}", text)
    chars = [ch for ch in text if '\u4e00' <= ch <= '\u9fff']
    return set(ascii_words + chinese_terms + chars)


class SandboxKnowledgeBuilder(object):
    """Builds type-aware chunks from the deterministic ecommerce sandbox."""
    TABLES = {
        "orders": ("订单事实表；一行代表一笔订单，GMV、退款、渠道、区域均在此表。", ["order_id", "order_date", "user_id", "channel", "region", "gmv", "refund_amount", "status"]),
        "products": ("商品维表；一行代表一个商品，提供品类与价格。", ["product_id", "product_name", "category", "price"]),
        "users": ("用户维表；一行代表一个用户，包含会员和区域属性。", ["user_id", "user_name", "region", "is_vip"]),
        "user_events": ("用户事件事实表；用于注册、活跃、购买等漏斗分析，tenant_id 用于租户隔离。", ["event_id", "user_id", "tenant_id", "event_name", "event_date"]),
    }

    def __init__(self, metrics_path="semantic/sandbox_metrics.yaml"):
        if metrics_path and not os.path.isabs(metrics_path):
            base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            metrics_path = os.path.join(base, metrics_path)
        self.metrics_path = metrics_path

    def build(self):
        chunks = []
        for name, (description, fields) in self.TABLES.items():
            parent = "table:%s" % name
            meta = {"table_refs": [name], "authority": "verified", "tenant_id": "global", "acl_roles": ["analyst", "admin"]}
            chunks.append(KnowledgeChunk(parent + "#profile", parent, "database_schema", "profile", "表 %s 概览" % name, description + " 主键/字段：" + ", ".join(fields), meta))
            chunks.append(KnowledgeChunk(parent + "#fields", parent, "database_schema", "fields", "表 %s 字段" % name, "表 %s 可用字段：%s。字段选择必须与指标口径一致。" % (name, ", ".join(fields)), meta))
        chunks.extend(self._metric_chunks())
        chunks.extend(self._scenario_chunks())
        return chunks

    def _metric_chunks(self):
        if yaml is None:
            metrics = self._minimal_metrics()
        else:
            with io.open(self.metrics_path, "r", encoding="utf-8") as handle:
                metrics = (yaml.safe_load(handle) or {}).get("metrics") or []
        chunks = []
        for metric in metrics:
            mid = metric["id"]; parent = "metric:%s" % mid
            base = metric.get("base_table")
            meta = {"metric_refs": [mid] + list(metric.get("synonyms") or []), "table_refs": [base], "authority": "verified", "tenant_id": "global", "acl_roles": ["analyst", "admin"]}
            chunks.append(KnowledgeChunk(parent + "#definition", parent, "metric_method", "definition", "%s 定义" % metric["name"], "%s；同义词：%s。" % (metric["name"], "、".join(metric.get("synonyms") or [])), meta))
            chunks.append(KnowledgeChunk(parent + "#formula", parent, "metric_method", "formula", "%s 计算公式" % metric["name"], "计算表达式：%s。基础表：%s。" % (metric["expression"], base), meta))
            chunks.append(KnowledgeChunk(parent + "#filters", parent, "metric_method", "filters", "%s 过滤与维度" % metric["name"], "默认过滤：%s；可用维度：%s。" % ("；".join(metric.get("default_filters") or ["无"]), "、".join(metric.get("allowed_dimensions") or [])), meta))
            for caveat in metric.get("caveats") or []:
                chunks.append(KnowledgeChunk(parent + "#caveats", parent, "metric_method", "caveats", "%s 注意事项" % metric["name"], caveat, meta))
        return chunks

    def _minimal_metrics(self):
        """Conservative fallback for stripped-down Python 2 test/runtime envs."""
        return [
            {"id": "sandbox_gmv", "name": "GMV", "synonyms": ["GMV", "销售额", "成交额"], "expression": "SUM(orders.gmv)", "base_table": "orders", "default_filters": ["orders.status = 'paid'"], "allowed_dimensions": ["date", "channel", "region", "category"], "caveats": ["GMV 不扣退款；如果问净收入，应使用 net_gmv。"]},
            {"id": "net_gmv", "name": "净GMV", "synonyms": ["净收入", "净GMV", "扣退款销售额"], "expression": "SUM(orders.gmv - orders.refund_amount)", "base_table": "orders", "default_filters": ["orders.status IN ('paid', 'refunded')"], "allowed_dimensions": ["date", "channel", "region", "category"]},
            {"id": "sandbox_roi", "name": "ROI", "synonyms": ["ROI", "投产比", "投入产出比"], "expression": "SUM(orders.gmv) / NULLIF(SUM(orders.ad_cost), 0)", "base_table": "orders", "default_filters": ["orders.status = 'paid'", "orders.ad_cost > 0"], "allowed_dimensions": ["date", "channel", "region"], "caveats": ["ROI 依赖 ad_cost，非投放渠道可能没有广告成本。"]},
        ]

    def _scenario_chunks(self):
        meta = {"metric_refs": ["sandbox_gmv", "sandbox_roi"], "table_refs": ["orders"], "authority": "verified", "tenant_id": "global", "acl_roles": ["analyst", "admin"]}
        return [
            KnowledgeChunk("scenario:gmv_drop#overview", "scenario:gmv_drop", "business_scenario", "overview", "GMV 下滑诊断", "适用于 GMV、销售额、成交额下降或波动问题。", meta),
            KnowledgeChunk("scenario:gmv_drop#steps", "scenario:gmv_drop", "business_scenario", "steps", "GMV 下滑诊断步骤", "先按 date、channel、region 拆趋势，再检查订单数、客单价、退款和品类；涉及投放时同时看 ROI。", meta),
            KnowledgeChunk("scenario:gmv_drop#risk", "scenario:gmv_drop", "business_scenario", "caveats", "GMV 下滑口径风险", "先确认问的是 GMV 还是扣退款后的净GMV，避免错误归因。", meta),
        ]


class HybridSandboxRetriever(object):
    """Exact + BM25-like lexical + deterministic dense-like recall + relation expansion."""
    def __init__(self, chunks=None):
        self.chunks = list(chunks or SandboxKnowledgeBuilder().build())

    def retrieve(self, query, top_k=8, roles=None, tenant_id="global"):
        roles = set(roles or ["analyst"]); qtokens = _tokens(query); scored = []
        for chunk in self.chunks:
            meta = chunk.metadata
            if not roles.intersection(set(meta.get("acl_roles") or [])):
                continue
            if meta.get("tenant_id") not in ("global", tenant_id):
                continue
            text_tokens = _tokens(chunk.title + " " + chunk.content + " " + " ".join(meta.get("metric_refs") or []))
            overlap = len(qtokens.intersection(text_tokens))
            exact = 1 if any(term.lower() in (query or "").lower() for term in meta.get("metric_refs") or []) else 0
            dense = self._dense_similarity(qtokens, text_tokens)
            score = exact * 3.0 + overlap + dense
            if score:
                scored.append((chunk, score, ["exact"] if exact else ["bm25", "hash_dense"]))
        scored.sort(key=lambda item: item[1], reverse=True)
        expanded = self._relation_expand(scored[:top_k], qtokens)
        return self._rerank(expanded, qtokens)[:top_k]

    def _dense_similarity(self, left, right):
        if not left or not right: return 0.0
        return float(len(left.intersection(right))) / math.sqrt(float(len(left) * len(right)))

    def _relation_expand(self, results, qtokens):
        by_id = {c.chunk_id: (c, s, ch) for c, s, ch in results}
        selected_parents = set(c.parent_id for c, _, _ in results if c.section_type in ("formula", "fields"))
        for chunk in self.chunks:
            if chunk.parent_id not in selected_parents or chunk.section_type not in ("definition", "caveats", "profile"):
                continue
            if chunk.chunk_id not in by_id:
                by_id[chunk.chunk_id] = (chunk, 0.25, ["relation"])
        return list(by_id.values())

    def _rerank(self, results, qtokens):
        ranked = []
        for chunk, score, channels in results:
            if any(word in qtokens for word in ["怎么", "口径", "计算", "多少"]):
                score += 0.6 if chunk.section_type in ("formula", "filters", "definition") else 0
            if any(word in qtokens for word in ["下滑", "原因", "为什么", "诊断"]):
                score += 0.8 if chunk.knowledge_type == "business_scenario" else 0
            ranked.append((chunk, score, channels))
        ranked.sort(key=lambda item: item[1], reverse=True)
        return ranked


def build_evidence_pack(query, top_k=8, roles=None, tenant_id="global"):
    results = HybridSandboxRetriever().retrieve(query, top_k=top_k, roles=roles, tenant_id=tenant_id)
    items = []
    seen_parent = {}
    for chunk, score, channels in results:
        if seen_parent.get(chunk.parent_id, 0) >= 3: continue
        seen_parent[chunk.parent_id] = seen_parent.get(chunk.parent_id, 0) + 1
        content = chunk.content[:260]
        items.append({"citation_id": "K%s" % (len(items) + 1), "claim": chunk.title, "supporting_extract": content, "chunk_id": chunk.chunk_id, "parent_id": chunk.parent_id, "score": round(score, 4), "channels": channels})
    return {"query": query, "evidence": items, "citations": [{"id": x["citation_id"], "chunk_id": x["chunk_id"], "claim": x["claim"]} for x in items]}


__all__ = ["KnowledgeChunk", "SandboxKnowledgeBuilder", "HybridSandboxRetriever", "build_evidence_pack"]
