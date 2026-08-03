# Changelog

All notable changes to Data Agent.

## [1.2.0] - 2026-07-02

### Added
- **Audit log**: JSONL audit trail recording every query (who/what/when/result)
- **Token cost tracker**: per-model pricing table, daily cost accumulation, budget alerts
- **Data masking**: PII redaction (user_id → USR****, email, phone, IP)
- **Multi-model fallback**: DeepSeek V4 Flash → DeepSeek Chat → regex chain
- **Prompt version manager**: version-controlled prompts with diff and rollback
- **LLM router benchmark**: 22 test cases with accuracy baseline reporting
- **Production test suite**: 21 tests (5 perf, 9 security, 4 integration, 2 benchmark)

### Changed
- SQL injection sanitization: now blocks AND/OR/UNION patterns in addition to DROP/DELETE
- `reset_db()` properly closes pool singleton for clean test teardown

## [1.1.0] - 2026-07-01

### Added
- **Rate limiter**: sliding window per-IP with configurable RPM and block duration
- **Config manager**: dev/staging/prod profiles with env override and hot-reload watch
- **Graceful shutdown**: SIGTERM drains inflight requests, 503 on new requests during drain
- **Connection pool**: thread-safe with retry, health check, idle timeout (SQLite default)
- **SQL dialect adapter**: framework for PostgreSQL/MySQL/DuckDB (SQLite active)
- **Schema migration engine**: version-tracked DDL with rollback support (v2: indexes)
- **SQL injection hardening**: `AgentRuntime._sanitize_value()` and `_sanitize_identifier()`

### Changed
- `db_executor.py` rewritten: pool-backed, migration-driven, parameterized queries
- `server.py` integrated rate limiter middleware, config-driven startup, inflight tracking

## [1.0.0] - 2026-07-01 — Team Production Release

### Added
- **FastAPI server** with 9 endpoints (query, stream, sessions, cache, health)
- **CLI tool** (`data-agent query/serve/check/session/mermaid`)
- **Docker** deployment (Dockerfile + docker-compose.yml)
- **Structured logging** (structlog JSON/text)
- **Health check** endpoint with cache stats
- **API key authentication** middleware
- **In-memory query cache** (LFU with TTL)
- **README** with API docs and quick-start

## [0.1.0] - 2026-06-30 — MVP

### Added
- **Nucleus DAG** framework: 12-node graph with conditional routing
- **Semantic layer**: 3 models, 4 metrics, 4 dimensions (YAML-driven)
- **CTE SQL compiler**: deterministic SQL generation from semantic plans
- **ReAct Agent**: 13 tools with 2-retry self-correction loop
- **Diagnosis engine**: 14 labels with detection rules and remediation strategies
- **Score bridge**: deepeval → Langfuse score push (5 custom metrics)
- **Session persistence**: multi-turn context inheritance with follow-up detection
- **Data flywheel**: run recording, trend analysis, strategy recommendation
- **6 route intents**: metric_query, breakdown, filter_value, merge, compare_periods, blocked
- **144 tests**: nucleus, golden queries, eval gate, E2E, diagnosis
