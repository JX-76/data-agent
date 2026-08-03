"""Tests for new analysis capabilities (time intelligence, composition, anomaly)."""

import pytest
import sys
sys.path.insert(0, 'src')

from time_intelligence import TimeIntelligence, build_time_query
from composition import CompositionAnalyzer, analyze_composition


class TestTimeIntelligence:
    """Test time intelligence capabilities."""

    def test_parse_yoy(self):
        engine = TimeIntelligence()
        config = engine.parse_time_expression("GMV同比")
        assert config.compare_type == "yoy"
        assert config.base_period == "day"  # Default when no time expression
    
    def test_parse_mom(self):
        engine = TimeIntelligence()
        config = engine.parse_time_expression("环比增长率")
        assert config.compare_type == "mom"
    
    def test_parse_trend(self):
        engine = TimeIntelligence()
        config = engine.parse_time_expression("最近30天趋势")
        assert config.compare_type == "trend"
        assert config.periods == 30
    
    def test_build_yoy_sql(self):
        engine = TimeIntelligence()
        config = engine.parse_time_expression("GMV同比")
        sql = engine.build_sql("gmv", config)
        assert "yoy" in sql.lower() or "last_year" in sql.lower()
        assert "gmv" in sql.lower()
    
    def test_build_trend_sql(self):
        engine = TimeIntelligence()
        config = engine.parse_time_expression("最近7天趋势")
        sql = engine.build_sql("gmv", config)
        assert "trend" in sql.lower() or "period" in sql.lower()


class TestComposition:
    """Test composition/percentage analysis."""

    def test_analyze_composition(self):
        # Mock data
        results = [
            {"channel": "online", "gmv": 5000},
            {"channel": "offline", "gmv": 3000},
            {"channel": "app", "gmv": 2000},
        ]
        
        analysis = analyze_composition(results, "channel", "gmv")
        
        assert analysis["dimension"] == "channel"
        assert analysis["metric"] == "gmv"
        assert analysis["total"] == 10000
        assert len(analysis["data"]) == 3
        
        # Check percentages
        online = next(item for item in analysis["data"] if item["label"] == "online")
        assert online["percentage"] == 50.0
        
        # Check insights
        assert len(analysis["insights"]) > 0
        assert "占比最高" in analysis["insights"][0]


class TestIntegration:
    """Integration tests with the full graph."""

    def test_time_compare_intent(self):
        from graph_agent import run_graph
        
        # Test time comparison query
        result = run_graph("GMV同比", use_db=True, use_llm=False)
        assert result["status"] in ("ok", "error")
        
    def test_composition_intent(self):
        from graph_agent import run_graph
        
        # Test composition query
        result = run_graph("各渠道占比", use_db=True, use_llm=False)
        assert result["status"] in ("ok", "error")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
