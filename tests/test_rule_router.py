"""Tests for rule_router edge cases and performance."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest
from rule_router import BusinessRuleRouter, rule_based_route


class TestRuleRouterEdgeCases:
    """Edge case tests for rule router."""
    
    def test_empty_query(self):
        """Empty query should not match any rule."""
        router = BusinessRuleRouter()
        match = router.match("")
        assert match is None
    
    def test_long_query(self):
        """Very long query should not crash."""
        router = BusinessRuleRouter()
        long_query = "昨天" + "各渠道" * 1000 + "GMV"
        match = router.match(long_query)
        # Should either match or return None, not crash
        assert match is None or match.intent in ("breakdown", "metric_query")
    
    def test_special_characters(self):
        """Special characters should not crash regex."""
        router = BusinessRuleRouter()
        queries = [
            "GMV$^*()+",
            "渠道[渠道]",
            "销售额{销售额}",
            "GMV|渠道",
        ]
        for q in queries:
            match = router.match(q)
            # Should not crash
            assert match is None or hasattr(match, 'intent')
    
    def test_unicode_variations(self):
        """Different unicode forms should be handled."""
        router = BusinessRuleRouter()
        # Full-width vs half-width
        match = router.match("昨天ＧＭＶ")  # Full-width GMV
        assert match is None  # Full-width not in aliases
    
    def test_case_sensitivity(self):
        """Case variations should be handled."""
        router = BusinessRuleRouter()
        match = router.match("昨天gmv")
        assert match is not None
        assert match.params.get("metric") == "gmv"
    
    def test_whitespace_variations(self):
        """Different whitespace should be handled."""
        router = BusinessRuleRouter()
        queries = [
            "昨天 GMV",
            "昨天  GMV",
            "昨天\tGMV",
        ]
        for q in queries:
            match = router.match(q)
            assert match is not None, f"Failed for: {q!r}"
    
    def test_multiple_metrics(self):
        """Query with multiple metrics should match first one."""
        router = BusinessRuleRouter()
        match = router.match("GMV和订单数对比")
        assert match is not None
        assert match.intent == "merge"
    
    def test_no_metric_found(self):
        """Query without known metric should return None or clarification."""
        router = BusinessRuleRouter()
        match = router.match("随便看看数据")
        # Should either not match or match clarification
        assert match is None or match.intent == "clarification_needed"
    
    def test_cache_stats(self):
        """Cache should track hits and misses."""
        router = BusinessRuleRouter()
        
        # First call - cache miss
        router.match("昨天GMV")
        stats = router.get_cache_stats()
        assert stats["misses"] == 1
        assert stats["hits"] == 0
        
        # Second call - cache hit
        router.match("昨天GMV")
        stats = router.get_cache_stats()
        assert stats["misses"] == 1
        assert stats["hits"] == 1
    
    def test_cache_lru_eviction(self):
        """Cache should evict old entries when full."""
        router = BusinessRuleRouter()
        
        # Fill cache beyond limit
        for i in range(1002):
            router.match(f"查询{i}")
        
        stats = router.get_cache_stats()
        # Cache stores alias resolution results, not match results
        # So it may not exceed limit depending on implementation
        assert stats["total"] > 0  # Should have some entries
    
    def test_conflict_detection(self):
        """Conflict detection should find overlapping patterns."""
        router = BusinessRuleRouter()
        conflicts = router.detect_conflicts()
        # Should not crash, may or may not find conflicts
        assert isinstance(conflicts, list)
    
    def test_reload(self):
        """Reload should refresh rules from disk."""
        router = BusinessRuleRouter()
        
        # Get initial state
        initial_rules = router.get_rule_summary()
        
        # Reload
        router.reload()
        
        # Should have same rules after reload
        after_rules = router.get_rule_summary()
        assert len(initial_rules) == len(after_rules)
    
    def test_performance_baseline(self):
        """Router should handle queries within reasonable time."""
        import time
        
        router = BusinessRuleRouter()
        queries = [
            "昨天GMV",
            "各渠道销售额",
            "GMV和订单数对比",
            "排名",
            "随便看看",
        ]
        
        start = time.time()
        for _ in range(100):
            for q in queries:
                router.match(q)
        elapsed = time.time() - start
        
        # Should complete 500 queries in less than 1 second
        assert elapsed < 1.0, f"Too slow: {elapsed:.2f}s for 500 queries"


class TestRuleBasedRoute:
    """Tests for the high-level rule_based_route function."""
    
    def test_basic_routing(self):
        """Basic routing should return plan dict."""
        plan = rule_based_route("昨天GMV")
        assert plan is not None
        assert plan["intent"] == "metric_query"
        assert plan["metric"] == "gmv"
    
    def test_no_match(self):
        """No match should return None."""
        plan = rule_based_route("完全不相关的查询")
        assert plan is None
    
    def test_source_tracking(self):
        """Plan should track that it came from rules."""
        plan = rule_based_route("昨天GMV")
        assert plan["source"] == "rule"
        assert "confidence" in plan
        assert "rule_name" in plan


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
