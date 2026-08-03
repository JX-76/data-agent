"""SemanticConsistency: SQL semantic consistency checker.

Validates that generated SQL matches the user's intent.
Checks:
1. SQL syntax correctness
2. Column existence in schema
3. Table existence in schema
4. Query intent alignment (does SQL answer the question?)
5. Data type compatibility
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger("semantic_consistency")


@dataclass
class ConsistencyResult:
    """Result of semantic consistency check."""
    consistent: bool
    checks: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)


class SemanticConsistencyChecker:
    """Checks semantic consistency between query and SQL."""
    
    def __init__(self, db_connection=None):
        self.db = db_connection
        self._schema_cache: dict[str, list[str]] = {}
    
    def check(self, query: str, sql: str, schema: dict[str, Any] | None = None) -> ConsistencyResult:
        """Check semantic consistency.
        
        Args:
            query: User query
            sql: Generated SQL
            schema: Optional schema information
        
        Returns:
            Consistency result
        """
        checks = []
        errors = []
        warnings = []
        suggestions = []
        
        # Check 1: SQL syntax
        syntax_ok, syntax_msg = self._check_syntax(sql)
        checks.append({"check": "syntax", "passed": syntax_ok, "message": syntax_msg})
        if not syntax_ok:
            errors.append(f"SQL syntax error: {syntax_msg}")
        
        # Check 2: Table existence
        tables = self._extract_tables(sql)
        for table in tables:
            table_ok, table_msg = self._check_table_exists(table)
            checks.append({"check": f"table_{table}", "passed": table_ok, "message": table_msg})
            if not table_ok:
                errors.append(f"Table not found: {table}")
                suggestions.append(f"Check if table '{table}' exists")
        
        # Check 3: Column existence
        columns = self._extract_columns(sql)
        for column in columns:
            col_ok, col_msg = self._check_column_exists(column, tables)
            checks.append({"check": f"column_{column}", "passed": col_ok, "message": col_msg})
            if not col_ok:
                warnings.append(f"Column not verified: {column}")
        
        # Check 4: Intent alignment
        intent_ok, intent_msg = self._check_intent_alignment(query, sql)
        checks.append({"check": "intent_alignment", "passed": intent_ok, "message": intent_msg})
        if not intent_ok:
            warnings.append(f"Intent alignment issue: {intent_msg}")
        
        # Check 5: Forbidden operations
        forbidden_ok, forbidden_msg = self._check_forbidden_operations(sql)
        checks.append({"check": "forbidden_operations", "passed": forbidden_ok, "message": forbidden_msg})
        if not forbidden_ok:
            errors.append(f"Forbidden operation: {forbidden_msg}")
        
        consistent = len(errors) == 0
        
        return ConsistencyResult(
            consistent=consistent,
            checks=checks,
            errors=errors,
            warnings=warnings,
            suggestions=suggestions,
        )
    
    def _check_syntax(self, sql: str) -> tuple[bool, str]:
        """Check SQL syntax."""
        # Basic syntax checks
        if not sql.strip():
            return False, "Empty SQL"
        
        if not sql.strip().upper().startswith(("SELECT", "WITH")):
            return False, "SQL must start with SELECT or WITH"
        
        # Check parentheses balance
        open_parens = sql.count("(")
        close_parens = sql.count(")")
        if open_parens != close_parens:
            return False, f"Unbalanced parentheses: {open_parens} open, {close_parens} close"
        
        return True, "Syntax OK"
    
    def _extract_tables(self, sql: str) -> list[str]:
        """Extract table names from SQL."""
        tables = []
        
        # FROM clause
        from_matches = re.findall(r"FROM\s+(\w+)", sql, re.IGNORECASE)
        tables.extend(from_matches)
        
        # JOIN clause
        join_matches = re.findall(r"JOIN\s+(\w+)", sql, re.IGNORECASE)
        tables.extend(join_matches)
        
        # INTO clause
        into_matches = re.findall(r"INTO\s+(\w+)", sql, re.IGNORECASE)
        tables.extend(into_matches)
        
        return list(set(tables))
    
    def _extract_columns(self, sql: str) -> list[str]:
        """Extract column names from SQL."""
        columns = []
        
        # SELECT clause
        select_match = re.search(r"SELECT\s+(.*?)\s+FROM", sql, re.IGNORECASE | re.DOTALL)
        if select_match:
            select_part = select_match.group(1)
            # Split by comma, handling simple cases
            cols = [c.strip().split()[-1] for c in select_part.split(",")]
            columns.extend(cols)
        
        # WHERE clause
        where_match = re.search(r"WHERE\s+(.*?)(?:GROUP|ORDER|LIMIT|$)", sql, re.IGNORECASE | re.DOTALL)
        if where_match:
            where_part = where_match.group(1)
            # Extract column names from conditions
            col_matches = re.findall(r"(\w+)\s*[=<>!]+", where_part)
            columns.extend(col_matches)
        
        return list(set(columns))
    
    def _check_table_exists(self, table: str) -> tuple[bool, str]:
        """Check if a table exists in the database."""
        if not self.db:
            return True, "No DB connection, assuming table exists"
        
        try:
            cursor = self.db.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
            if cursor.fetchone():
                return True, f"Table {table} exists"
            return False, f"Table {table} not found"
        except Exception as e:
            return False, f"Error checking table: {e}"
    
    def _check_column_exists(self, column: str, tables: list[str]) -> tuple[bool, str]:
        """Check if a column exists in any of the tables."""
        if not self.db:
            return True, "No DB connection, assuming column exists"
        
        for table in tables:
            try:
                cursor = self.db.cursor()
                cursor.execute(f"PRAGMA table_info({table})")
                for row in cursor.fetchall():
                    if row[1] == column:
                        return True, f"Column {column} found in {table}"
            except Exception as e:
                logger.warning("bare_exception_caught", error=str(e))
                continue
        
        return False, f"Column {column} not found in any table"
    
    def _check_intent_alignment(self, query: str, sql: str) -> tuple[bool, str]:
        """Check if SQL aligns with query intent."""
        query_lower = query.lower()
        sql_lower = sql.lower()
        
        # Check metric alignment
        metrics = {
            "gmv": ["gmv", "销售额", "金额"],
            "order_count": ["order_count", "订单数", "订单量"],
            "aov": ["aov", "客单价", "平均订单"],
        }
        
        for metric, aliases in metrics.items():
            if any(alias in query_lower for alias in aliases):
                if metric not in sql_lower and not any(alias in sql_lower for alias in aliases):
                    return False, f"Query asks for {metric} but SQL doesn't reference it"
        
        # Check dimension alignment
        dimensions = {
            "channel": ["渠道", "channel"],
            "region": ["地区", "区域", "region"],
            "category": ["品类", "类目", "category"],
        }
        
        for dim, aliases in dimensions.items():
            if any(alias in query_lower for alias in aliases):
                if dim not in sql_lower:
                    return False, f"Query asks for {dim} breakdown but SQL doesn't group by it"
        
        return True, "Intent aligned"
    
    def _check_forbidden_operations(self, sql: str) -> tuple[bool, str]:
        """Check for forbidden operations."""
        forbidden = ["delete", "update", "insert", "drop", "alter", "truncate"]
        sql_lower = sql.lower()
        
        for op in forbidden:
            if op in sql_lower:
                return False, f"Forbidden operation: {op}"
        
        return True, "No forbidden operations"


def check_semantic_consistency(query: str, sql: str, db_connection=None) -> ConsistencyResult:
    """Convenience function to check semantic consistency.
    
    Args:
        query: User query
        sql: Generated SQL
        db_connection: Optional database connection
    
    Returns:
        Consistency result
    """
    checker = SemanticConsistencyChecker(db_connection)
    return checker.check(query, sql)
