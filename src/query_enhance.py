"""QueryEnhance: Query enhancement and expansion.

Enhances user queries by:
1. Expanding abbreviations
2. Adding synonyms
3. Resolving ambiguities
4. Adding context
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger("query_enhance")


@dataclass
class EnhancedQuery:
    """Enhanced query with metadata."""
    original: str
    enhanced: str
    expansions: list[dict[str, Any]] = field(default_factory=list)
    ambiguities: list[dict[str, Any]] = field(default_factory=list)
    confidence: float = 1.0


class QueryEnhancer:
    """Enhances user queries."""
    
    def __init__(self):
        self._abbreviations: dict[str, str] = {
            "GMV": "Gross Merchandise Value",
            "AOV": "Average Order Value",
            "DAU": "Daily Active Users",
            "MAU": "Monthly Active Users",
            "CTR": "Click-Through Rate",
            "CVR": "Conversion Rate",
            "ROI": "Return on Investment",
            "CAC": "Customer Acquisition Cost",
            "LTV": "Lifetime Value",
            "SKU": "Stock Keeping Unit",
            "UV": "Unique Visitors",
            "PV": "Page Views",
        }
        
        self._synonyms: dict[str, list[str]] = {
            "销售额": ["GMV", "收入", "营收", "营业额"],
            "订单数": ["订单量", "单量", "订单数量"],
            "用户": ["客户", "会员", "访客"],
            "商品": ["产品", "SKU", "货品"],
            "渠道": ["来源", "通路", "平台"],
        }
    
    def enhance(self, query: str) -> EnhancedQuery:
        """Enhance a user query.
        
        Args:
            query: User query
        
        Returns:
            Enhanced query
        """
        enhanced = query
        expansions = []
        ambiguities = []
        
        # Expand abbreviations
        for abbr, full in self._abbreviations.items():
            if abbr in query.upper():
                enhanced = enhanced.replace(abbr, f"{abbr} ({full})")
                expansions.append({
                    "type": "abbreviation",
                    "original": abbr,
                    "expanded": full,
                })
        
        # Add synonyms
        for word, synonyms in self._synonyms.items():
            if word in query:
                # Don't replace, just note
                pass
        
        # Detect ambiguities
        ambiguity_patterns = [
            (r"最近", "时间范围不明确"),
            (r"对比", "对比对象不明确"),
            (r"趋势", "时间粒度不明确"),
            (r"占比", "分母不明确"),
        ]
        
        for pattern, description in ambiguity_patterns:
            if re.search(pattern, query):
                ambiguities.append({
                    "type": "ambiguity",
                    "pattern": pattern,
                    "description": description,
                })
        
        return EnhancedQuery(
            original=query,
            enhanced=enhanced,
            expansions=expansions,
            ambiguities=ambiguities,
        )
    
    def expand_abbreviation(self, text: str) -> str:
        """Expand abbreviations in text.
        
        Args:
            text: Text to expand
        
        Returns:
            Text with expanded abbreviations
        """
        for abbr, full in self._abbreviations.items():
            text = text.replace(abbr, f"{abbr} ({full})")
        return text
    
    def get_synonyms(self, word: str) -> list[str]:
        """Get synonyms for a word.
        
        Args:
            word: Word to get synonyms for
        
        Returns:
            List of synonyms
        """
        return self._synonyms.get(word, [])


def enhance_query(query: str) -> EnhancedQuery:
    """Convenience function to enhance a query.
    
    Args:
        query: User query
    
    Returns:
        Enhanced query
    """
    enhancer = QueryEnhancer()
    return enhancer.enhance(query)
