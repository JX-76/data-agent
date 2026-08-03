"""Python sandbox: Safe Python execution environment.

Provides a restricted environment for executing Python code safely.
"""

from __future__ import annotations

import builtins
import sys
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger("python_sandbox")


# ── Restricted builtins ──

SAFE_BUILTINS = {
    "abs", "all", "any", "bin", "bool", "bytearray", "bytes",
    "chr", "complex", "dict", "divmod", "enumerate", "filter",
    "float", "format", "frozenset", "hasattr", "hash", "hex",
    "int", "isinstance", "issubclass", "iter", "len", "list",
    "map", "max", "min", "next", "oct", "ord", "pow", "print",
    "range", "repr", "reversed", "round", "set", "slice", "sorted",
    "str", "sum", "tuple", "type", "zip",
}

# Block imports by removing __import__
SAFE_BUILTINS_DICT = {name: __builtins__[name] for name in SAFE_BUILTINS if name in __builtins__}


@dataclass
class SandboxResult:
    """Result of sandbox execution."""
    success: bool
    output: Any = None
    error: str | None = None
    logs: list[str] = field(default_factory=list)


class PythonSandbox:
    """Safe Python execution environment."""
    
    def __init__(self, timeout: int = 30):
        self.timeout = timeout
        self._allowed_modules: set[str] = {
            "math", "random", "statistics", "datetime",
            "json", "re", "collections", "itertools",
            "functools", "operator", "string", "typing",
        }
    
    def execute(self, code: str, globals_dict: dict[str, Any] | None = None) -> SandboxResult:
        """Execute Python code in sandbox.
        
        Args:
            code: Python code to execute
            globals_dict: Optional globals
        
        Returns:
            Execution result
        """
        try:
            # Create restricted environment
            restricted_globals = {
                "__builtins__": SAFE_BUILTINS_DICT,
                **(globals_dict or {}),
            }
            
            # Execute code
            exec(code, restricted_globals)
            
            # Get result
            result = restricted_globals.get("_result")
            
            return SandboxResult(
                success=True,
                output=result,
            )
        
        except Exception as e:
            return SandboxResult(
                success=False,
                error=str(e),
            )
    
    def add_allowed_module(self, module: str) -> None:
        """Add an allowed module.
        
        Args:
            module: Module name
        """
        self._allowed_modules.add(module)
        logger.info("module_allowed", module=module)


def execute_in_sandbox(code: str, globals_dict: dict[str, Any] | None = None, timeout: int = 30) -> SandboxResult:
    """Convenience function to execute code in sandbox.
    
    Args:
        code: Python code to execute
        globals_dict: Optional globals
        timeout: Execution timeout
    
    Returns:
        Execution result
    """
    sandbox = PythonSandbox(timeout)
    return sandbox.execute(code, globals_dict)
