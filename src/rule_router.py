"""Business rule router for deterministic intent classification.

Loads rules from YAML and matches user queries against regex patterns.
Acts as a fallback when LLM routing is unavailable or unreliable.

Usage:
    from rule_router import BusinessRuleRouter
    
    router = BusinessRuleRouter("rules/business_rules.yaml")
    match = router.match("昨天各渠道GMV")
    # -> RuleMatch(intent="breakdown", params={"metric": "gmv", "dimensions": ["channel"]})
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
    import yaml
except ImportError:
    yaml = None

logger = logging.getLogger("rule_router")


@dataclass(frozen=True)
class RuleMatch:
    """Result of a successful rule match."""
    intent: str
    params: dict
    confidence: float
    rule_name: str


class BusinessRuleRouter:
    """Deterministic rule-based intent router.
    
    Loads business rules from YAML and matches queries using regex patterns.
    Supports department-specific aliases and priority-based rule ordering.
    
    Features:
    - Pre-compiled regex patterns for performance
    - LRU cache for alias resolution
    - Hot-reload support
    - Conflict detection
    """
    
    def __init__(self, rules_path: str = None):
        if rules_path is None:
            # Auto-discover rules from project root
            project_root = Path(__file__).resolve().parents[1]
            rules_path = project_root / "rules" / "business_rules.yaml"
        self._path = str(rules_path)
        self._rules: list[dict] = []
        self._metric_aliases: dict[str, dict] = {}
        self._dimension_aliases: dict[str, dict] = {}
        self._alias_cache: dict[str, dict] = {}  # LRU cache for alias resolution
        self._cache_hits = 0
        self._cache_misses = 0
        self._load(self._path)
    
    def _load(self, path: str) -> None:
        """Load rules from YAML file."""
        if yaml is None:
            logger.warning("yaml module not available, rule router disabled")
            return
        
        rules_file = Path(path)
        if not rules_file.exists():
            logger.warning(f"Rules file not found: {path}")
            return
        
        try:
            with open(rules_file, encoding="utf-8") as f:
                config = yaml.safe_load(f)
        except Exception as e:
            logger.error(f"Failed to load rules: {e}")
            return
        
        if not config:
            logger.warning("Empty rules file")
            return
        
        # Load department-specific aliases
        for dept, dept_config in config.get("departments", {}).items():
            self._metric_aliases[dept] = dept_config.get("metric_aliases", {})
            self._dimension_aliases[dept] = dept_config.get("dimension_aliases", {})
            
            # Compile intent rules
            for rule in dept_config.get("intent_rules", []):
                compiled_patterns = []
                for pattern in rule.get("patterns", []):
                    try:
                        # Replace {metric} and {dimension} placeholders with actual aliases
                        metric_aliases = self._metric_aliases.get(dept, {})
                        dimension_aliases = self._dimension_aliases.get(dept, {})
                        
                        # Build regex for metric aliases
                        all_metrics = []
                        for canonical, synonyms in metric_aliases.items():
                            all_metrics.extend(synonyms)
                            all_metrics.append(canonical)
                        metric_pattern = "|".join(re.escape(m) for m in set(all_metrics))
                        
                        # Build regex for dimension aliases
                        all_dimensions = []
                        for canonical, synonyms in dimension_aliases.items():
                            all_dimensions.extend(synonyms)
                            all_dimensions.append(canonical)
                        dimension_pattern = "|".join(re.escape(d) for d in set(all_dimensions))
                        
                        # Replace placeholders in pattern
                        expanded_pattern = pattern.replace("{metric}", f"({metric_pattern})")
                        expanded_pattern = expanded_pattern.replace("{dimension}", f"({dimension_pattern})")
                        
                        compiled_patterns.append(re.compile(expanded_pattern))
                    except re.error as e:
                        logger.warning(f"Invalid regex pattern in rule {rule['name']}: {e}")
                        continue
                
                if compiled_patterns:
                    self._rules.append({
                        "dept": dept,
                        "name": rule["name"],
                        "priority": rule.get("priority", 0),
                        "patterns": compiled_patterns,
                        "params": rule.get("params", {}),
                        "description": rule.get("description", ""),
                    })
        
        # Sort by priority (higher first)
        self._rules.sort(key=lambda r: r["priority"], reverse=True)
        logger.info(f"Loaded {len(self._rules)} rules from {path}")
    
    def reload(self) -> None:
        """Reload rules from disk (useful for hot-updating without restart)."""
        self._rules.clear()
        self._metric_aliases.clear()
        self._dimension_aliases.clear()
        self._alias_cache.clear()
        self._load(self._path)
    
    def resolve_metric(self, term: str, dept: str = "default") -> Optional[str]:
        """Resolve a term to canonical metric name.
        
        Args:
            term: User's term (e.g., "销售额")
            dept: Department context
            
        Returns:
            Canonical metric name (e.g., "gmv") or None if not found
        """
        aliases = self._metric_aliases.get(dept, self._metric_aliases.get("default", {}))
        for canonical, synonyms in aliases.items():
            if term in synonyms or term == canonical:
                return canonical
        return None
    
    def resolve_dimension(self, term: str, dept: str = "default") -> Optional[str]:
        """Resolve a term to canonical dimension name."""
        aliases = self._dimension_aliases.get(dept, self._dimension_aliases.get("default", {}))
        for canonical, synonyms in aliases.items():
            if term in synonyms or term == canonical:
                return canonical
        return None
    
    def _resolve_aliases(self, query: str, dept: str = "default") -> dict:
        """Find all metric/dimension aliases in a query (with LRU cache)."""
        cache_key = f"{dept}:{query}"
        if cache_key in self._alias_cache:
            self._cache_hits += 1
            return self._alias_cache[cache_key]
        
        self._cache_misses += 1
        result = {
            "metrics": [],
            "dimensions": [],
        }
        
        # Check each metric alias
        aliases = self._metric_aliases.get(dept, self._metric_aliases.get("default", {}))
        for canonical, synonyms in aliases.items():
            for synonym in synonyms:
                if synonym in query:
                    result["metrics"].append({
                        "canonical": canonical,
                        "matched": synonym,
                        "position": query.index(synonym),
                    })
                    break
        
        # Check each dimension alias
        dim_aliases = self._dimension_aliases.get(dept, self._dimension_aliases.get("default", {}))
        for canonical, synonyms in dim_aliases.items():
            for synonym in synonyms:
                if synonym in query:
                    result["dimensions"].append({
                        "canonical": canonical,
                        "matched": synonym,
                        "position": query.index(synonym),
                    })
                    break
        
        # Sort by position in query
        result["metrics"].sort(key=lambda x: x["position"])
        result["dimensions"].sort(key=lambda x: x["position"])
        
        # Cache result (simple LRU: limit to 1000 entries)
        if len(self._alias_cache) >= 1000:
            self._alias_cache.pop(next(iter(self._alias_cache)))
        self._alias_cache[cache_key] = result
        
        return result
    
    def match(self, query: str, dept: str = "default") -> Optional[RuleMatch]:
        """Match query against business rules.
        
        Args:
            query: User's natural language query
            dept: Department context for alias resolution
            
        Returns:
            RuleMatch if a rule matches, None otherwise
        """
        if not self._rules:
            return None
        
        # Resolve aliases in query
        aliases = self._resolve_aliases(query, dept)
        
        for rule in self._rules:
            # Skip rules for other departments (except default)
            if rule["dept"] != "default" and rule["dept"] != dept:
                continue
            
            # Try each compiled pattern
            for pattern in rule["patterns"]:
                match = pattern.search(query)
                if match:
                    # Build params from template
                    params = {}
                    for key, template in rule["params"].items():
                        if isinstance(template, str):
                            # Replace template variables
                            value = self._resolve_template(
                                template, query, aliases, match
                            )
                            params[key] = value
                        elif isinstance(template, list):
                            # Recursively resolve template variables in list items
                            resolved_list = []
                            for item in template:
                                if isinstance(item, str):
                                    resolved_list.append(self._resolve_template(item, query, aliases, match))
                                else:
                                    resolved_list.append(item)
                            params[key] = resolved_list
                        else:
                            params[key] = template
                    
                    return RuleMatch(
                        intent=params.get("intent", "unknown"),
                        params=params,
                        confidence=0.95 if rule["priority"] >= 80 else 0.8,
                        rule_name=rule["name"],
                    )
        
        return None
    
    def _resolve_template(
        self, 
        template: str, 
        query: str, 
        aliases: dict, 
        match: re.Match
    ) -> str:
        """Resolve a template string with variables."""
        result = template
        
        # Replace {matched_metric} with first matched metric
        if "{matched_metric}" in result and aliases["metrics"]:
            result = result.replace("{matched_metric}", aliases["metrics"][0]["canonical"])
        
        # Replace {matched_metric_1} and {matched_metric_2}
        if "{matched_metric_1}" in result and len(aliases["metrics"]) >= 1:
            result = result.replace("{matched_metric_1}", aliases["metrics"][0]["canonical"])
        if "{matched_metric_2}" in result and len(aliases["metrics"]) >= 2:
            result = result.replace("{matched_metric_2}", aliases["metrics"][1]["canonical"])
        
        # Replace {matched_dimension} with first matched dimension
        if "{matched_dimension}" in result and aliases["dimensions"]:
            result = result.replace("{matched_dimension}", aliases["dimensions"][0]["canonical"])
        
        # Replace {matched_value} with regex group if available
        if "{matched_value}" in result:
            try:
                value = match.group(1) if match.groups() else ""
                result = result.replace("{matched_value}", value)
            except IndexError:
                result = result.replace("{matched_value}", "")
        
        return result
    
    def get_rule_summary(self) -> list[dict]:
        """Get summary of loaded rules for debugging."""
        return [
            {
                "name": r["name"],
                "dept": r["dept"],
                "priority": r["priority"],
                "description": r["description"],
                "pattern_count": len(r["patterns"]),
            }
            for r in self._rules
        ]
    
    def get_cache_stats(self) -> dict:
        """Get cache hit/miss statistics."""
        total = self._cache_hits + self._cache_misses
        return {
            "hits": self._cache_hits,
            "misses": self._cache_misses,
            "total": total,
            "hit_rate": self._cache_hits / total if total > 0 else 0,
        }
    
    def detect_conflicts(self) -> list[dict]:
        """Detect potential rule conflicts (same pattern matches same query).
        
        Returns list of conflicting rule pairs.
        """
        conflicts = []
        for i, rule1 in enumerate(self._rules):
            for rule2 in self._rules[i+1:]:
                # Check if rules have overlapping patterns
                for p1 in rule1["patterns"]:
                    for p2 in rule2["patterns"]:
                        # Simple heuristic: if patterns are identical or one contains the other
                        p1_str = p1.pattern
                        p2_str = p2.pattern
                        if p1_str == p2_str or p1_str in p2_str or p2_str in p1_str:
                            conflicts.append({
                                "rule1": rule1["name"],
                                "rule2": rule2["name"],
                                "pattern1": p1_str,
                                "pattern2": p2_str,
                                "priority1": rule1["priority"],
                                "priority2": rule2["priority"],
                            })
        return conflicts


# ── Convenience function for graph_agent integration ──

def rule_based_route(query: str, dept: str = "default") -> Optional[dict]:
    """High-level function to route a query using business rules.
    
    Returns a plan dict compatible with graph_agent's route_and_plan output,
    or None if no rule matches.
    
    Usage:
        plan = rule_based_route("昨天各渠道GMV")
        if plan:
            return plan
        # Fall back to LLM or clarification
    """
    router = BusinessRuleRouter()
    match = router.match(query, dept=dept)
    
    if not match:
        return None
    
    # Build standard plan dict
    plan = {
        "intent": match.intent,
        "model": "order_detail",
        "metric": "gmv",
        "dimensions": [],
        "source": "rule",
        "confidence": match.confidence,
        "rule_name": match.rule_name,
    }
    
    # Override with matched params
    params = match.params
    plan["metric"] = params.get("metric", "gmv")
    plan["dimensions"] = params.get("dimensions", [])
    
    if "filter_dim" in params:
        plan["filter_dim"] = params["filter_dim"]
    if "filter_val" in params:
        plan["filter_val"] = params["filter_val"]
    if "metrics" in params:
        plan["metrics"] = params["metrics"]
    if "merge_on" in params:
        plan["merge_on"] = params["merge_on"]
    if "order" in params:
        plan["order"] = params["order"]
    
    return plan
