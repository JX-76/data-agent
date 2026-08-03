"""Production API server for Data Agent.

FastAPI-based REST server with:
- API key authentication
- In-memory query result cache (LFU)
- Rate limiting (sliding window, per-IP)
- Configuration management (profiles + hot-reload)
- Structured logging (structlog)
- Graceful shutdown (SIGTERM drains inflight requests)
- Health check endpoint
- SSE streaming for long-running queries
- Multiple session management

Usage:
    DATA_AGENT_PROFILE=prod python3 src/server.py
    uvicorn src.server:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import signal
import sys
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import structlog
from fastapi import FastAPI, HTTPException, Request, Depends, Query as QueryParam
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config_manager import load_config, get_config, reload_config
from ratelimit import RateLimiter, RateLimitMiddleware
from distributed_cache import DistributedCache
from validation import InputValidator, OutputValidator, ValidationError
from log_masking import log_filter
from tracing import TraceContext, tracer

# ── Structured Logging ──

def _setup_logging(cfg):
    processors = [
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.stdlib.add_log_level,
    ]
    if cfg.logging.format == "json":
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer())
    structlog.configure(processors=processors, cache_logger_on_first_use=True)

cfg = load_config()
_setup_logging(cfg)
logger = structlog.get_logger("data-agent")


# ── Lightweight in-memory stream task store ──
_STREAM_TASKS = {}


def _stream_task_snapshot(task_id):
    item = _STREAM_TASKS.get(task_id) or {}
    return dict(item)


async def _run_stream_release_task(task_id, req, headers, trace_id, started_ms):
    task = _STREAM_TASKS.setdefault(task_id, {})
    task.update({'task_id': task_id, 'trace_id': trace_id, 'status': 'running', 'events': list(task.get('events') or [])})
    task['events'].append({'type': 'progress', 'task_id': task_id, 'trace_id': trace_id, 'step': 'release_api'})
    try:
        from release_api import ask_release
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None, lambda: ask_release(req.query, session_id=req.session_id, use_llm=req.use_llm, headers=headers))
        response['type'] = 'complete'
        response['task_id'] = task_id
        response['trace_id'] = response.get('trace_id') or trace_id
        response['legacy_endpoint'] = '/query/stream'
        task.update({'status': response.get('status') or 'ok', 'result': response, 'completed_at': time.time()})
        task['events'].append(response)
    except Exception as e:
        from release_api import _runtime_error_result, _envelope, _new_id
        result = _runtime_error_result('server_stream', 'server_stream_exception', str(e), True, trace_id)
        error = _envelope(req.query, req.session_id, result, started_ms, _new_id('audit'))
        error['type'] = 'error'
        error['task_id'] = task_id
        error['legacy_endpoint'] = '/query/stream'
        task.update({'status': 'error', 'result': error, 'completed_at': time.time()})
        task['events'].append(error)
    return task.get('result')


# ── Request/Response Models ──

class QueryRequest(BaseModel):
    query: str = Field(..., description="Natural language query", min_length=1, max_length=2000)
    use_llm: bool = Field(default=False)
    session_id: Optional[str] = Field(default=None)
    cache_ttl: int = Field(default=300, ge=0, le=3600)


class ResumeRequest(BaseModel):
    session_id: str
    choice_id: str


class ProviderConfigRequest(BaseModel):
    """Local single-user DeepSeek configuration; API key is write-only."""
    provider: str = Field(default="deepseek", min_length=1, max_length=40)
    api_key: Optional[str] = Field(default=None, min_length=4, max_length=512)
    base_url: Optional[str] = Field(default=None, max_length=500)
    model: Optional[str] = Field(default=None, max_length=160)


class QueryResponse(BaseModel):
    status: str
    intent: Optional[str] = None
    model: Optional[str] = None
    metric: Optional[str] = None
    sql: Optional[str] = None
    results: Optional[list] = None
    insight: Optional[dict] = None
    diagnosis: Optional[dict] = None
    trace_id: Optional[str] = None
    cached: bool = False
    duration_ms: float = 0


class HealthResponse(BaseModel):
    status: str
    version: str
    uptime_seconds: float
    cache_stats: dict
    session_count: int


# ── Query Cache ──

class QueryCache:
    """In-memory LFU cache with TTL."""

    def __init__(self, max_size: int = 500):
        self.max_size = max_size
        self._store: dict[str, tuple[float, dict]] = {}  # key → (expiry, result)
        self._hits = 0
        self._misses = 0

    def _key(self, query: str, use_llm: bool) -> str:
        return hashlib.sha256(f"{query}|{use_llm}".encode()).hexdigest()[:16]

    def get(self, query: str, use_llm: bool = False) -> Optional[dict]:
        k = self._key(query, use_llm)
        entry = self._store.get(k)
        if entry:
            expiry, result = entry
            if time.time() < expiry:
                self._hits += 1
                return result
            del self._store[k]
        self._misses += 1
        return None

    def set(self, query: str, use_llm: bool, result: dict, ttl: int = 300):
        k = self._key(query, use_llm)
        self._store[k] = (time.time() + ttl, result)
        # LFU eviction
        if len(self._store) > self.max_size:
            oldest = min(self._store.keys(), key=lambda k: self._store[k][0])
            del self._store[oldest]

    @property
    def stats(self) -> dict:
        total = self._hits + self._misses
        return {
            "size": len(self._store),
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": f"{self._hits / max(1, total):.1%}",
        }


# ── Auth ──

def _get_api_keys() -> set[str]:
    """Get API keys from config (supports hot-reload)."""
    return set(get_config().auth.api_keys)


def _auth_enabled() -> bool:
    return get_config().auth.enabled and bool(_get_api_keys())


async def verify_api_key(request: Request):
    """Middleware: verify X-API-Key header if auth is enabled."""
    if not _auth_enabled():
        return
    api_key = request.headers.get("X-API-Key", "")
    if api_key not in _get_api_keys():
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


# ── App Lifecycle ──

# Initialize distributed cache with Redis support
cache = DistributedCache(config={
    "max_size": get_config().cache.max_entries,
    "redis_host": os.environ.get("REDIS_HOST"),
    "redis_port": int(os.environ.get("REDIS_PORT", "6379")),
    "redis_db": int(os.environ.get("REDIS_DB", "0")),
    "redis_password": os.environ.get("REDIS_PASSWORD"),
})
limiter = RateLimiter(
    max_requests=get_config().rate_limit.max_requests_per_minute,
    window_seconds=60,
    block_seconds=get_config().rate_limit.block_seconds,
)
session_manager = None  # Lazy init
_session_access_index = {}  # session_id -> safe owner metadata for legacy session endpoints
start_time = time.time()
_inflight_requests = 0
_inflight_lock = asyncio.Lock()
_shutting_down = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    global start_time, _shutting_down
    start_time = time.time()

    # Uvicorn owns process signals.  On Windows' ProactorEventLoop,
    # add_signal_handler is intentionally unsupported; lifecycle shutdown below
    # still drains requests, so only register optional Unix loop handlers.
    loop = asyncio.get_running_loop()
    if os.name != "nt":
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(_handle_shutdown(s)))
            except (NotImplementedError, RuntimeError):
                logger.warning("signal_handler_not_registered", signal=sig.name)

    logger.info("server_start",
                profile=os.environ.get("DATA_AGENT_PROFILE", "dev"),
                auth_enabled=_auth_enabled(),
                rate_limit_enabled=get_config().rate_limit.enabled,
                cache_size=get_config().cache.max_entries)

    # Start config hot-reload
    get_config().on_reload(lambda: logger.info("config_reloaded"))
    try:
        get_config().watch(interval_seconds=10.0)
    except Exception as e:
        logger.warning("bare_exception_caught", error=str(e))
        pass

    yield

    # Graceful shutdown: drain inflight requests
    _shutting_down = True
    logger.info("server_stopping", inflight=_inflight_requests)

    # Wait for inflight requests (up to 30s)
    deadline = time.time() + 30
    while _inflight_requests > 0 and time.time() < deadline:
        await asyncio.sleep(0.5)
        logger.info("shutdown_waiting", remaining=_inflight_requests)

    # Save sessions
    if session_manager:
        try:
            session_manager.save()
            logger.info("sessions_saved")
        except Exception as e:
            logger.warning("bare_exception_caught", error=str(e))
            pass

    logger.info("server_stop", remaining_inflight=_inflight_requests)


async def _handle_shutdown(sig):
    logger.info("signal_received", signal=sig.name)
    # Signal uvicorn to stop
    import uvicorn
    # Graceful: let uvicorn handle it via its own shutdown flow


app = FastAPI(
    title="Data Agent API",
    description="Natural language to SQL analytics agent with semantic layer",
    version="1.0.0",
    lifespan=lifespan,
)

# Middleware stack (order matters: rate limit → CORS → auth via Depends)
if get_config().rate_limit.enabled:
    app.add_middleware(RateLimitMiddleware, limiter=limiter)
    logger.info("rate_limit_enabled",
                max_rpm=get_config().rate_limit.max_requests_per_minute,
                block_s=get_config().rate_limit.block_seconds)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Inflight request tracking middleware
@app.middleware("http")
async def track_inflight(request: Request, call_next):
    global _inflight_requests
    if _shutting_down:
        return JSONResponse(
            status_code=503,
            content={"status": "shutting_down", "message": "Server is shutting down"},
        )
    async with _inflight_lock:
        _inflight_requests += 1
    try:
        response = await call_next(request)
        return response
    finally:
        async with _inflight_lock:
            _inflight_requests -= 1


# Input validation middleware
@app.middleware("http")
async def validate_request(request: Request, call_next):
    """Validate incoming requests for security."""
    # Only validate POST/PUT requests with body
    if request.method in ("POST", "PUT", "PATCH"):
        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type:
            try:
                body = await request.json()
                if "query" in body and isinstance(body["query"], str):
                    validator = InputValidator()
                    try:
                        body["query"] = validator.validate_query(body["query"])
                    except ValidationError as e:
                        return JSONResponse(
                            status_code=400,
                            content={"status": "error", "message": str(e)}
                        )
            except Exception:
                pass  # Let FastAPI handle JSON parsing errors
    
    return await call_next(request)


# ── BI Dashboard / Release v1 Routes ──
from bi_api import router as bi_router
app.include_router(bi_router)
try:
    from release_api import router as release_router
    if release_router is not None:
        app.include_router(release_router)
    else:
        logger.warning("release_router_not_mounted", error="FastAPI router is unavailable in this environment")
except Exception as e:
    logger.warning("release_router_not_mounted", error=str(e))

# ── Static Files ──
# Dashboard UI
ui_path = Path(__file__).resolve().parent / "ui"
if ui_path.is_dir():
    app.mount("/ui", StaticFiles(directory=str(ui_path), html=True), name="ui")
    
    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def root():
        return HTMLResponse((ui_path / "index.html").read_text(encoding="utf-8"))
    
    @app.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
    async def dashboard():
        return HTMLResponse((ui_path / "dashboard.html").read_text(encoding="utf-8"))
    
    logger.info("ui_mounted", path=str(ui_path))

def _get_session_manager():
    global session_manager
    if session_manager is None:
        from session import SessionManager
        session_manager = SessionManager()
    return session_manager


def _build_response(result: dict, trace_id: str = "", cached: bool = False, duration_ms: float = 0) -> dict:
    diagnosis = result.get("diagnosis", {})
    if isinstance(diagnosis, dict):
        diagnosis = diagnosis

    return {
        "status": result.get("status", "error"),
        "intent": result.get("intent"),
        "model": result.get("model"),
        "metric": result.get("metric"),
        "dimensions": result.get("dimensions"),
        "sql": result.get("sql"),
        "results": result.get("results"),
        "analysis": result.get("analysis"),
        "insight": result.get("insight"),
        "diagnosis": diagnosis,
        "trace_id": trace_id,
        "cached": cached,
        "duration_ms": duration_ms,
    }


def _server_access_context(request: Optional[Request] = None) -> dict:
    """Resolve trusted identity for legacy metadata endpoints."""
    from release_api import _resolve_access_context
    headers = dict(getattr(request, "headers", {}) or {}) if request is not None else {}
    return _resolve_access_context(headers=headers)


def _is_admin_context(access_context: dict) -> bool:
    role = (access_context or {}).get("role") or ""
    roles = ((access_context or {}).get("metadata") or {}).get("roles") or []
    return role in ("admin", "security_admin") or "admin" in roles or "security_admin" in roles


def _session_meta(session_id: str) -> dict:
    return _session_access_index.get(session_id) or {}


def _remember_session_owner(session_id: str, access_context: dict):
    if not session_id:
        return
    if session_id not in _session_access_index:
        _session_access_index[session_id] = {
            "tenant_id": (access_context or {}).get("tenant_id") or "default",
            "user_id": (access_context or {}).get("user_id") or "anonymous",
            "role": (access_context or {}).get("role") or "anonymous",
            "created_at": int(time.time()),
        }


def _can_access_session(session_id: str, access_context: dict) -> bool:
    meta = _session_meta(session_id)
    if not meta:
        return _is_admin_context(access_context)
    if _is_admin_context(access_context):
        return meta.get("tenant_id") == (access_context or {}).get("tenant_id")
    return (meta.get("tenant_id") == (access_context or {}).get("tenant_id") and
            meta.get("user_id") == (access_context or {}).get("user_id"))


def _blocked_metadata_response(resource: str, reason: str, access_context: dict) -> JSONResponse:
    from release_api import _runtime_error_result, _envelope, _new_id
    trace_id = _new_id("trace")
    result = _runtime_error_result("server_metadata", reason, "无权访问该资源或资源不存在。", False, trace_id)
    result["status"] = "blocked"
    result["answer_type"] = "error"
    result["blocked_reason"] = reason
    result["tenant_id"] = (access_context or {}).get("tenant_id") or "default"
    env = _envelope(resource, None, result, int(time.time() * 1000), _new_id("audit"))
    env["legacy_endpoint"] = resource
    return JSONResponse(env, status_code=200)


def _safe_turn_metadata(turn, index: int) -> dict:
    result = getattr(turn, "result", {}) or {}
    if not isinstance(result, dict):
        result = {}
    return {
        "index": index,
        "timestamp": int(getattr(turn, "timestamp", 0) or 0),
        "status": result.get("status") or "error",
        "answer_type": result.get("answer_type") or result.get("intent"),
        "metric": result.get("metric"),
        "task_type": (result.get("plan") or {}).get("task_type") if isinstance(result.get("plan"), dict) else result.get("intent"),
        "trace_id": result.get("trace_id") or ((result.get("diagnostics") or {}).get("trace_id") if isinstance(result.get("diagnostics"), dict) else None),
        "evidence_ids": list(result.get("evidence_ids") or []),
        "has_verified_evidence": bool(result.get("evidence_ids") or result.get("provenance")),
    }


def _safe_session_history_payload(session_id: str, session, access_context: dict) -> dict:
    meta = _session_meta(session_id)
    turns = list(getattr(session, "turns", []) or [])
    return {
        "contract": "legacy_session_metadata_v1",
        "status": "ok",
        "session_id": session_id,
        "tenant_id": meta.get("tenant_id") or (access_context or {}).get("tenant_id") or "default",
        "owner_user_id": meta.get("user_id") if _is_admin_context(access_context) else None,
        "state": getattr(session, "state", "unknown"),
        "created_at": int(getattr(session, "created_at", 0) or 0),
        "updated_at": int(getattr(session, "updated_at", 0) or 0),
        "turn_count": len(turns),
        "turns": [_safe_turn_metadata(t, i) for i, t in enumerate(turns)],
        "stats": {"turn_count": len(turns), "state": getattr(session, "state", "unknown")},
        "limitations": ["该接口只返回会话元数据，不返回原始 query、SQL、行级结果或自然语言结论。"],
    }


def _safe_audit_event(event: dict, access_context: dict) -> dict:
    from masking_policy import sanitize_output
    event = dict(event or {})
    details = dict(event.get("details") or {})
    for key in ("query", "sql", "raw", "rows", "result", "answer", "prompt"):
        details.pop(key, None)
    safe = {
        "timestamp": event.get("timestamp"),
        "event_type": event.get("event_type"),
        "action": event.get("action"),
        "resource": event.get("resource"),
        "event_hash": event.get("event_hash"),
        "status": details.get("status"),
        "trace_id": details.get("trace_id"),
        "audit_id": details.get("audit_id"),
        "details": details,
    }
    if _is_admin_context(access_context):
        safe["user_id"] = event.get("user_id")
    return sanitize_output(safe)


def _admin_only_response(resource: str, access_context: dict):
    if _is_admin_context(access_context):
        return None
    return _blocked_metadata_response(resource, "admin_required", access_context)


def _safe_observability_payload(value):
    from release_api import _safe_release_payload
    return _safe_release_payload(value)


def _safe_prompt_history_item(item):
    if isinstance(item, dict):
        blocked = set(["prompt", "content", "template", "text", "body", "messages", "system", "user"])
        return dict((k, v) for k, v in item.items() if str(k).lower() not in blocked)
    return {"version_ref": str(item)[:80]}


# ── Routes ──

@app.get("/api/provider-config")
async def get_provider_config(_auth: None = Depends(verify_api_key)):
    """Return safe provider configuration metadata; API keys are never returned."""
    from user_provider_config import LocalProviderConfigStore, default_public_view, public_view
    record = LocalProviderConfigStore().load()
    return public_view(record) if record else default_public_view()


@app.post("/api/provider-config")
async def save_provider_config(req: ProviderConfigRequest, _auth: None = Depends(verify_api_key)):
    """Save a local single-user provider configuration without returning its key."""
    from user_provider_config import LocalProviderConfigStore, ProviderConfigError, public_view
    try:
        record = LocalProviderConfigStore().save(
            provider=req.provider, api_key=req.api_key, base_url=req.base_url, model=req.model)
        logger.info("provider_config_saved", provider=record.get("provider"), source="user_config")
        return public_view(record)
    except ProviderConfigError as exc:
        return JSONResponse({"contract": "user_provider_config_v1", "status": "error",
                             "error": {"code": exc.code, "message": exc.message}}, status_code=400)


@app.post("/api/provider-config/validate")
async def validate_provider_config(_auth: None = Depends(verify_api_key)):
    """Perform a minimal authenticated provider check; no provider response is exposed."""
    from user_provider_config import LocalProviderConfigStore, ProviderConfigError, public_view
    from deepseek_adapter import DeepSeekAdapter, DeepSeekError
    store = LocalProviderConfigStore()
    record = store.load()
    if not record:
        return JSONResponse({"contract": "user_provider_config_v1", "status": "error",
                             "error": {"code": "not_configured", "message": "请先保存 API 配置。"}}, status_code=400)
    try:
        adapter = DeepSeekAdapter(provider_store=store)
        adapter._request_json("/models", timeout_seconds=min(adapter.timeout_seconds, 15.0))
        record = store.mark_validated("validated")
        logger.info("provider_config_validated", provider=record.get("provider"), source="user_config")
        return public_view(record)
    except (DeepSeekError, ProviderConfigError) as exc:
        code = getattr(exc, "code", "validation_failed")
        try:
            record = store.mark_validated("validation_failed")
            body = public_view(record)
        except ProviderConfigError:
            body = {"contract": "user_provider_config_v1", "provider": "deepseek", "status": "validation_failed"}
        body["error"] = {"code": code, "message": "Provider 连接校验失败，请检查 Key、Base URL、模型权限或网络。"}
        return JSONResponse(body, status_code=400)


@app.delete("/api/provider-config")
async def delete_provider_config(_auth: None = Depends(verify_api_key)):
    """Delete the local user configuration and fall back to environment settings."""
    from user_provider_config import LocalProviderConfigStore, ProviderConfigError, default_public_view
    try:
        deleted = LocalProviderConfigStore().delete()
        logger.info("provider_config_deleted", source="user_config", deleted=deleted)
        result = default_public_view()
        result["deleted"] = deleted
        return result
    except ProviderConfigError as exc:
        return JSONResponse({"contract": "user_provider_config_v1", "status": "error",
                             "error": {"code": exc.code, "message": exc.message}}, status_code=400)


@app.get("/health", response_model=HealthResponse)
async def health():
    """Health check endpoint."""
    if _shutting_down:
        return JSONResponse(
            status_code=503,
            content={"status": "shutting_down", "version": "1.0.0"},
        )
    sm = _get_session_manager()
    return {
        "status": "healthy",
        "version": "1.0.0",
        "uptime_seconds": time.time() - start_time,
        "cache_stats": cache.stats,
        "rate_limit": limiter.stats(),
        "session_count": len(sm.sessions),
        "inflight_requests": _inflight_requests,
        "profile": os.environ.get("DATA_AGENT_PROFILE", "dev"),
    }


@app.post("/query")
async def query(
    req: QueryRequest,
    request: Request,
    _auth: None = Depends(verify_api_key),
):
    """Execute a natural language query against the data agent.

    Returns structured results including SQL, data, analysis, and diagnosis.

    Example:
        curl -X POST http://localhost:8000/query \
          -H "Content-Type: application/json" \
          -H "X-API-Key: your-key" \
          -d '{"query": "昨天 GMV 是多少？"}'
    """
    t0 = time.time()
    try:
        from release_api import ask_release
        env = ask_release(
            req.query,
            session_id=req.session_id,
            use_llm=req.use_llm,
            headers=dict(request.headers),
        )
        env["legacy_endpoint"] = "/query"
        env["cached"] = bool((env.get("raw") or {}).get("from_cache"))
        env["duration_ms"] = env.get("elapsed_ms", int((time.time() - t0) * 1000))
        logger.info("query_complete",
                    trace_id=(env.get("raw") or {}).get("trace_id") or env.get("audit_id"),
                    status=env.get("status"),
                    duration_ms=int(env.get("duration_ms") or 0))
        return JSONResponse(env)
    except Exception as e:
        from release_api import _runtime_error_result, _envelope, _new_id
        started_ms = int(t0 * 1000)
        trace_id = _new_id("trace")
        result = _runtime_error_result("server_query", "server_runtime_exception", str(e), True, trace_id)
        env = _envelope(req.query, req.session_id, result, started_ms, _new_id("audit"))
        env["legacy_endpoint"] = "/query"
        logger.error("query_error", trace_id=trace_id, error=str(e), duration_ms=int((time.time() - t0) * 1000))
        return JSONResponse(env, status_code=200)


@app.post("/query/stream")
async def query_stream(
    req: QueryRequest,
    request: Request,
    _auth: None = Depends(verify_api_key),
):
    """Execute a query with SSE streaming and a resumable in-memory task record."""
    trace_id = uuid.uuid4().hex
    task_id = "stream_" + uuid.uuid4().hex
    started_ms = int(time.time() * 1000)
    headers = dict(request.headers)
    _STREAM_TASKS[task_id] = {'task_id': task_id, 'trace_id': trace_id, 'status': 'queued', 'events': []}

    async def event_stream():
        # Start work when the stream is actually consumed, not when the
        # StreamingResponse is constructed.  This keeps the task on the
        # consumer's event loop for direct adapters/tests as well as ASGI, and
        # avoids a cancelled queued task leaving this loop unbounded.
        background = asyncio.create_task(
            _run_stream_release_task(task_id, req, headers, trace_id, started_ms))
        yield f"data: {json.dumps({'type': 'start', 'task_id': task_id, 'trace_id': trace_id})}\n\n"
        sent = 0
        while True:
            task = _STREAM_TASKS.get(task_id) or {}
            events = list(task.get('events') or [])
            for event in events[sent:]:
                sent += 1
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            if task.get('status') not in ('queued', 'running'):
                break
            await asyncio.sleep(0.01)
        yield "data: [DONE]\n\n"
        await background

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/tasks/{task_id}")
async def task_status(task_id: str, _auth: None = Depends(verify_api_key)):
    """Return metadata/result for a stream task after client reconnect/disconnect."""
    item = _stream_task_snapshot(task_id)
    if not item:
        return JSONResponse({'contract': 'stream_task_v1', 'status': 'error', 'error': {'code': 'task_not_found'}, 'task_id': task_id}, status_code=404)
    item['contract'] = 'stream_task_v1'
    return JSONResponse(item)


@app.post("/sessions")
async def create_session(
    request: Request,
    _auth: None = Depends(verify_api_key),
):
    """Create a new multi-turn session."""
    access = _server_access_context(request)
    sm = _get_session_manager()
    sid = sm.create()
    _remember_session_owner(sid, access)
    logger.info("session_created", session_id=sid, tenant_id=access.get("tenant_id"))
    return {"contract": "legacy_session_metadata_v1", "status": "ok", "session_id": sid,
            "tenant_id": access.get("tenant_id") or "default",
            "limitations": ["会话接口只返回元数据；最终分析请使用 /query 或 /api/ask。"]}


@app.get("/sessions/{session_id}/history")
async def session_history(
    session_id: str,
    request: Request,
    _auth: None = Depends(verify_api_key),
):
    """Get safe metadata-only conversation history for a session."""
    access = _server_access_context(request)
    sm = _get_session_manager()
    if session_id not in sm.sessions or not _can_access_session(session_id, access):
        return _blocked_metadata_response("/sessions/{session_id}/history", "session_access_denied", access)
    return _safe_session_history_payload(session_id, sm.sessions[session_id], access)


@app.post("/sessions/{session_id}/resume")
async def session_resume(
    session_id: str,
    req: ResumeRequest,
    request: Request,
    _auth: None = Depends(verify_api_key),
):
    """Resume a paused session (after clarification) with owner/tenant isolation."""
    access = _server_access_context(request)
    sm = _get_session_manager()
    if session_id not in sm.sessions or not _can_access_session(session_id, access):
        return _blocked_metadata_response("/sessions/{session_id}/resume", "session_access_denied", access)
    try:
        from release_api import resume_release
        env = resume_release(session_id, req.choice_id, headers=dict(request.headers))
        env["legacy_endpoint"] = "/sessions/{session_id}/resume"
        return JSONResponse(env)
    except Exception as e:
        from release_api import _runtime_error_result, _envelope, _new_id
        trace_id = _new_id("trace")
        result = _runtime_error_result("server_resume", "server_resume_exception", str(e), True, trace_id)
        env = _envelope("resume:%s" % req.choice_id, session_id, result, int(time.time() * 1000), _new_id("audit"))
        env["legacy_endpoint"] = "/sessions/{session_id}/resume"
        return JSONResponse(env, status_code=200)


@app.delete("/cache")
async def clear_cache(request: Request, _auth: None = Depends(verify_api_key)):
    """Clear the query result cache. Admin-only because it changes runtime state."""
    access = _server_access_context(request)
    blocked = _admin_only_response("/cache", access)
    if blocked is not None:
        return blocked
    global cache
    size = len(cache._store)
    cache = QueryCache(max_size=get_config().cache.max_entries)
    logger.info("cache_cleared", previous_size=size, tenant_id=access.get("tenant_id"))
    return {"contract": "legacy_operation_metadata_v1", "status": "ok", "resource": "/cache",
            "cleared": True, "previous_entries": size,
            "limitations": ["该接口只返回操作元数据，不返回缓存内容。"]}


@app.delete("/sessions/{session_id}")
async def delete_session(
    session_id: str,
    request: Request,
    _auth: None = Depends(verify_api_key),
):
    """Delete a session with owner/tenant isolation."""
    access = _server_access_context(request)
    sm = _get_session_manager()
    if session_id not in sm.sessions or not _can_access_session(session_id, access):
        return _blocked_metadata_response("/sessions/{session_id}", "session_access_denied", access)
    del sm.sessions[session_id]
    _session_access_index.pop(session_id, None)
    logger.info("session_deleted", session_id=session_id, tenant_id=access.get("tenant_id"))
    return {"contract": "legacy_session_metadata_v1", "status": "ok", "deleted": True, "session_id": session_id}


# ── Observability Endpoints ──

@app.get("/audit")
async def audit_log(
    request: Request,
    identity: str = QueryParam(None),
    status: str = QueryParam(None),
    since: str = QueryParam(None),
    limit: int = QueryParam(100),
    _auth: None = Depends(verify_api_key),
):
    """Query safe metadata-only audit entries."""
    access = _server_access_context(request)
    from audit import get_audit
    effective_identity = identity if _is_admin_context(access) else access.get("user_id")
    if identity and identity != access.get("user_id") and not _is_admin_context(access):
        return _blocked_metadata_response("/audit", "audit_identity_access_denied", access)
    safe_limit = max(1, min(int(limit or 100), 200))
    entries = get_audit().query(identity=effective_identity, status=status, since=since, limit=safe_limit)
    safe_entries = [_safe_audit_event(item, access) for item in entries]
    return {"contract": "legacy_audit_metadata_v1", "status": "ok", "entries": safe_entries,
            "count": len(safe_entries), "limitations": ["审计接口只返回脱敏元数据，不返回 query、SQL、prompt、raw result 或行级数据。"]}


@app.get("/costs")
async def cost_report(
    request: Request,
    hours: int = QueryParam(24),
    _auth: None = Depends(verify_api_key),
):
    """Token cost summary. Admin-only metadata endpoint."""
    access = _server_access_context(request)
    blocked = _admin_only_response("/costs", access)
    if blocked is not None:
        return blocked
    from token_tracker import get_tracker
    safe_hours = max(1, min(int(hours or 24), 168))
    s = get_tracker().summary(hours=safe_hours)
    return _safe_observability_payload({
        "contract": "legacy_observability_metadata_v1",
        "status": "ok",
        "resource": "/costs",
        "period_hours": s.period_hours,
        "total_calls": s.total_calls,
        "total_tokens": s.total_tokens,
        "total_cost_usd": round(s.total_cost_usd, 6),
        "by_model": s.by_model,
        "by_operation": s.by_operation,
    })


@app.get("/masks")
async def mask_rules(request: Request, _auth: None = Depends(verify_api_key)):
    """List active data masking rule metadata. Admin-only; no raw regex/patterns."""
    access = _server_access_context(request)
    blocked = _admin_only_response("/masks", access)
    if blocked is not None:
        return blocked
    from masking import get_masker
    rules = list(getattr(get_masker(), "active_rules", []) or [])
    safe_rules = []
    for idx, rule in enumerate(rules):
        if isinstance(rule, dict):
            safe_rules.append({"index": idx, "name": rule.get("name"), "field": rule.get("field"),
                               "type": rule.get("type") or rule.get("mask_type"), "enabled": rule.get("enabled", True)})
        else:
            safe_rules.append({"index": idx, "name": getattr(rule, "name", None),
                               "field": getattr(rule, "field", None), "type": getattr(rule, "type", None),
                               "enabled": getattr(rule, "enabled", True)})
    return _safe_observability_payload({"contract": "legacy_observability_metadata_v1", "status": "ok",
                                        "resource": "/masks", "rule_count": len(safe_rules), "rules": safe_rules,
                                        "limitations": ["不返回原始正则、样例敏感值或完整脱敏模板。"]})


@app.get("/fallback/stats")
async def fallback_stats(request: Request, _auth: None = Depends(verify_api_key)):
    """Multi-model fallback chain statistics. Admin-only metadata endpoint."""
    access = _server_access_context(request)
    blocked = _admin_only_response("/fallback/stats", access)
    if blocked is not None:
        return blocked
    from model_fallback import get_fallback_chain
    return _safe_observability_payload({"contract": "legacy_observability_metadata_v1", "status": "ok",
                                        "resource": "/fallback/stats", "stats": get_fallback_chain().stats})


@app.get("/prompts/{name}")
async def prompt_info(
    name: str,
    request: Request,
    _auth: None = Depends(verify_api_key),
):
    """Get prompt version metadata. Admin-only; never returns prompt body."""
    access = _server_access_context(request)
    blocked = _admin_only_response("/prompts/{name}", access)
    if blocked is not None:
        return blocked
    from prompt_manager import get_prompt_manager
    pm = get_prompt_manager()
    history_data = pm.history(name)
    if not history_data:
        raise HTTPException(status_code=404, detail="Prompt not found")
    safe_history = [_safe_prompt_history_item(item) for item in list(history_data or [])]
    active = pm.get_prompt(name)
    active_hash = hashlib.sha256((active or "").encode("utf-8")).hexdigest()[:16]
    return _safe_observability_payload({"contract": "legacy_prompt_metadata_v1", "status": "ok",
                                        "name": name, "active_hash": active_hash,
                                        "history_count": len(safe_history), "history": safe_history,
                                        "limitations": ["不返回 prompt 正文、system message 或 few-shot 内容。"]})


@app.post("/prompts/{name}/rollback")
async def prompt_rollback(
    name: str,
    request: Request,
    _auth: None = Depends(verify_api_key),
):
    """Rollback to a previous version. Admin-only placeholder; no prompt content returned."""
    access = _server_access_context(request)
    blocked = _admin_only_response("/prompts/{name}/rollback", access)
    if blocked is not None:
        return blocked
    return {"contract": "legacy_operation_metadata_v1", "status": "ok", "resource": "/prompts/{name}/rollback",
            "name": name, "message": "请通过受审计的变更流程提交目标版本；本接口不返回 prompt 正文。"}


@app.get("/benchmark")
async def router_benchmark(request: Request, _auth: None = Depends(verify_api_key)):
    """Run router benchmark (regex mode, fast). Admin-only aggregate metadata."""
    access = _server_access_context(request)
    blocked = _admin_only_response("/benchmark", access)
    if blocked is not None:
        return blocked
    from llm_eval_benchmark import LLMRouterBenchmark
    bench = LLMRouterBenchmark()
    report = bench.run()
    return _safe_observability_payload({
        "contract": "legacy_observability_metadata_v1",
        "status": "ok",
        "resource": "/benchmark",
        "total": report.total_cases,
        "passed": report.passed,
        "accuracy": f"{report.accuracy:.1%}",
        "by_intent": report.accuracy_by_intent,
        "by_category": report.accuracy_by_category,
        "duration_ms": report.duration_ms,
    })


# ── Main ──

if __name__ == "__main__":
    import uvicorn

    port = get_config().server.port
    host = get_config().server.host
    logger.info("starting_server", host=host, port=port,
                profile=os.environ.get("DATA_AGENT_PROFILE", "dev"))

    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level=get_config().logging.level.lower(),
        timeout_keep_alive=get_config().server.timeout_seconds,
    )
