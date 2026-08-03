"""CodeFix: Code fixing utilities.

Provides code fixing capabilities for common issues.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger("code_fix")


@dataclass
class CodeFix:
    """A code fix."""
    issue: str
    original: str
    fixed: str
    line: int | None = None
    confidence: float = 1.0


class CodeFixer:
    """Fixes code issues."""
    
    def __init__(self):
        self._fixes: list[CodeFix] = []
    
    def fix_sql(self, sql: str) -> tuple[str, list[CodeFix]]:
        """Fix SQL issues.
        
        Args:
            sql: SQL to fix
        
        Returns:
            Fixed SQL and list of fixes
        """
        fixes = []
        fixed = sql
        
        # Fix 1: Missing semicolons
        if not fixed.rstrip().endswith(";"):
            fixed = fixed.rstrip() + ";"
            fixes.append(CodeFix(
                issue="missing_semicolon",
                original=sql,
                fixed=fixed,
            ))
        
        # Fix 2: Unbalanced parentheses
        open_parens = fixed.count("(")
        close_parens = fixed.count(")")
        if open_parens > close_parens:
            fixed = fixed + ")" * (open_parens - close_parens)
            fixes.append(CodeFix(
                issue="unbalanced_parens",
                original=sql,
                fixed=fixed,
            ))
        
        # Fix 3: Missing quotes
        single_quotes = fixed.count("'")
        double_quotes = fixed.count('"')
        if single_quotes % 2 != 0:
            fixed = fixed + "'"
            fixes.append(CodeFix(
                issue="unbalanced_quotes",
                original=sql,
                fixed=fixed,
            ))
        
        return fixed, fixes
    
    def fix_python(self, code: str) -> tuple[str, list[CodeFix]]:
        """Fix Python issues.
        
        Args:
            code: Python code to fix
        
        Returns:
            Fixed code and list of fixes
        """
        fixes = []
        fixed = code
        
        # Fix 1: Missing colons
        patterns = [
            (r"(def \w+\([^)]*\))\n", r"\1:\n"),
            (r"(if [^:]+)\n", r"\1:\n"),
            (r"(for [^:]+)\n", r"\1:\n"),
            (r"(while [^:]+)\n", r"\1:\n"),
            (r"(class \w+[^:]*)\n", r"\1:\n"),
        ]
        
        for pattern, replacement in patterns:
            new_fixed = re.sub(pattern, replacement, fixed)
            if new_fixed != fixed:
                fixes.append(CodeFix(
                    issue="missing_colon",
                    original=fixed,
                    fixed=new_fixed,
                ))
                fixed = new_fixed
        
        # Fix 2: Fix indentation
        lines = fixed.split("\n")
        fixed_lines = []
        indent_level = 0
        
        for line in lines:
            stripped = line.strip()
            if stripped.startswith(("def ", "class ")):
                fixed_lines.append("    " * indent_level + stripped)
                indent_level += 1
            elif stripped.startswith(("return", "pass", "break", "continue")):
                indent_level = max(0, indent_level - 1)
                fixed_lines.append("    " * indent_level + stripped)
            elif stripped.startswith(("if ", "for ", "while ")):
                fixed_lines.append("    " * indent_level + stripped)
                indent_level += 1
            else:
                fixed_lines.append("    " * indent_level + stripped)
        
        fixed = "\n".join(fixed_lines)
        
        # Fix 3: Missing imports
        if "pd." in fixed and "import pandas" not in fixed:
            fixed = "import pandas as pd\n" + fixed
            fixes.append(CodeFix(
                issue="missing_import",
                original=code,
                fixed=fixed,
            ))
        
        if "np." in fixed and "import numpy" not in fixed:
            fixed = "import numpy as np\n" + fixed
            fixes.append(CodeFix(
                issue="missing_import",
                original=code,
                fixed=fixed,
            ))
        
        return fixed, fixes
    
    def fix_yaml(self, yaml: str) -> tuple[str, list[CodeFix]]:
        """Fix YAML issues.
        
        Args:
            yaml: YAML to fix
        
        Returns:
            Fixed YAML and list of fixes
        """
        fixes = []
        fixed = yaml
        
        # Fix 1: Fix escape sequences in double quotes
        # Convert problematic escape sequences
        fixed = re.sub(r'"(.*?)\\d(.*?)"', r"'\1\\d\2'", fixed)
        
        return fixed, fixes
    
    def get_fixes(self) -> list[CodeFix]:
        """Get all fixes.
        
        Returns:
            List of fixes
        """
        return self._fixes


def fix_code(code: str, language: str = "python") -> tuple[str, list[CodeFix]]:
    """Convenience function to fix code.
    
    Args:
        code: Code to fix
        language: Language (python, sql, yaml)
    
    Returns:
        Fixed code and list of fixes
    """
    fixer = CodeFixer()
    
    if language == "sql":
        return fixer.fix_sql(code)
    elif language == "python":
        return fixer.fix_python(code)
    elif language == "yaml":
        return fixer.fix_yaml(code)
    else:
        return code, []
