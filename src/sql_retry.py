"""SQL Retry: Automatic SQL retry with error recovery.

Handles SQL execution errors by:
1. Catching execution errors
2. Analyzing error type (syntax, missing table, missing column, etc.)
3. Generating corrected SQL
4. Retrying up to SQL_RETRY_COUNT times
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger("sql_retry")


# ── Constants ──

SQL_RETRY_COUNT = 3  # Max SQL retries


@dataclass
class RetryResult:
    """Result of SQL retry."""
    success: bool
    sql: str
    error: str | None = None
    attempts: int = 0
    corrections: list[dict[str, Any]] = field(default_factory=list)


class SQLRetryHandler:
    """Handles SQL execution errors with automatic retry."""
    
    def __init__(self, db_connection=None):
        self.db = db_connection
        self._error_patterns = {
            "syntax_error": [
                r"syntax error",
                r"near .* syntax error",
                r"unrecognized token",
            ],
            "missing_table": [
                r"no such table",
                r"table .* not found",
                r"relation .* does not exist",
            ],
            "missing_column": [
                r"no such column",
                r"column .* not found",
                r"field .* not found",
            ],
            "type_mismatch": [
                r"datatype mismatch",
                r"type mismatch",
                r"incompatible types",
            ],
            "permission_denied": [
                r"permission denied",
                r"access denied",
                r"not authorized",
            ],
        }
    
    def execute_with_retry(self, sql: str, query: str | None = None) -> RetryResult:
        """Execute SQL with automatic retry.
        
        Args:
            sql: SQL to execute
            query: Original user query (for context)
        
        Returns:
            Retry result
        """
        attempts = 0
        current_sql = sql
        corrections = []
        
        while attempts < SQL_RETRY_COUNT:
            attempts += 1
            
            try:
                # Try to execute
                result = self._execute_sql(current_sql)
                
                # Success
                return RetryResult(
                    success=True,
                    sql=current_sql,
                    attempts=attempts,
                    corrections=corrections,
                )
            
            except Exception as e:
                error_msg = str(e)
                error_type = self._classify_error(error_msg)
                
                logger.warning("sql_error", 
                    attempt=attempts, 
                    error_type=error_type,
                    error=error_msg[:200],
                )
                
                if attempts >= SQL_RETRY_COUNT:
                    # Max retries reached
                    return RetryResult(
                        success=False,
                        sql=current_sql,
                        error=error_msg,
                        attempts=attempts,
                        corrections=corrections,
                    )
                
                # Try to correct
                correction = self._correct_sql(current_sql, error_type, error_msg, query)
                
                if correction:
                    corrections.append({
                        "attempt": attempts,
                        "error_type": error_type,
                        "original": current_sql,
                        "corrected": correction,
                    })
                    current_sql = correction
                else:
                    # Can't correct, return error
                    return RetryResult(
                        success=False,
                        sql=current_sql,
                        error=error_msg,
                        attempts=attempts,
                        corrections=corrections,
                    )
        
        return RetryResult(
            success=False,
            sql=current_sql,
            error="Max retries reached",
            attempts=attempts,
            corrections=corrections,
        )
    
    def _execute_sql(self, sql: str) -> Any:
        """Execute SQL and return result.
        
        Args:
            sql: SQL to execute
        
        Returns:
            Execution result
        """
        if not self.db:
            raise RuntimeError("No database connection")
        
        cursor = self.db.cursor()
        cursor.execute(sql)
        return cursor.fetchall()
    
    def _classify_error(self, error_msg: str) -> str:
        """Classify error type from error message.
        
        Args:
            error_msg: Error message
        
        Returns:
            Error type
        """
        error_lower = error_msg.lower()
        
        for error_type, patterns in self._error_patterns.items():
            for pattern in patterns:
                if re.search(pattern, error_lower):
                    return error_type
        
        return "unknown"
    
    def _correct_sql(self, sql: str, error_type: str, error_msg: str, query: str | None = None) -> str | None:
        """Correct SQL based on error type.
        
        Args:
            sql: Original SQL
            error_type: Error type
            error_msg: Error message
            query: Original user query
        
        Returns:
            Corrected SQL or None if can't correct
        """
        if error_type == "syntax_error":
            return self._fix_syntax(sql, error_msg)
        elif error_type == "missing_table":
            return self._fix_table(sql, error_msg)
        elif error_type == "missing_column":
            return self._fix_column(sql, error_msg)
        elif error_type == "type_mismatch":
            return self._fix_type(sql, error_msg)
        else:
            return None
    
    def _fix_syntax(self, sql: str, error_msg: str) -> str | None:
        """Fix syntax errors.
        
        Common fixes:
        - Missing commas
        - Unbalanced parentheses
        - Missing quotes
        """
        # Fix unbalanced parentheses
        open_parens = sql.count("(")
        close_parens = sql.count(")")
        
        if open_parens > close_parens:
            sql = sql + ")" * (open_parens - close_parens)
        elif close_parens > open_parens:
            sql = sql[:-(close_parens - open_parens)]
        
        # Fix missing semicolons (SQLite doesn't require them, but some dialects do)
        if not sql.rstrip().endswith(";"):
            sql = sql.rstrip() + ";"
        
        return sql
    
    def _fix_table(self, sql: str, error_msg: str) -> str | None:
        """Fix missing table errors.
        
        Extract table name from error and try to find alternative.
        """
        # Extract table name from error
        match = re.search(r"no such table: (\w+)", error_msg, re.IGNORECASE)
        if match:
            missing_table = match.group(1)
            
            # Try to find similar table
            similar = self._find_similar_table(missing_table)
            if similar:
                sql = sql.replace(missing_table, similar)
                return sql
        
        return None
    
    def _fix_column(self, sql: str, error_msg: str) -> str | None:
        """Fix missing column errors.
        
        Extract column name from error and try to find alternative.
        """
        # Extract column name from error
        match = re.search(r"no such column: (\w+)", error_msg, re.IGNORECASE)
        if match:
            missing_column = match.group(1)
            
            # Try to find similar column
            similar = self._find_similar_column(missing_column, sql)
            if similar:
                sql = sql.replace(missing_column, similar)
                return sql
        
        return None
    
    def _fix_type(self, sql: str, error_msg: str) -> str | None:
        """Fix type mismatch errors.
        
        Try to cast values to correct type.
        """
        # Simple fix: wrap comparisons in CAST
        # This is a heuristic and may not always work
        sql = re.sub(
            r"(\w+)\s*=\s*['\"](\d+)['\"]",
            r"\1 = CAST('\2' AS INTEGER)",
            sql,
        )
        
        return sql
    
    def _find_similar_table(self, table_name: str) -> str | None:
        """Find similar table name.
        
        Args:
            table_name: Missing table name
        
        Returns:
            Similar table name or None
        """
        if not self.db:
            return None
        
        try:
            cursor = self.db.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [row[0] for row in cursor.fetchall()]
            
            # Simple string similarity
            for table in tables:
                if table_name.lower() in table.lower() or table.lower() in table_name.lower():
                    return table
            
            return None
        except Exception as e:
            logger.warning("bare_exception_caught", error=str(e))
            return None
    
    def _find_similar_column(self, column_name: str, sql: str) -> str | None:
        """Find similar column name.
        
        Args:
            column_name: Missing column name
            sql: SQL query
        
        Returns:
            Similar column name or None
        """
        if not self.db:
            return None
        
        # Extract tables from SQL
        tables = re.findall(r"FROM\s+(\w+)", sql, re.IGNORECASE)
        tables.extend(re.findall(r"JOIN\s+(\w+)", sql, re.IGNORECASE))
        
        for table in tables:
            try:
                cursor = self.db.cursor()
                cursor.execute(f"PRAGMA table_info({table})")
                columns = [row[1] for row in cursor.fetchall()]
                
                for col in columns:
                    if column_name.lower() in col.lower() or col.lower() in column_name.lower():
                        return col
            except Exception as e:
                logger.warning("bare_exception_caught", error=str(e))
                continue
        
        return None


def execute_sql_with_retry(sql: str, db_connection=None, query: str | None = None) -> RetryResult:
    """Convenience function to execute SQL with retry.
    
    Args:
        sql: SQL to execute
        db_connection: Database connection
        query: Original user query
    
    Returns:
        Retry result
    """
    handler = SQLRetryHandler(db_connection)
    return handler.execute_with_retry(sql, query)
