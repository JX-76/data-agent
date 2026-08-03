"""DockerExecute: Docker execution environment.

Provides Docker-based execution for safe code execution.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger("docker_execute")


@dataclass
class DockerResult:
    """Result of Docker execution."""
    success: bool
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    duration: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


class DockerExecutor:
    """Executes code in Docker containers."""
    
    def __init__(self, image: str = "python:3.11-slim", timeout: int = 30):
        self.image = image
        self.timeout = timeout
    
    def execute(self, code: str, language: str = "python") -> DockerResult:
        """Execute code in Docker.
        
        Args:
            code: Code to execute
            language: Language
        
        Returns:
            Execution result
        """
        import time
        
        start = time.time()
        
        try:
            # Create temporary file
            import tempfile
            import os
            
            with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
                f.write(code)
                temp_file = f.name
            
            # Run in Docker
            cmd = [
                "docker", "run", "--rm",
                "-v", f"{temp_file}:/code.py",
                self.image,
                "python", "/code.py",
            ]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
            
            duration = time.time() - start
            
            # Clean up
            os.unlink(temp_file)
            
            return DockerResult(
                success=result.returncode == 0,
                stdout=result.stdout,
                stderr=result.stderr,
                exit_code=result.returncode,
                duration=duration,
            )
        
        except subprocess.TimeoutExpired:
            return DockerResult(
                success=False,
                stderr="Execution timed out",
                exit_code=-1,
            )
        
        except Exception as e:
            return DockerResult(
                success=False,
                stderr=str(e),
                exit_code=-1,
            )
    
    def execute_sql(self, sql: str, db_path: str | None = None) -> DockerResult:
        """Execute SQL in Docker.
        
        Args:
            sql: SQL to execute
            db_path: Database path
        
        Returns:
            Execution result
        """
        import time
        
        start = time.time()
        
        try:
            # Create temporary file
            import tempfile
            import os
            
            with tempfile.NamedTemporaryFile(mode="w", suffix=".sql", delete=False) as f:
                f.write(sql)
                temp_file = f.name
            
            # Run in Docker with SQLite
            cmd = [
                "docker", "run", "--rm",
                "-v", f"{temp_file}:/query.sql",
            ]
            
            if db_path:
                cmd.extend(["-v", f"{db_path}:/data.db"])
                cmd.extend(["sqlite:alpine", "sqlite3", "/data.db", ".read /query.sql"])
            else:
                cmd.extend(["sqlite:alpine", "sqlite3", ".read /query.sql"])
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
            
            duration = time.time() - start
            
            # Clean up
            os.unlink(temp_file)
            
            return DockerResult(
                success=result.returncode == 0,
                stdout=result.stdout,
                stderr=result.stderr,
                exit_code=result.returncode,
                duration=duration,
            )
        
        except subprocess.TimeoutExpired:
            return DockerResult(
                success=False,
                stderr="Execution timed out",
                exit_code=-1,
            )
        
        except Exception as e:
            return DockerResult(
                success=False,
                stderr=str(e),
                exit_code=-1,
            )
    
    def is_available(self) -> bool:
        """Check if Docker is available.
        
        Returns:
            True if Docker is available
        """
        try:
            result = subprocess.run(
                ["docker", "--version"],
                capture_output=True,
                timeout=5,
            )
            return result.returncode == 0
        except Exception as e:
            logger.warning("bare_exception_caught", error=str(e))
            return False


def execute_in_docker(code: str, language: str = "python", image: str = "python:3.11-slim") -> DockerResult:
    """Convenience function to execute code in Docker.
    
    Args:
        code: Code to execute
        language: Language
        image: Docker image
    
    Returns:
        Execution result
    """
    executor = DockerExecutor(image)
    return executor.execute(code, language)
