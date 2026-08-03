"""Tests for anomaly detection and root cause analysis."""

import pytest
import sys
sys.path.insert(0, 'src')

from anomaly_detection import AnomalyDetector, DimensionDrillDown, detect_anomaly
from root_cause import RootCauseAnalyzer, find_root_cause


class TestAnomalyDetection:
    """Test anomaly detection capabilities."""

    def test_no_anomaly(self):
        """Test with stable data - no anomaly."""
        detector = AnomalyDetector()
        history = [100, 101, 99, 102, 100, 101, 99, 100, 101, 100]
        result = detector.detect("gmv", 100, history)
        
        assert not result.is_anomaly
        assert result.severity == "none"
        assert "未检测到异常" in result.reason

    def test_statistical_anomaly(self):
        """Test with statistical outlier."""
        detector = AnomalyDetector()
        history = [100, 101, 99, 102, 100, 101, 99, 100, 101, 100]
        result = detector.detect("gmv", 200, history)  # 2x normal
        
        assert result.is_anomaly
        assert result.severity == "high"
        assert "统计异常" in result.reason

    def test_business_rule_anomaly(self):
        """Test with business rule violation (>30% change)."""
        detector = AnomalyDetector()
        history = [100, 100, 100, 100, 100]
        result = detector.detect("gmv", 50, history)  # -50%
        
        assert result.is_anomaly
        assert result.severity in ("medium", "high")
        assert "业务" in result.reason

    def test_insufficient_data(self):
        """Test with insufficient history."""
        detector = AnomalyDetector()
        result = detector.detect("gmv", 100, [100])
        
        assert not result.is_anomaly
        assert "历史数据不足" in result.reason

    def test_z_score_calculation(self):
        """Test Z-score calculation."""
        detector = AnomalyDetector()
        history = [100, 100, 100, 100, 100]
        result = detector.detect("gmv", 200, history)
        
        # When std is 0, z_score is 0 (but business rule still triggers)
        assert result.z_score >= 0

    def test_suggested_dimensions(self):
        """Test dimension suggestions."""
        detector = AnomalyDetector()
        history = [100, 101, 99, 102, 100]
        result = detector.detect("gmv", 100, history)
        
        assert len(result.suggested_dimensions) > 0
        assert "channel" in result.suggested_dimensions


class TestRootCauseAnalysis:
    """Test root cause analysis capabilities."""

    def test_dimension_contribution(self):
        """Test dimension contribution analysis."""
        analyzer = RootCauseAnalyzer()
        
        # Current period data
        current = [
            {"channel": "online", "gmv": 5000},
            {"channel": "offline", "gmv": 3000},
            {"channel": "app", "gmv": 2000}
        ]
        
        # Previous period data (online decreased)
        previous = [
            {"channel": "online", "gmv": 8000},
            {"channel": "offline", "gmv": 3000},
            {"channel": "app", "gmv": 2000}
        ]
        
        result = analyzer.analyze("gmv", current, previous, "channel")
        
        assert result.metric == "gmv"
        assert result.primary_cause is not None
        assert result.primary_cause.dimension == "channel"
        assert len(result.findings) > 0
        assert len(result.recommendations) > 0

    def test_multi_dimension_analysis(self):
        """Test multi-dimension root cause."""
        analyzer = RootCauseAnalyzer()
        
        current_data = {
            "channel": [
                {"channel": "online", "gmv": 5000},
                {"channel": "offline", "gmv": 3000}
            ],
            "region": [
                {"region": "south", "gmv": 4000},
                {"region": "north", "gmv": 4000}
            ]
        }
        
        previous_data = {
            "channel": [
                {"channel": "online", "gmv": 8000},
                {"channel": "offline", "gmv": 3000}
            ],
            "region": [
                {"region": "south", "gmv": 6000},
                {"region": "north", "gmv": 5000}
            ]
        }
        
        results = analyzer.multi_dimension_analysis("gmv", current_data, previous_data)
        
        assert "channel" in results
        assert "region" in results
        assert results["channel"].primary_cause is not None

    def test_insight_generation(self):
        """Test insight generation."""
        analyzer = RootCauseAnalyzer()
        
        current = [
            {"channel": "online", "gmv": 5000},
            {"channel": "offline", "gmv": 3000}
        ]
        
        previous = [
            {"channel": "online", "gmv": 8000},
            {"channel": "offline", "gmv": 3000}
        ]
        
        result = analyzer.analyze("gmv", current, previous, "channel")
        
        assert len(result.findings) > 0
        assert "gmv" in result.findings[0]
        assert len(result.recommendations) > 0


class TestIntegration:
    """Integration tests."""

    def test_end_to_end_anomaly(self):
        """Test end-to-end anomaly detection."""
        result = detect_anomaly("gmv", 200, [100, 100, 100, 100, 100])
        
        assert result.is_anomaly
        assert result.severity == "high"

    def test_end_to_end_root_cause(self):
        """Test end-to-end root cause analysis."""
        current = [
            {"channel": "online", "gmv": 5000},
            {"channel": "offline", "gmv": 3000}
        ]
        
        previous = [
            {"channel": "online", "gmv": 8000},
            {"channel": "offline", "gmv": 3000}
        ]
        
        result = find_root_cause("gmv", current, previous, "channel")
        
        assert result.metric == "gmv"
        assert result.primary_cause is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
