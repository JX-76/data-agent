"""Shared configuration: API endpoints, model selection, schema constants."""

import os
from pathlib import Path

# ── LLM API ──────────────────────────────────────────────
# All LLM callers use the same OpenAI-compatible DeepSeek endpoint.  Values
# are configurable to make staging/proxy deployments explicit rather than
# silently pinning a model in source code.
DEEPSEEK_BASE = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")

# Try multiple sources for the API key

def _resolve_api_key():
    # 1. Environment variable
    k = os.environ.get("DEEPSEEK_API_KEY", "")
    if k:
        return k
    # 2. OpenClaw config
    try:
        import json as _json
        cfg_path = Path.home() / ".openclaw" / "openclaw.json"
        if cfg_path.exists():
            cfg = _json.loads(cfg_path.read_text())
            k = cfg.get("models", {}).get("providers", {}).get("deepseek", {}).get("apiKey", "")
            if k and k != "__OPENCLAW_REDACTED__":
                return k
    except Exception as e:
        logger.warning("bare_exception_caught", error=str(e))
        pass
    return ""

DEEPSEEK_KEY = _resolve_api_key()
# DEEPSEEK_MODEL is the single runtime default.  Per-stage overrides remain
# available, but all fall back to the configured model instead of an obsolete
# hard-coded identifier.
DEFAULT_DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
ROUTER_MODEL = os.environ.get("DEEPSEEK_ROUTER_MODEL", DEFAULT_DEEPSEEK_MODEL)
DATA_GEN_MODEL = os.environ.get("DEEPSEEK_DATA_GEN_MODEL", DEFAULT_DEEPSEEK_MODEL)
ANALYSIS_MODEL = os.environ.get("DEEPSEEK_ANALYSIS_MODEL", DEFAULT_DEEPSEEK_MODEL)

# ── DB schema that LLM needs to know for mock data generation ─
DB_SCHEMA = {
    "fct_orders": {
        "columns": [
            ("order_id",   "TEXT PRIMARY KEY"),
            ("store_id",   "TEXT NOT NULL"),
            ("product_id", "TEXT NOT NULL"),
            ("sell_through","REAL NOT NULL"),
            ("channel",    "TEXT NOT NULL"),
            ("order_status","TEXT NOT NULL"),
            ("paid_at",    "TEXT NOT NULL"),   # ISO-8601
            ("user_id",    "TEXT NOT NULL"),
        ],
        "note": "channel values: online/offline/live. order_status values: paid/completed/cancelled/refunded. product_id references dim_product."
    },
    "dim_store": {
        "columns": [
            ("store_id", "TEXT PRIMARY KEY"),
            ("store_name","TEXT NOT NULL"),
            ("region",   "TEXT NOT NULL"),
        ],
        "note": "region values: 华东/华南/华北/西南/华中."
    },
    "dim_product": {
        "columns": [
            ("product_id",   "TEXT PRIMARY KEY"),
            ("product_name", "TEXT NOT NULL"),
            ("category",     "TEXT NOT NULL"),
            ("unit_price",   "REAL NOT NULL"),
        ],
        "note": "category values: 女装/男装/数码/家居/美妆."
    },
}

# ── Database Configuration ────────────────────────────────
DB_TYPE = os.environ.get("DATA_AGENT_DB_TYPE", "sqlite")
DB_PATH = os.environ.get("DATA_AGENT_DB_PATH", ":memory:")
DB_POOL_MIN = int(os.environ.get("DATA_AGENT_DB_POOL_MIN", "1"))
DB_POOL_MAX = int(os.environ.get("DATA_AGENT_DB_POOL_MAX", "5"))
DB_POOL_TIMEOUT = float(os.environ.get("DATA_AGENT_DB_POOL_TIMEOUT", "5.0"))

# ── Routing mode ─────────────────────────────────────────
# "regex": regex-based NLU (fast, deterministic, no API cost)
# "llm":   LLM-based routing (flexible, handles ambiguous queries)
ROUTER_MODE = os.environ.get("ROUTER_MODE", "regex")

# ── Semantic layer summary for LLM router prompt ────────
SEMANTIC_SUMMARY = {
    "metrics": {
        "gmv":         {"description":"已支付订单成交总额，未扣除退款", "synonyms":["GMV","销售额","成交额","业绩","revenue"], "allowed_dimensions":["date","channel","region","category"]},
        "order_count": {"description":"有效订单数量", "synonyms":["订单数","交易笔数","单量","orders"], "allowed_dimensions":["date","channel","region","category"]},
        "aov":         {"description":"客单价=GMV/订单数", "synonyms":["客单价","AOV","平均订单金额"], "allowed_dimensions":["date","channel","region","category"]},
        "avg_price":   {"description":"商品平均单价", "synonyms":["均价","平均价格","average price"], "allowed_dimensions":["date","category"]},
    },
    "dimensions": {
        "date":    {"description":"日期（按天）"},
        "channel": {"description":"渠道：online/offline/live"},
        "region":  {"description":"区域：华东/华南/华北/西南/华中"},
        "category":{"description":"商品品类：女装/男装/数码/家居/美妆"},
    },
    "models": {
        "order_detail":     {"description":"订单明细单表视图，一人一单粒度", "visible_dimensions":["date","channel","region"]},
        "user_summary":    {"description":"用户概览视图", "visible_dimensions":["date","channel"]},
        "product_analysis":{"description":"商品分析视图，按品类维度", "visible_dimensions":["date","channel","category"]},
    },
}
