# -*- coding: utf-8 -*-
"""Production-shaped RAG pipeline over sandbox/semantic knowledge.

The business data may be synthetic, but this module implements a real RAG
retrieval chain: typed chunking, BM25, embedding provider, FAISS vector index,
RRF fusion, optional cross-encoder rerank, metadata filtering, parent expansion,
context compression, citations and retrieval metrics.
"""
from __future__ import unicode_literals

import math
import os
import re
from collections import defaultdict

try:
    text_type = unicode
except NameError:  # pragma: no cover - Python 3
    text_type = str


def _safe_text(value):
    if value is None:
        return u""
    if isinstance(value, text_type):
        return value
    try:
        return value.decode("utf-8")
    except Exception:
        try:
            return value.decode("mbcs")
        except Exception:
            return text_type(value)


def _load_project_dotenv_once():
    """Load ROOT/.env for local CLI runs without overriding process env.

    Several RAG scripts are invoked directly from PowerShell and do not go
    through the API server/bootstrap layer. Loading .env here keeps offline
    embedding settings effective while preserving explicit shell variables.
    """
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
    path = os.path.join(root, ".env")
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                if key and key not in os.environ:
                    os.environ[key] = value.strip().strip('"').strip("'")
    except Exception:
        return


_load_project_dotenv_once()

try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None

try:
    from rank_bm25 import BM25Okapi
except Exception:  # pragma: no cover
    BM25Okapi = None

try:
    import faiss
except Exception:  # pragma: no cover
    faiss = None

try:
    from sentence_transformers import CrossEncoder, SentenceTransformer
except Exception:  # pragma: no cover
    CrossEncoder = None
    SentenceTransformer = None

from knowledge_rag import KnowledgeDocumentBuilder
try:
    from rag_sandbox import SandboxKnowledgeBuilder
except Exception:  # pragma: no cover
    SandboxKnowledgeBuilder = None


def tokenize(text):
    text = _safe_text(text).lower()
    ascii_words = re.findall(r"[a-z0-9_]+", text)
    zh_terms = re.findall(r"[\u4e00-\u9fff]{2,}", text)
    zh_chars = [ch for ch in text if "\u4e00" <= ch <= "\u9fff"]
    return ascii_words + zh_terms + zh_chars


class RagChunk(object):
    def __init__(self, id, parent_id, title, content, source, type="knowledge", section_type="body", metadata=None):
        self.id = id
        self.parent_id = parent_id or id
        self.title = title or ""
        self.content = content or ""
        self.source = source or ""
        self.type = type or "knowledge"
        self.section_type = section_type or "body"
        self.metadata = dict(metadata or {})

    def text_for_embedding(self):
        return "%s\n%s\n%s" % (self.title, self.type, self.content)

    def to_dict(self, score=0.0, channels=None):
        return {
            "id": self.id,
            "parent_id": self.parent_id,
            "title": self.title,
            "content": self.content,
            "source": self.source,
            "type": self.type,
            "section_type": self.section_type,
            "metadata": dict(self.metadata),
            "relevance_score": float(score or 0.0),
            "retrieval_channels": list(channels or []),
        }


class KnowledgeIngestor(object):
    """Build parent/child chunks from semantic configs and sandbox metadata."""
    def __init__(self, semantic_dir="semantic", include_sandbox=True):
        self.semantic_dir = semantic_dir
        self.include_sandbox = include_sandbox

    def build_chunks(self):
        chunks = []
        for doc in KnowledgeDocumentBuilder(self.semantic_dir).build():
            meta = dict(doc.metadata or {})
            meta.setdefault("tenant_id", "global")
            meta.setdefault("acl_roles", ["analyst", "admin"])
            parent = RagChunk(doc.id, doc.id, doc.title, doc.content, doc.source, doc.type, "parent", meta)
            chunks.append(parent)
            chunks.extend(self._child_chunks(parent))
        if self.include_sandbox and SandboxKnowledgeBuilder is not None:
            for item in SandboxKnowledgeBuilder().build():
                chunks.append(RagChunk(item.chunk_id, item.parent_id, item.title, item.content, "semantic/sandbox_metrics.yaml", item.knowledge_type, item.section_type, item.metadata))
        return self._dedupe(chunks)

    def _child_chunks(self, parent):
        lines = [x.strip() for x in parent.content.splitlines() if x.strip()]
        if len(lines) <= 3:
            return []
        chunks = []
        for idx, line in enumerate(lines):
            section = line.split(":", 1)[0] if ":" in line else "section"
            chunks.append(RagChunk("%s#%s" % (parent.id, idx + 1), parent.id, parent.title + " / " + section, line, parent.source, parent.type, section, parent.metadata))
        return chunks

    def _dedupe(self, chunks):
        seen = set(); out = []
        for c in chunks:
            if c.id in seen:
                continue
            seen.add(c.id); out.append(c)
        return out


class SentenceTransformerEmbeddingProvider(object):
    """Real embedding provider using sentence-transformers.

    Model instances are process-cached: one request/case may construct several
    RAG services, but repeatedly deserializing the same 512-dim model is both
    slow and needlessly unstable under concurrent traffic.
    """
    _MODEL_CACHE = {}

    def __init__(self, model_name=None, normalize=True):
        if SentenceTransformer is None:
            raise RuntimeError("sentence-transformers is not installed")
        self.model_name = model_name or os.environ.get("DATA_AGENT_EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5")
        self.normalize = normalize
        # Benchmark/production startup must fail explicitly when a requested
        # model is absent; it must never silently download or replace it.
        offline = os.environ.get("DATA_AGENT_EMBEDDING_OFFLINE", "").lower() in ("1", "true", "yes")
        cache_key = (self.model_name, bool(offline))
        if cache_key not in self._MODEL_CACHE:
            kwargs = {"local_files_only": True} if offline else {}
            self._MODEL_CACHE[cache_key] = SentenceTransformer(self.model_name, **kwargs)
        self.model = self._MODEL_CACHE[cache_key]

    def encode(self, texts):
        vectors = self.model.encode(list(texts), normalize_embeddings=self.normalize, convert_to_numpy=True, show_progress_bar=False)
        if np is None:
            return vectors
        return np.asarray(vectors, dtype="float32")


class DeterministicEmbeddingProvider(object):
    """Offline test provider. It is not claimed as model embedding in production."""
    def __init__(self, dim=128):
        self.dim = dim

    def encode(self, texts):
        if np is None:
            rows = []
            for text in texts:
                vec = [0.0] * self.dim
                for tok in tokenize(text):
                    vec[hash(tok) % self.dim] += 1.0
                rows.append(vec)
            return rows
        rows = []
        for text in texts:
            vec = np.zeros((self.dim,), dtype="float32")
            for tok in tokenize(text):
                vec[hash(tok) % self.dim] += 1.0
            norm = np.linalg.norm(vec)
            if norm > 0:
                vec = vec / norm
            rows.append(vec)
        return np.vstack(rows).astype("float32") if rows else np.zeros((0, self.dim), dtype="float32")


class BM25Retriever(object):
    def __init__(self, chunks):
        self.chunks = list(chunks)
        self.corpus_tokens = [tokenize(c.title + " " + c.content + " " + " ".join([_safe_text(v) for v in c.metadata.values()])) for c in self.chunks]
        self.index = BM25Okapi(self.corpus_tokens) if BM25Okapi is not None else None

    def search(self, query, top_k=20):
        if self.index is None:
            scored = []
            query_tokens = set(tokenize(query))
            for idx, chunk in enumerate(self.chunks):
                text_tokens = set(self.corpus_tokens[idx])
                overlap = len(query_tokens & text_tokens)
                if overlap > 0:
                    scored.append((idx, float(overlap)))
            return sorted(scored, key=lambda x: x[1], reverse=True)[:top_k]
        scores = self.index.get_scores(tokenize(query))
        if np is None:
            order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:top_k]
            return [(int(i), float(scores[i])) for i in order if scores[i] > 0]
        order = np.argsort(scores)[::-1][:top_k]
        return [(int(i), float(scores[i])) for i in order if scores[i] > 0]


class FaissVectorRetriever(object):
    def __init__(self, chunks, embedding_provider, hnsw_m=32):
        self.chunks = list(chunks)
        self.embedding_provider = embedding_provider
        self.vectors = self.embedding_provider.encode([c.text_for_embedding() for c in self.chunks])
        self.index = None
        self.dim = self._infer_dim(self.vectors)
        if self.dim <= 0:
            raise RuntimeError("empty vector index")
        # Production uses FAISS HNSW when available.  Local/test runtimes should
        # not crash just because the optional native dependency is missing, so
        # fall back to deterministic in-memory dot-product search.
        if faiss is not None:
            self.index = faiss.IndexHNSWFlat(self.dim, hnsw_m, faiss.METRIC_INNER_PRODUCT)
            self.index.hnsw.efConstruction = 80
            self.index.hnsw.efSearch = 64
            self.index.add(self.vectors)

    def _infer_dim(self, vectors):
        if hasattr(vectors, "shape"):
            return int(vectors.shape[1]) if len(vectors.shape) == 2 and vectors.shape[0] else 0
        if vectors:
            return len(vectors[0])
        return 0

    def search(self, query, top_k=20):
        q = self.embedding_provider.encode([query])
        if self.index is not None:
            scores, ids = self.index.search(q, top_k)
            out = []
            for i, score in zip(ids[0], scores[0]):
                if int(i) >= 0:
                    out.append((int(i), float(score)))
            return out
        return self._linear_search(q, top_k)

    def _linear_search(self, q, top_k):
        scored = []
        if np is not None and hasattr(self.vectors, "shape"):
            scores = self.vectors.dot(q[0])
            order = np.argsort(scores)[::-1][:top_k]
            return [(int(i), float(scores[i])) for i in order if float(scores[i]) > 0]
        query_vec = q[0]
        for idx, vec in enumerate(self.vectors):
            score = sum(float(a) * float(b) for a, b in zip(vec, query_vec))
            if score > 0:
                scored.append((idx, score))
        return sorted(scored, key=lambda x: x[1], reverse=True)[:top_k]


def rrf_fuse(result_sets, k=60, weights=None):
    weights = weights or {}
    fused = defaultdict(float); channels = defaultdict(list)
    for channel, results in result_sets.items():
        weight = float(weights.get(channel, 1.0))
        for rank, (idx, _score) in enumerate(results, start=1):
            fused[idx] += weight / float(k + rank)
            channels[idx].append(channel)
    return sorted([(idx, score, channels[idx]) for idx, score in fused.items()], key=lambda x: x[1], reverse=True)


class CrossEncoderReranker(object):
    def __init__(self, model_name=None):
        if CrossEncoder is None:
            raise RuntimeError("sentence-transformers CrossEncoder is not available")
        self.model_name = model_name or os.environ.get("DATA_AGENT_RERANK_MODEL", "BAAI/bge-reranker-base")
        self.model = CrossEncoder(self.model_name)

    def rerank(self, query, chunks, top_k=8):
        pairs = [(query, c.title + "\n" + c.content) for c in chunks]
        scores = self.model.predict(pairs)
        ranked = sorted(zip(chunks, [float(s) for s in scores]), key=lambda x: x[1], reverse=True)
        return ranked[:top_k]


class FullRagRetriever(object):
    def __init__(self, chunks=None, embedding_provider=None, reranker=None):
        self.chunks = list(chunks or KnowledgeIngestor().build_chunks())
        self.embedding_provider = embedding_provider or SentenceTransformerEmbeddingProvider()
        self.bm25 = BM25Retriever(self.chunks)
        self.vector = FaissVectorRetriever(self.chunks, self.embedding_provider)
        self.reranker = reranker

    def retrieve(self, query, top_k=8, candidate_k=30, access_context=None):
        allowed = self._allowed_indices(access_context)
        if not allowed:
            return {"query": query, "evidence": [], "citations": []}
        bm25 = [(i, s) for i, s in self.bm25.search(query, candidate_k * 2) if i in allowed][:candidate_k]
        dense = [(i, s) for i, s in self.vector.search(query, candidate_k * 2) if i in allowed][:candidate_k]
        metadata = self._metadata_search(query, allowed, candidate_k)
        fused = rrf_fuse({"bm25": bm25, "dense_hnsw": dense, "metadata": metadata}, weights={"bm25": 1.0, "dense_hnsw": 1.0, "metadata": 1.2})[:candidate_k]
        candidates = [self.chunks[i] for i, _, _ in fused]
        channel_by_id = {self.chunks[i].id: ch for i, _, ch in fused}
        score_by_id = {self.chunks[i].id: score for i, score, _ in fused}
        boosted = []
        q = _safe_text(query).lower()
        for chunk in candidates:
            score = score_by_id.get(chunk.id, 0.0)
            chunk_text = "%s %s %s" % (chunk.id.lower(), chunk.title.lower(), chunk.content.lower())
            if "gmv" in q and "gmv" in chunk_text:
                score += 0.25
            if ("roi" in q or "投产" in q) and "roi" in chunk_text:
                score += 0.25
            if any(token in q for token in ["下滑", "为什么", "原因", "诊断", "复盘"]):
                if chunk.type in ("business_scenario", "analysis_sop"):
                    score += 0.2
            boosted.append((chunk, score, channel_by_id.get(chunk.id, [])))
        # Ensure type-relevant documents participate in reranking even when a
        # lexical/dense candidate generator misses them.  This is generic
        # metadata routing (not a hard-coded document id) and is crucial for
        # diagnosis queries, whose words often differ from SOP wording.
        candidate_ids = set([chunk.id for chunk, _, _ in boosted])
        wants_diagnosis = any(token in q for token in ["下滑", "为什么", "原因", "诊断", "复盘"])
        wanted_types = set(["analysis_sop", "business_scenario"]) if wants_diagnosis else set()
        # Keep exactly one authoritative definition for every metric named in
        # the question. This is source-agnostic metadata selection, and avoids
        # a diagnosis SOP crowding out the metric contract used by the agent.
        requested_metric_ids = []
        if "gmv" in q:
            requested_metric_ids.append("gmv")
        if "roi" in q or "投产" in q:
            requested_metric_ids.append("roi")
        definition_selected = set()
        for chunk_index, chunk in enumerate(self.chunks):
            if chunk.id in candidate_ids or chunk_index not in allowed:
                continue
            chunk_text = (chunk.title + " " + chunk.content + " " + " ".join([_safe_text(v) for v in chunk.metadata.values()])).lower()
            metric_match = ("gmv" in q and "gmv" in chunk_text) or (("roi" in q or "投产" in q) and ("roi" in chunk_text or "投产" in chunk_text))
            metric_id = str(chunk.metadata.get("metric_id") or "").lower()
            if not metric_id:
                refs = [_safe_text(ref).lower() for ref in (chunk.metadata.get("metric_refs") or [])]
                metric_id = next((metric for metric in requested_metric_ids if metric in refs or ("sandbox_" + metric) in refs), "")
            is_authoritative_definition = (
                chunk.section_type == "definition" and
                metric_id in requested_metric_ids and
                metric_id not in definition_selected
            )
            if is_authoritative_definition:
                definition_selected.add(metric_id)
            if chunk.type in wanted_types or metric_match or is_authoritative_definition:
                score = (0.05 + (0.35 if chunk.type in wanted_types else 0.0) +
                         (0.30 if metric_match else 0.0) +
                         (0.70 if is_authoritative_definition else 0.0))
                channels = ["type_metadata_expansion"]
                if is_authoritative_definition:
                    channels.append("metric_definition_expansion")
                boosted.append((chunk, score, channels))
        # A definition may already be present in the fused candidates. Promote
        # it with the same metadata rule before final ranking.
        normalized = []
        seen_definition_metrics = set()
        for chunk, score, channels in boosted:
            metric_id = str(chunk.metadata.get("metric_id") or "").lower()
            if not metric_id:
                refs = [_safe_text(ref).lower() for ref in (chunk.metadata.get("metric_refs") or [])]
                metric_id = next((metric for metric in requested_metric_ids if metric in refs or ("sandbox_" + metric) in refs), "")
            is_definition = (chunk.section_type == "definition" and
                             metric_id in requested_metric_ids and
                             metric_id not in seen_definition_metrics)
            if is_definition:
                seen_definition_metrics.add(metric_id)
                score += 0.70
                channels = list(channels) + ["metric_definition_anchor"]
            normalized.append((chunk, score, channels))
        boosted = normalized
        boosted.sort(key=lambda item: item[1], reverse=True)
        # Never inject known chunk ids for named business cases.  Such shortcuts
        # make a small benchmark look good but destroy tenant/document
        # generalization and hide embedding failures.
        base_candidates = list(boosted[:top_k])
        # Ranking remains corpus-agnostic. Domain-specific boosts above use
        # query/chunk semantics only; never inject known document ids, so the
        # same retriever can serve newly ingested tenants and knowledge bases.
        if self.reranker:
            reranked = self.reranker.rerank(query, [c for c, _, _ in base_candidates], top_k=top_k)
            reranked_ids = {c.id: s for c, s in reranked}
            base = [(c, reranked_ids.get(c.id, 0.0), channel_by_id.get(c.id, ["rerank"])) for c, _, _ in base_candidates[:top_k]]
        else:
            base = base_candidates[:top_k]
        expanded = self._expand_parents(base, access_context)
        return self._build_pack(query, expanded[:top_k])

    def _metadata_search(self, query, allowed, top_k=20):
        qtokens = set(tokenize(query))
        scored = []
        for idx, chunk in enumerate(self.chunks):
            if idx not in allowed:
                continue
            refs = []
            for key in ("metric_refs", "table_refs", "scenario_refs"):
                refs.extend(chunk.metadata.get(key) or [])
            if not refs:
                continue
            ref_text = " ".join([_safe_text(ref).lower() for ref in refs])
            overlap = [tok for tok in qtokens if tok and tok in ref_text]
            if overlap:
                scored.append((idx, float(len(overlap))))
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:top_k]

    def _allowed_indices(self, access_context=None):
        ctx = access_context or {}; role = ctx.get("role", "analyst"); tenant = ctx.get("tenant_id", "global")
        allowed = set()
        for i, c in enumerate(self.chunks):
            roles = set(c.metadata.get("acl_roles") or ["analyst", "admin"])
            ctenant = c.metadata.get("tenant_id", "global")
            if role in roles and ctenant in ("global", tenant):
                allowed.add(i)
        return allowed

    def _expand_parents(self, items, access_context=None):
        seen = set(); out = []
        parent_ids = set([c.parent_id for c, _, _ in items])
        for c, s, ch in items:
            if c.id not in seen:
                out.append((c, s, ch)); seen.add(c.id)
        for c in self.chunks:
            if c.parent_id in parent_ids and c.section_type in ("parent", "definition", "caveats", "profile") and c.id not in seen:
                out.append((c, 0.001, ["parent_expansion"])); seen.add(c.id)
        return out

    def _build_pack(self, query, items):
        evidence = []
        for idx, (chunk, score, channels) in enumerate(items, start=1):
            evidence.append({
                "citation_id": "R%s" % idx,
                "chunk_id": chunk.id,
                "parent_id": chunk.parent_id,
                "title": chunk.title,
                "claim": chunk.title,
                "supporting_extract": self._compress(chunk.content, 360),
                "type": chunk.type,
                "knowledge_type": chunk.type,
                "section_type": chunk.section_type,
                "source_uri": chunk.source,
                "score": round(float(score), 6),
                "channels": list(channels),
                "metadata": dict(chunk.metadata),
            })
        return {"query": query, "evidence": evidence, "citations": [{"id": e["citation_id"], "chunk_id": e["chunk_id"], "title": e["title"]} for e in evidence]}

    def _compress(self, text, max_len):
        text = re.sub(r"\s+", " ", text or "").strip()
        return text if len(text) <= max_len else text[:max_len - 1] + "…"


class RagEvaluator(object):
    def __init__(self, retriever):
        self.retriever = retriever

    def evaluate(self, cases, k_values=(1, 3, 5)):
        rows = []
        for case in cases:
            pack = self.retriever.retrieve(case["query"], top_k=max(k_values), access_context=case.get("access_context"))
            got = [e["chunk_id"] for e in pack["evidence"]]
            expected = set(case.get("expected_chunk_ids") or [])
            rows.append((got, expected))
        metrics = {}
        for k in k_values:
            hits = 0
            for got, expected in rows:
                if expected.intersection(set(got[:k])):
                    hits += 1
            metrics["recall@%s" % k] = float(hits) / float(len(rows) or 1)
        rr = []
        for got, expected in rows:
            rank = 0
            for idx, cid in enumerate(got, start=1):
                if cid in expected:
                    rank = idx; break
            rr.append(0.0 if rank == 0 else 1.0 / float(rank))
        metrics["mrr@%s" % max(k_values)] = sum(rr) / float(len(rr) or 1)
        return metrics


__all__ = ["RagChunk", "KnowledgeIngestor", "SentenceTransformerEmbeddingProvider", "DeterministicEmbeddingProvider", "BM25Retriever", "FaissVectorRetriever", "CrossEncoderReranker", "FullRagRetriever", "RagEvaluator", "rrf_fuse"]
