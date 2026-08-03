"""Input/output validation and sanitization for security.

Provides:
- SQL injection detection
- XSS prevention
- Input length limits
- Output encoding
"""

from __future__ import annotations

import re
from typing import Optional


class ValidationError(Exception):
    """Raised when validation fails."""
    pass


class InputValidator:
    """Validates and sanitizes user inputs."""
    
    # SQL injection patterns
    SQL_PATTERNS = [
        r"(\b(SELECT|INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|EXEC|UNION)\b)",
        r"(--|#|/\*)",
        r"(\bOR\b|\bAND\b)\s+\d+\s*=\s*\d+",
        r"(\bOR\b|\bAND\b)\s+'[^']*'\s*=\s*'[^']*'",
        r";\s*\w+",
        r"(\bOR\b|\bAND\b)\s*'[^']*'\s*=\s*'[^']*'",
        r"(\bOR\b|\bAND\b)\s*\d+\s*=\s*\d+",
        r"(\bOR\b|\bAND\b)\s*'\s*'\s*=\s*'\s*'",
        r"'(\s*\bOR\b|\s*\bAND\b)\s*'",  # ' OR ' pattern
        r"'(\s*\bOR\b|\s*\bAND\b)\s*\d+",  # ' OR 1 pattern
        r"'(\s*\bOR\b|\s*\bAND\b)\s*'\s*'",  # ' OR '' pattern
    ]
    
    # XSS patterns
    XSS_PATTERNS = [
        r"<script[^>]*>.*?</script>",
        r"javascript:",
        r"on\w+\s*=",
        r"<iframe[^>]*>.*?</iframe>",
    ]
    
    def __init__(self, max_length: int = 2000):
        self.max_length = max_length
        self._sql_patterns = [re.compile(p, re.IGNORECASE) for p in self.SQL_PATTERNS]
        self._xss_patterns = [re.compile(p, re.IGNORECASE) for p in self.XSS_PATTERNS]
    
    def validate_query(self, query: str) -> str:
        """Validate natural language query.
        
        Args:
            query: User's natural language query
            
        Returns:
            Sanitized query string
            
        Raises:
            ValidationError: If query contains malicious content
        """
        if not query:
            raise ValidationError("Query cannot be empty")
        
        if len(query) > self.max_length:
            raise ValidationError(f"Query exceeds maximum length of {self.max_length} characters")
        
        # Check for SQL injection attempts
        for pattern in self._sql_patterns:
            if pattern.search(query):
                raise ValidationError("Query contains potentially malicious content")
        
        # Check for XSS attempts
        for pattern in self._xss_patterns:
            if pattern.search(query):
                raise ValidationError("Query contains potentially malicious content")
        
        # Sanitize: remove control characters
        sanitized = re.sub(r'[\x00-\x08\x0b-\x0c\x0e-\x1f\x7f]', '', query)
        
        return sanitized.strip()
    
    def validate_sql(self, sql: str) -> str:
        """Validate generated SQL for safety.
        
        Args:
            sql: Generated SQL query
            
        Returns:
            Sanitized SQL string
            
        Raises:
            ValidationError: If SQL contains dangerous operations
        """
        if not sql:
            raise ValidationError("SQL cannot be empty")
        
        # Check for dangerous operations
        dangerous = re.compile(
            r"\b(DROP|DELETE|INSERT|UPDATE|CREATE|ALTER|EXEC|TRUNCATE)\b",
            re.IGNORECASE
        )
        if dangerous.search(sql):
            raise ValidationError("SQL contains dangerous operations")
        
        return sql.strip()
    
    def sanitize_output(self, output: str) -> str:
        """Sanitize output to prevent XSS.
        
        Args:
            output: Raw output string
            
        Returns:
            Sanitized output string
        """
        # Escape HTML entities
        output = output.replace("&", "&amp;")
        output = output.replace("<", "&lt;")
        output = output.replace(">", "&gt;")
        output = output.replace('"', "&quot;")
        output = output.replace("'", "&#x27;")
        
        return output


class OutputValidator:
    """Validates and sanitizes outputs."""
    
    def __init__(self, max_results: int = 10000):
        self.max_results = max_results
    
    def validate_results(self, results: list) -> list:
        """Validate query results.
        
        Args:
            results: Query results
            
        Returns:
            Validated results
            
        Raises:
            ValidationError: If results are too large
        """
        if len(results) > self.max_results:
            raise ValidationError(f"Results exceed maximum of {self.max_results} rows")
        
        return results
    
    def validate_sql(self, sql: str) -> str:
        """Validate SQL output.
        
        Args:
            sql: Generated SQL
            
        Returns:
            Validated SQL
        """
        validator = InputValidator()
        return validator.validate_sql(sql)


# Global validator instance
input_validator = InputValidator()
output_validator = OutputValidator()
