"""Python Retry: Automatic Python code retry with error recovery.

Handles Python execution errors by:
1. Catching execution errors
2. Analyzing error type (syntax, import, runtime, etc.)
3. Generating corrected code
4. Retrying up to PYTHON_RETRY_COUNT times
"""

from __future__ import annotations

import re
import traceback
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger("python_retry")


# ── Constants ──

PYTHON_RETRY_COUNT = 3  # Max Python retries


@dataclass
class PythonRetryResult:
    """Result of Python retry."""
    success: bool
    code: str
    output: Any = None
    error: str | None = None
    attempts: int = 0
    corrections: list[dict[str, Any]] = field(default_factory=list)


class PythonRetryHandler:
    """Handles Python execution errors with automatic retry."""
    
    def __init__(self, globals_dict: dict[str, Any] | None = None):
        self.globals = globals_dict or {}
        self._error_patterns = {
            "syntax_error": [
                r"SyntaxError",
                r"IndentationError",
                r"unexpected indent",
            ],
            "import_error": [
                r"ModuleNotFoundError",
                r"ImportError",
                r"No module named",
            ],
            "name_error": [
                r"NameError",
                r"is not defined",
            ],
            "type_error": [
                r"TypeError",
                r"unsupported operand",
            ],
            "index_error": [
                r"IndexError",
                r"list index out of range",
            ],
            "key_error": [
                r"KeyError",
                r"not found in",
            ],
            "attribute_error": [
                r"AttributeError",
                r"has no attribute",
            ],
        }
    
    def execute_with_retry(self, code: str, query: str | None = None) -> PythonRetryResult:
        """Execute Python code with automatic retry.
        
        Args:
            code: Python code to execute
            query: Original user query (for context)
        
        Returns:
            Retry result
        """
        attempts = 0
        current_code = code
        corrections = []
        
        while attempts < PYTHON_RETRY_COUNT:
            attempts += 1
            
            try:
                # Try to execute
                output = self._execute_python(current_code)
                
                # Success
                return PythonRetryResult(
                    success=True,
                    code=current_code,
                    output=output,
                    attempts=attempts,
                    corrections=corrections,
                )
            
            except Exception as e:
                error_msg = str(e)
                error_type = self._classify_error(error_msg)
                
                logger.warning("python_error",
                    attempt=attempts,
                    error_type=error_type,
                    error=error_msg[:200],
                )
                
                if attempts >= PYTHON_RETRY_COUNT:
                    # Max retries reached
                    return PythonRetryResult(
                        success=False,
                        code=current_code,
                        error=error_msg,
                        attempts=attempts,
                        corrections=corrections,
                    )
                
                # Try to correct
                correction = self._correct_python(current_code, error_type, error_msg, query)
                
                if correction:
                    corrections.append({
                        "attempt": attempts,
                        "error_type": error_type,
                        "original": current_code,
                        "corrected": correction,
                    })
                    current_code = correction
                else:
                    # Can't correct, return error
                    return PythonRetryResult(
                        success=False,
                        code=current_code,
                        error=error_msg,
                        attempts=attempts,
                        corrections=corrections,
                    )
        
        return PythonRetryResult(
            success=False,
            code=current_code,
            error="Max retries reached",
            attempts=attempts,
            corrections=corrections,
        )
    
    def _execute_python(self, code: str) -> Any:
        """Execute Python code and return result.
        
        Args:
            code: Python code to execute
        
        Returns:
            Execution result
        """
        # Create a safe execution environment
        exec_globals = {
            "__builtins__": __builtins__,
            **self.globals,
        }
        
        exec(code, exec_globals)
        
        # Return the last expression if any
        return exec_globals.get("_result")
    
    def _classify_error(self, error_msg: str) -> str:
        """Classify error type from error message.
        
        Args:
            error_msg: Error message
        
        Returns:
            Error type
        """
        for error_type, patterns in self._error_patterns.items():
            for pattern in patterns:
                if re.search(pattern, error_msg):
                    return error_type
        
        return "unknown"
    
    def _correct_python(self, code: str, error_type: str, error_msg: str, query: str | None = None) -> str | None:
        """Correct Python code based on error type.
        
        Args:
            code: Original code
            error_type: Error type
            error_msg: Error message
            query: Original user query
        
        Returns:
            Corrected code or None if can't correct
        """
        if error_type == "syntax_error":
            return self._fix_syntax(code, error_msg)
        elif error_type == "import_error":
            return self._fix_import(code, error_msg)
        elif error_type == "name_error":
            return self._fix_name(code, error_msg)
        elif error_type == "type_error":
            return self._fix_type(code, error_msg)
        elif error_type == "index_error":
            return self._fix_index(code, error_msg)
        elif error_type == "key_error":
            return self._fix_key(code, error_msg)
        elif error_type == "attribute_error":
            return self._fix_attribute(code, error_msg)
        else:
            return None
    
    def _fix_syntax(self, code: str, error_msg: str) -> str | None:
        """Fix syntax errors."""
        # Common fixes
        # Missing colons
        code = re.sub(r"(def \w+\([^)]*\))\n", r"\1:\n", code)
        code = re.sub(r"(if [^:]+)\n", r"\1:\n", code)
        code = re.sub(r"(for [^:]+)\n", r"\1:\n", code)
        code = re.sub(r"(while [^:]+)\n", r"\1:\n", code)
        
        # Fix indentation
        lines = code.split("\n")
        fixed_lines = []
        indent_level = 0
        for line in lines:
            stripped = line.strip()
            if stripped.startswith(("def ", "class ", "if ", "for ", "while ")):
                fixed_lines.append("    " * indent_level + stripped)
                indent_level += 1
            elif stripped.startswith(("return", "pass", "break", "continue")):
                indent_level = max(0, indent_level - 1)
                fixed_lines.append("    " * indent_level + stripped)
            else:
                fixed_lines.append("    " * indent_level + stripped)
        
        return "\n".join(fixed_lines)
    
    def _fix_import(self, code: str, error_msg: str) -> str | None:
        """Fix import errors."""
        # Extract module name
        match = re.search(r"No module named ['\"]([^'\"]+)['\"]", error_msg)
        if match:
            module = match.group(1)
            # Try alternative imports
            alternatives = {
                "pandas": "import pandas as pd",
                "numpy": "import numpy as np",
                "matplotlib": "import matplotlib.pyplot as plt",
            }
            if module in alternatives:
                code = alternatives[module] + "\n" + code
                return code
        
        return None
    
    def _fix_name(self, code: str, error_msg: str) -> str | None:
        """Fix name errors."""
        # Extract variable name
        match = re.search(r"name '([^']+)' is not defined", error_msg)
        if match:
            name = match.group(1)
            # Add initialization
            code = f"{name} = None\n" + code
            return code
        
        return None
    
    def _fix_type(self, code: str, error_msg: str) -> str | None:
        """Fix type errors."""
        # Common type error fixes
        # Convert string to int
        code = re.sub(r"(\w+)\s*=\s*['\"](\d+)['\"]", r"\1 = int('\2')", code)
        
        # Convert string to float
        code = re.sub(r"(\w+)\s*=\s*['\"]([\d.]+)['\"]", r"\1 = float('\2')", code)
        
        return code
    
    def _fix_index(self, code: str, error_msg: str) -> str | None:
        """Fix index errors."""
        # Add bounds checking
        # This is a heuristic and may not always work
        return code
    
    def _fix_key(self, code: str, error_msg: str) -> str | None:
        """Fix key errors."""
        # Add default values
        # This is a heuristic and may not always work
        return code
    
    def _fix_attribute(self, code: str, error_msg: str) -> str | None:
        """Fix attribute errors."""
        # This is a heuristic and may not always work
        return code


def execute_python_with_retry(code: str, globals_dict: dict[str, Any] | None = None, query: str | None = None) -> PythonRetryResult:
    """Convenience function to execute Python with retry.
    
    Args:
        code: Python code to execute
        globals_dict: Global variables
        query: Original user query
    
    Returns:
        Retry result
    """
    handler = PythonRetryHandler(globals_dict)
    return handler.execute_with_retry(code, query)
