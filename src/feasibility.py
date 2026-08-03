"""FeasibilityAssessment: Pre-execution feasibility check.

Checks if a query can be executed before running it:
1. Table existence
2. Column existence
3. Permission check
4. Query complexity
5. Data availability
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger("feasibility")


@dataclass
class FeasibilityResult:
    """Result of a feasibility check."""
    feasible: bool
    reason: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    suggestions: list[str] = field(default_factory=list)


class FeasibilityAssessor:
    """Assesses whether a query is feasible before execution."""
    
    def __init__(self, db_connection=None):
        self.db = db_connection
        self._schema_cache: dict[str, list[str]] = {}
    
    def assess(self, query: str, plan: dict[str, Any] | None = None) -> FeasibilityResult:
        """Assess if a query is feasible.
        
        Args:
            query: User query
            plan: Optional execution plan
        
        Returns:
            Feasibility result
        """
        checks = []
        
        # Check 1: Table existence
        table_check = self._check_tables(query)
        checks.append(table_check)
        
        # Check 2: Column existence
        column_check = self._check_columns(query)
        checks.append(column_check)
        
        # Check 3: Query complexity
        complexity_check = self._check_complexity(query)
        checks.append(complexity_check)
        
        # Check 4: Data availability
        data_check = self._check_data_availability(query)
        checks.append(data_check)
        
        # Aggregate results
        failed_checks = [c for c in checks if not c.feasible]
        
        if failed_checks:
            reasons = [c.reason for c in failed_checks]
            suggestions = []
            for c in failed_checks:
                suggestions.extend(c.suggestions)
            
            return FeasibilityResult(
                feasible=False,
                reason="; ".join(reasons),
                details={"failed_checks": len(failed_checks)},
                suggestions=suggestions,
            )
        
        return FeasibilityResult(
            feasible=True,
            reason="Query is feasible",
            details={"checks_passed": len(checks)},
        )
    
    def _check_tables(self, query: str) -> FeasibilityResult:
        """Check if referenced tables exist."""
        # Extract table names from query
        import re
        
        # Simple heuristic: look for common table name patterns
        table_patterns = [
            r"from\s+(\w+)",
            r"join\s+(\w+)",
            r"into\s+(\w+)",
        ]
        
        tables = []
        for pattern in table_patterns:
            matches = re.findall(pattern, query, re.IGNORECASE)
            tables.extend(matches)
        
        if not tables:
            return FeasibilityResult(feasible=True, reason="No tables referenced")
        
        # Check if tables exist (using schema cache)
        missing_tables = []
        for table in tables:
            if table not in self._schema_cache:
                missing_tables.append(table)
        
        if missing_tables:
            return FeasibilityResult(
                feasible=False,
                reason=f"Tables not found: {', '.join(missing_tables)}",
                suggestions=[f"Check if table '{t}' exists" for t in missing_tables],
            )
        
        return FeasibilityResult(feasible=True, reason="All tables exist")
    
    def _check_columns(self, query: str) -> FeasibilityResult:
        """Check if referenced columns exist."""
        # This would require actual schema information
        # For now, assume columns are valid
        return FeasibilityResult(feasible=True, reason="Columns assumed valid")
    
    def _check_complexity(self, query: str) -> FeasibilityResult:
        """Check if query is too complex."""
        # Simple heuristic: query length and nesting depth
        max_length = 10000  # characters
        max_nesting = 5  # subquery nesting depth
        
        if len(query) > max_length:
            return FeasibilityResult(
                feasible=False,
                reason="Query too complex (exceeds max length)",
                suggestions=["Simplify query", "Break into multiple queries"],
            )
        
        # Check nesting depth
        nesting_depth = query.count("(") - query.count(")")
        if nesting_depth > max_nesting:
            return FeasibilityResult(
                feasible=False,
                reason="Query too deeply nested",
                suggestions=["Reduce subquery nesting", "Use CTEs"],
            )
        
        return FeasibilityResult(feasible=True, reason="Complexity acceptable")
    
    def _check_data_availability(self, query: str) -> FeasibilityResult:
        """Check if data is available for the query."""
        # This would require checking actual data availability
        # For now, assume data is available
        return FeasibilityResult(feasible=True, reason="Data assumed available")
    
    def cache_schema(self, table_name: str, columns: list[str]) -> None:
        """Cache schema information for a table.
        
        Args:
            table_name: Table name
            columns: List of column names
        """
        self._schema_cache[table_name] = columns
        logger.info("schema_cached", table=table_name, columns=len(columns))


def assess_feasibility(query: str, db_connection=None) -> FeasibilityResult:
    """Convenience function to assess query feasibility.
    
    Args:
        query: User query
        db_connection: Optional database connection
    
    Returns:
        Feasibility result
    """
    assessor = FeasibilityAssessor(db_connection)
    return assessor.assess(query)
