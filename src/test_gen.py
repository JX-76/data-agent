"""TestGen: Test generation utilities.

Generates tests for code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger("test_gen")


@dataclass
class TestCase:
    """A single test case."""
    name: str
    input_data: dict[str, Any]
    expected_output: Any
    description: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TestSuite:
    """A test suite."""
    name: str
    cases: list[TestCase] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    
    def add_case(self, case: TestCase) -> None:
        """Add a test case.
        
        Args:
            case: Test case
        """
        self.cases.append(case)
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary.
        
        Returns:
            Dictionary representation
        """
        return {
            "name": self.name,
            "cases": [
                {
                    "name": c.name,
                    "input": c.input_data,
                    "expected": c.expected_output,
                    "description": c.description,
                }
                for c in self.cases
            ],
        }


class TestGenerator:
    """Generates tests for code."""
    
    def __init__(self):
        self._suites: list[TestSuite] = []
    
    def generate_tests(self, code: str, language: str = "python") -> TestSuite:
        """Generate tests for code.
        
        Args:
            code: Code to test
            language: Language
        
        Returns:
            Test suite
        """
        suite = TestSuite(name="Generated Tests")
        
        if language == "python":
            suite = self._generate_python_tests(code)
        elif language == "sql":
            suite = self._generate_sql_tests(code)
        
        self._suites.append(suite)
        
        logger.info("tests_generated", language=language, cases=len(suite.cases))
        
        return suite
    
    def _generate_python_tests(self, code: str) -> TestSuite:
        """Generate Python tests.
        
        Args:
            code: Python code
        
        Returns:
            Test suite
        """
        suite = TestSuite(name="Python Tests")
        
        # Extract function names
        import re
        func_pattern = r"def\s+(\w+)\s*\("
        funcs = re.findall(func_pattern, code)
        
        for func in funcs:
            # Generate basic test case
            suite.add_case(TestCase(
                name=f"test_{func}",
                input_data={"func": func, "args": []},
                expected_output="success",
                description=f"Test {func} function",
            ))
        
        return suite
    
    def _generate_sql_tests(self, code: str) -> TestSuite:
        """Generate SQL tests.
        
        Args:
            code: SQL code
        
        Returns:
            Test suite
        """
        suite = TestSuite(name="SQL Tests")
        
        # Basic SQL test cases
        suite.add_case(TestCase(
            name="test_sql_syntax",
            input_data={"sql": code},
            expected_output="valid",
            description="Test SQL syntax",
        ))
        
        suite.add_case(TestCase(
            name="test_sql_execution",
            input_data={"sql": code},
            expected_output="success",
            description="Test SQL execution",
        ))
        
        return suite
    
    def get_suites(self) -> list[TestSuite]:
        """Get all test suites.
        
        Returns:
            List of test suites
        """
        return self._suites


def generate_tests(code: str, language: str = "python") -> TestSuite:
    """Convenience function to generate tests.
    
    Args:
        code: Code to test
        language: Language
    
    Returns:
        Test suite
    """
    generator = TestGenerator()
    return generator.generate_tests(code, language)
