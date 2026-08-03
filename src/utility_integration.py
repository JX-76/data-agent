"""File/Code/Test/Docker Integration: Integration utilities.

Integrates FileLocator, CodeFixer, TestGenerator, DockerExecutor with DAG.
"""

from __future__ import annotations

from typing import Any

from file_locator import FileLocator, FileInfo
from code_fix import CodeFixer, CodeFix
from test_gen import TestGenerator, TestSuite
from docker_execute import DockerExecutor, DockerResult


class FileIntegration:
    """Integrates file locator with DAG execution."""
    
    def __init__(self):
        self.locator = FileLocator()
    
    def find_files(self, pattern: str, recursive: bool = True) -> list[FileInfo]:
        """Find files.
        
        Args:
            pattern: File pattern
            recursive: Whether to search recursively
        
        Returns:
            List of file info
        """
        return self.locator.find_files(pattern, recursive)
    
    def find_by_name(self, name: str, recursive: bool = True) -> list[FileInfo]:
        """Find files by name.
        
        Args:
            name: File name
            recursive: Whether to search recursively
        
        Returns:
            List of file info
        """
        return self.locator.find_by_name(name, recursive)
    
    def find_by_content(self, keyword: str, file_pattern: str = "*") -> list[FileInfo]:
        """Find files by content.
        
        Args:
            keyword: Search keyword
            file_pattern: File pattern
        
        Returns:
            List of file info
        """
        return self.locator.find_by_content(keyword, file_pattern)
    
    def get_file_tree(self, max_depth: int = 3) -> dict[str, Any]:
        """Get file tree.
        
        Args:
            max_depth: Maximum depth
        
        Returns:
            File tree
        """
        return self.locator.get_file_tree(max_depth)


class CodeFixIntegration:
    """Integrates code fixer with DAG execution."""
    
    def __init__(self):
        self.fixer = CodeFixer()
    
    def fix_sql(self, sql: str) -> tuple[str, list[CodeFix]]:
        """Fix SQL.
        
        Args:
            sql: SQL to fix
        
        Returns:
            Fixed SQL and fixes
        """
        return self.fixer.fix_sql(sql)
    
    def fix_python(self, code: str) -> tuple[str, list[CodeFix]]:
        """Fix Python.
        
        Args:
            code: Python code to fix
        
        Returns:
            Fixed code and fixes
        """
        return self.fixer.fix_python(code)
    
    def fix_yaml(self, yaml: str) -> tuple[str, list[CodeFix]]:
        """Fix YAML.
        
        Args:
            yaml: YAML to fix
        
        Returns:
            Fixed YAML and fixes
        """
        return self.fixer.fix_yaml(yaml)


class TestGenIntegration:
    """Integrates test generator with DAG execution."""
    
    def __init__(self):
        self.generator = TestGenerator()
    
    def generate_tests(self, code: str, language: str = "python") -> TestSuite:
        """Generate tests.
        
        Args:
            code: Code to test
            language: Language
        
        Returns:
            Test suite
        """
        return self.generator.generate_tests(code, language)


class DockerIntegration:
    """Integrates Docker executor with DAG execution."""
    
    def __init__(self):
        self.executor = DockerExecutor()
    
    def execute(self, code: str, language: str = "python") -> DockerResult:
        """Execute code in Docker.
        
        Args:
            code: Code to execute
            language: Language
        
        Returns:
            Execution result
        """
        return self.executor.execute(code, language)
    
    def execute_sql(self, sql: str, db_path: str | None = None) -> DockerResult:
        """Execute SQL in Docker.
        
        Args:
            sql: SQL to execute
            db_path: Database path
        
        Returns:
            Execution result
        """
        return self.executor.execute_sql(sql, db_path)
    
    def is_available(self) -> bool:
        """Check if Docker is available.
        
        Returns:
            True if available
        """
        return self.executor.is_available()


def create_file_integration() -> FileIntegration:
    """Convenience function to create file integration.
    
    Returns:
        File integration
    """
    return FileIntegration()


def create_code_fix_integration() -> CodeFixIntegration:
    """Convenience function to create code fix integration.
    
    Returns:
        Code fix integration
    """
    return CodeFixIntegration()


def create_test_gen_integration() -> TestGenIntegration:
    """Convenience function to create test gen integration.
    
    Returns:
        Test gen integration
    """
    return TestGenIntegration()


def create_docker_integration() -> DockerIntegration:
    """Convenience function to create Docker integration.
    
    Returns:
        Docker integration
    """
    return DockerIntegration()
