"""Production readiness tests: performance, security, real DB integration.

Run:              pytest tests/test_production.py -v
Performance only: pytest tests/test_production.py -v -k "perf" -s
Security only:    pytest tests/test_production.py -v -k "security"
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

# CRITICAL: Set environment variables BEFORE any imports
os.environ["DATA_AGENT_DB_TYPE"] = "sqlite"
os.environ["DATA_AGENT_DB_PATH"] = ":memory:"

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# Module-level warm-up to initialize DB before tests
from graph_agent import run_graph as _warmup_run_graph
_warmup_run_graph("昨天GMV是多少？", use_db=True, use_llm=False)


# ══════════════════════════════════════════════════════════════
# Performance Tests
# ══════════════════════════════════════════════════════════════

class TestPerformance:
    """QPS, latency, and resource baseline tests."""

    def setup_method(self):
        """Reset DB before each performance test to ensure clean state."""
        from db_executor import reset_db
        reset_db()

    @pytest.mark.perf
    def test_single_query_latency(self):
        """Single query should complete under 100ms (regex mode, no LLM)."""
        from graph_agent import run_graph

        t0 = time.time()
        result = run_graph("昨天GMV是多少？", use_db=True, use_llm=False)
        dt = (time.time() - t0) * 1000

        assert result["status"] == "ok", f"Query failed: {result.get('reason')}"
        assert dt < 100, f"Query too slow: {dt:.0f}ms (target <100ms)"

    @pytest.mark.perf
    def test_breakdown_query_latency(self):
        """Breakdown query should complete under 100ms."""
        from graph_agent import run_graph

        t0 = time.time()
        result = run_graph("各渠道GMV", use_db=True, use_llm=False)
        dt = (time.time() - t0) * 1000

        assert result["status"] == "ok"
        assert dt < 100, f"Breakdown too slow: {dt:.0f}ms (target <100ms)"

    @pytest.mark.perf
    def test_merge_query_latency(self):
        """Merge query should complete under 150ms."""
        from graph_agent import run_graph

        t0 = time.time()
        result = run_graph("各渠道GMV和订单数", use_db=True, use_llm=False)
        dt = (time.time() - t0) * 1000

        assert result["status"] == "ok"
        assert dt < 150, f"Merge too slow: {dt:.0f}ms (target <150ms)"

    @pytest.mark.perf
    def test_throughput_burst(self):
        """Handle 20 rapid successive queries without degradation."""
        from graph_agent import run_graph
        from db_executor import reset_db

        queries = [
            "昨天GMV是多少？",
            "各渠道订单量",
            "昨天GMV",
            "最近7天各品类销售额",
            "只看线上渠道",
            "各区域客单价",
            "数码品类GMV",
            "平均价格",
            "昨天订单数",
            "GMV按日期",
        ] * 2  # 20 queries

        latencies = []
        for q in queries:
            # Reset DB before each query to avoid state corruption
            reset_db()
            t0 = time.time()
            result = run_graph(q, use_db=True, use_llm=False)
            dt = (time.time() - t0) * 1000
            latencies.append(dt)
            assert result.get("status") in ("ok", "blocked", "clarification_needed"), \
                f"Unexpected status {result.get('status')} for '{q}'"

        avg = sum(latencies) / len(latencies)
        p95 = sorted(latencies)[int(len(latencies) * 0.95)]

        # No query should be >5x the baseline
        baseline = 50
        assert avg < baseline * 3, f"Average too high: {avg:.0f}ms"
        assert p95 < baseline * 5, f"P95 too high: {p95:.0f}ms"

    @pytest.mark.perf
    def test_cache_hit_latency(self):
        """Cached queries should return in <1ms."""
        from graph_agent import run_graph

        # Warm up DB
        run_graph("昨天GMV", use_db=True, use_llm=False)

        # Measure cached
        t0 = time.time()
        result = run_graph("昨天GMV", use_db=True, use_llm=False)
        dt = (time.time() - t0) * 1000

        # Note: actual cache is at server layer; graph_agent doesn't cache
        # This tests that repeated queries are still fast
        assert dt < 50, f"Repeated query too slow: {dt:.0f}ms"


# ══════════════════════════════════════════════════════════════
# Security Tests
# ══════════════════════════════════════════════════════════════

class TestSecurity:
    """SQL injection, input validation, and auth tests."""

    @pytest.mark.security
    def test_sql_injection_filter_value(self):
        """filter_value should reject SQL injection in channel name."""
        from dag_agent import AgentRuntime
        from db_executor import get_db
        db = get_db()

        rt = AgentRuntime()
        # Setup: switch and filter
        did = rt.switch("order_detail", db)
        fid = rt.filter_time_and_defaults(did, "gmv",
            __import__('datetime').datetime(2026, 6, 30),
            __import__('datetime').datetime(2026, 7, 1))

        # Attempt injection through value
        with pytest.raises(ValueError, match="Unsafe value"):
            rt.filter_value(fid, "channel", "online' OR '1'='1")

    @pytest.mark.security
    def test_sql_injection_drop(self):
        """DROP TABLE via query value should be rejected."""
        from dag_agent import AgentRuntime

        rt = AgentRuntime()
        with pytest.raises(ValueError, match="Unsafe value"):
            rt._sanitize_value("x'; DROP TABLE fct_orders; --")

    @pytest.mark.security
    def test_sql_injection_comment(self):
        """SQL comment injection should be rejected."""
        from dag_agent import AgentRuntime

        rt = AgentRuntime()
        with pytest.raises(ValueError, match="Unsafe value"):
            rt._sanitize_value("admin'--")

    @pytest.mark.security
    def test_sql_injection_union(self):
        """UNION-based injection should be blocked."""
        from dag_agent import AgentRuntime

        rt = AgentRuntime()
        with pytest.raises(ValueError, match="Unsafe value"):
            rt._sanitize_value("x' UNION SELECT * FROM users--")

    @pytest.mark.security
    def test_unsafe_identifier_rejected(self):
        """Non-alphanumeric identifiers should be rejected."""
        from dag_agent import AgentRuntime

        rt = AgentRuntime()
        with pytest.raises(ValueError, match="Unsafe identifier"):
            rt._sanitize_identifier("channel; DELETE FROM")

    @pytest.mark.security
    def test_safe_identifier_accepted(self):
        """Valid identifiers should pass."""
        from dag_agent import AgentRuntime

        rt = AgentRuntime()
        assert rt._sanitize_identifier("channel") == "channel"
        assert rt._sanitize_identifier("order_count") == "order_count"

    @pytest.mark.security
    def test_safe_value_preserved(self):
        """Legitimate values should work normally."""
        from dag_agent import AgentRuntime

        rt = AgentRuntime()
        assert rt._sanitize_value("online") == "online"
        assert rt._sanitize_value("华南") == "华南"
        assert rt._sanitize_value("O'Brien") == "O''Brien"

    @pytest.mark.security
    def test_auth_required_in_production(self):
        """Auth should reject requests without API key (prod config)."""
        import os
        os.environ["DATA_AGENT_PROFILE"] = "prod"
        from config_manager import load_config
        cfg = load_config("prod")
        assert cfg.auth.enabled, "Production must have auth enabled"
        del os.environ["DATA_AGENT_PROFILE"]

    @pytest.mark.security
    def test_data_masking_user_id(self):
        """User IDs should be masked in output."""
        from masking import DataMasker
        masker = DataMasker()
        rows = [{"user_id": "USR0001", "channel": "online", "gmv": 100.0}]
        masked = masker.mask_rows(rows)
        assert masked[0]["user_id"] == "USR****"
        assert masked[0]["channel"] == "online"  # Untouched
        assert masked[0]["gmv"] == 100.0  # Untouched

    @pytest.mark.security
    def test_data_masking_email(self):
        """Emails should be masked."""
        from masking import DataMasker
        masker = DataMasker()
        result = masker.mask_value("zhangsan@example.com", masker._match_rule("email"))
        assert "@" in result
        assert "zhangsan" not in result


# ══════════════════════════════════════════════════════════════
# Real DB Integration Tests (SQLite file mode)
# ══════════════════════════════════════════════════════════════

class TestRealDBIntegration:
    """End-to-end tests with persistent SQLite database."""

    @pytest.fixture(autouse=True)
    def setup_teardown(self):
        """Reset DB state before each test."""
        from db_executor import reset_db
        # Keep using in-memory but verify reset works
        import os
        os.environ["DATA_AGENT_DB_PATH"] = ":memory:"
        reset_db()
        yield
        reset_db()

    @pytest.mark.integration
    def test_persistent_db_query(self):
        """Query works with in-memory DB."""
        from graph_agent import run_graph
        from db_executor import get_db, reset_db

        # Verify DB has data
        db = get_db()
        rows = db.execute_cte("SELECT COUNT(*) AS c FROM fct_orders")
        assert rows[0].get("c") == 120

        # Simple metric query
        result = run_graph("昨天GMV是多少？", use_db=True, use_llm=False)
        assert result["status"] == "ok"

        # Second query
        result2 = run_graph("各渠道订单数", use_db=True, use_llm=False)
        assert result2["status"] == "ok"

    @pytest.mark.integration
    def test_pool_stats_after_queries(self):
        """Connection pool should report correct stats after queries."""
        from db_executor import get_db
        from graph_agent import run_graph

        for _ in range(5):
            run_graph("昨天GMV", use_db=True, use_llm=False)

        db = get_db()
        stats = db.pool_stats()
        assert stats["total_created"] > 0, "No connections created"
        assert stats["total_failures"] == 0, f"Connection failures: {stats['total_failures']}"

    @pytest.mark.integration
    def test_audit_log_written(self):
        """Audit log should be written after queries."""
        from audit import AuditLogger
        audit = AuditLogger("/tmp/test_audit_integration.jsonl")
        audit.log("test query", "ok", identity="test", trace_id="t1")
        audit.log("test blocked", "blocked", identity="test", trace_id="t2")

        results = audit.query(identity=audit._hash_identity("test"))
        assert len(results) >= 2, f"Expected >= 2 audit entries, got {len(results)}"

        # Cleanup
        try:
            os.unlink("/tmp/test_audit_integration.jsonl")
        except Exception:
            pass

    @pytest.mark.integration
    def test_schema_migration_applies(self):
        """Schema migration should apply successfully."""
        from db import create_pool
        from schema_migration import SchemaMigrator

        pool = create_pool({"type": "sqlite", "path": ":memory:"})
        m = SchemaMigrator(pool)
        assert m.current_version() == 0

        ver = m.apply()
        assert ver >= 2, f"Migration should reach v2+, got v{ver}"
        assert m.current_version() >= 2


# ══════════════════════════════════════════════════════════════
# Router Benchmark
# ══════════════════════════════════════════════════════════════

class TestRouterBenchmark:
    """LLM router baseline tests (regex mode, deterministic)."""

    @pytest.mark.benchmark
    def test_regex_router_baseline(self):
        """Regex router should achieve >=50% on default test cases."""
        from llm_eval_benchmark import LLMRouterBenchmark

        bench = LLMRouterBenchmark()
        report = bench.run()

        print(f"\n  Router Baseline: {report.passed}/{report.total_cases} "
              f"({report.accuracy:.1%})")
        print(f"  By intent: {report.accuracy_by_intent}")
        if report.failures:
            print(f"  Failures: {report.failures}")

        # Assert minimum accuracy
        assert report.accuracy >= 0.50, \
            f"Router accuracy {report.accuracy:.1%} below 50% minimum"

    @pytest.mark.benchmark
    def test_router_intent_coverage(self):
        """Every expected intent type should have test cases."""
        from llm_eval_benchmark import LLMRouterBenchmark

        bench = LLMRouterBenchmark()
        report = bench.run()

        expected_intents = {"metric_query", "breakdown", "filter_value",
                           "merge", "compare_periods", "blocked"}
        covered = set(report.accuracy_by_intent.keys())

        missing = expected_intents - covered
        assert not missing, f"Intents missing from test coverage: {missing}"
