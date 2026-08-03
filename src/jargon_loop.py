"""Jargon Feedback Loop: learn and correct unrecognized terms.

When a query contains terms not in the alias registry:
1. Log the unrecognized term
2. Try to match with fuzzy search
3. If still unmatched, flag for human review
4. After review, add to alias registry
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import structlog

logger = structlog.get_logger("jargon_loop")


# ── State File ──

_JARGON_STATE_PATH = Path("/tmp/data_agent_jargon.json")


@dataclass
class UnrecognizedTerm:
    term: str
    query: str
    timestamp: str
    suggested_match: str | None = None
    resolved: bool = False
    resolution: str | None = None
    reviewer: str | None = None


# ── Jargon Registry ──

class JargonRegistry:
    """Registry for unrecognized terms and their resolutions."""
    
    def __init__(self, state_path: Path = _JARGON_STATE_PATH):
        self.state_path = state_path
        self.terms: dict[str, UnrecognizedTerm] = {}
        self.alias_additions: dict[str, str] = {}  # term → canonical
        self._load()
    
    def _load(self) -> None:
        """Load state from disk."""
        if self.state_path.exists():
            try:
                with open(self.state_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for term_data in data.get("terms", []):
                    term = UnrecognizedTerm(**term_data)
                    self.terms[term.term] = term
                self.alias_additions = data.get("alias_additions", {})
            except Exception as e:
                logger.warning("jargon_load_failed", error=str(e))
    
    def _save(self) -> None:
        """Save state to disk."""
        data = {
            "terms": [
                {
                    "term": t.term,
                    "query": t.query,
                    "timestamp": t.timestamp,
                    "suggested_match": t.suggested_match,
                    "resolved": t.resolved,
                    "resolution": t.resolution,
                    "reviewer": t.reviewer,
                }
                for t in self.terms.values()
            ],
            "alias_additions": self.alias_additions,
        }
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.state_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    
    def record_unrecognized(self, term: str, query: str, suggested_match: str | None = None) -> None:
        """Record an unrecognized term."""
        if term in self.terms and not self.terms[term].resolved:
            return  # Already recorded and unresolved
        
        self.terms[term] = UnrecognizedTerm(
            term=term,
            query=query,
            timestamp=datetime.now().isoformat(),
            suggested_match=suggested_match,
        )
        self._save()
        logger.info("unrecognized_term_recorded", term=term, query=query)
    
    def resolve_term(self, term: str, resolution: str, reviewer: str = "system") -> None:
        """Resolve an unrecognized term by adding it to aliases."""
        if term not in self.terms:
            return
        
        self.terms[term].resolved = True
        self.terms[term].resolution = resolution
        self.terms[term].reviewer = reviewer
        self.alias_additions[term] = resolution
        self._save()
        logger.info("term_resolved", term=term, resolution=resolution, reviewer=reviewer)
    
    def get_pending_terms(self) -> list[UnrecognizedTerm]:
        """Get all unresolved terms."""
        return [t for t in self.terms.values() if not t.resolved]
    
    def get_alias_additions(self) -> dict[str, str]:
        """Get all resolved alias additions."""
        return dict(self.alias_additions)
    
    def clear_resolved(self) -> None:
        """Clear resolved terms from the registry."""
        self.terms = {k: v for k, v in self.terms.items() if not v.resolved}
        self.alias_additions = {}
        self._save()


# ── Global Registry ──

_registry: JargonRegistry | None = None


def get_registry() -> JargonRegistry:
    """Get the global jargon registry."""
    global _registry
    if _registry is None:
        _registry = JargonRegistry()
    return _registry


# ── Query Analysis ──

def extract_unknown_terms(query: str, known_metrics: set[str], known_dimensions: set[str]) -> list[str]:
    """Extract terms from query that are not in known metrics/dimensions.
    
    This is a simple heuristic. In production, use NLP or embedding similarity.
    """
    # Common stop words
    stop_words = {"的", "是", "什么", "多少", "怎么", "如何", "昨天", "今天", "最近", "本月", "上周", "按", "各", "查", "看", "分析"}
    
    # Split query into potential terms
    import re
    candidates = re.findall(r'[\u4e00-\u9fff]{2,}|[a-zA-Z_]{2,}', query)
    
    unknown = []
    for term in candidates:
        if term in stop_words:
            continue
        if term in known_metrics or term in known_dimensions:
            continue
        # Check if it's a time-related term
        if any(t in term for t in ["天", "周", "月", "年", "日"]):
            continue
        unknown.append(term)
    
    return unknown


def check_query_jargon(query: str, dept_id: str = "default") -> dict:
    """Check a query for unrecognized jargon and return feedback.
    
    Returns:
        {
            "has_unknown": bool,
            "unknown_terms": list[str],
            "suggestions": dict[str, str],
            "should_clarify": bool,
        }
    """
    from dept_view import get_department_view
    
    view = get_department_view(dept_id)
    known_metrics = set()
    known_dimensions = set()
    
    if view:
        known_metrics.update(view.allowed_metrics)
        known_metrics.update(view.metric_aliases.keys())
        known_dimensions.update(view.dimension_aliases.keys())
    
    # Add common metrics and dimensions
    known_metrics.update(["gmv", "order_count", "aov", "conversion_rate", "roi", "cac", "retention_rate", "churn_rate"])
    known_dimensions.update(["channel", "region", "category", "date", "campaign", "sku"])
    
    unknown = extract_unknown_terms(query, known_metrics, known_dimensions)
    
    registry = get_registry()
    suggestions = {}
    
    for term in unknown:
        # Check if we have a resolved alias for this term
        if term in registry.alias_additions:
            suggestions[term] = registry.alias_additions[term]
        else:
            # Record for future review
            registry.record_unrecognized(term, query)
    
    return {
        "has_unknown": len(unknown) > 0 and len(suggestions) == 0,
        "unknown_terms": unknown,
        "suggestions": suggestions,
        "should_clarify": len(unknown) > 0 and len(suggestions) == 0,
    }


# ── Integration with Route ──

def enrich_query_with_jargon_feedback(query: str, dept_id: str = "default") -> tuple[str, dict]:
    """Process a query through the jargon feedback loop.
    
    Returns:
        (enriched_query, feedback_dict)
    """
    feedback = check_query_jargon(query, dept_id)
    
    if feedback["should_clarify"]:
        return query, {
            "status": "need_clarification",
            "reason": "unrecognized_terms",
            "unknown_terms": feedback["unknown_terms"],
            "message": f"查询中包含不熟悉的术语: {', '.join(feedback['unknown_terms'])}。请确认这些术语的含义，或尝试使用标准指标名称。",
        }
    
    # Apply suggestions
    enriched = query
    for term, canonical in feedback["suggestions"].items():
        enriched = enriched.replace(term, canonical)
    
    return enriched, {"status": "ok", "replacements": feedback["suggestions"]}
