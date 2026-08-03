"""FileLocator: File location and search utilities.

Provides file search and location capabilities.
"""

from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger("file_locator")


@dataclass
class FileInfo:
    """Information about a file."""
    path: str
    name: str
    size: int
    modified: float
    is_dir: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


class FileLocator:
    """Locates files in the filesystem."""
    
    def __init__(self, root_dir: str = "."):
        self.root_dir = Path(root_dir)
    
    def find_files(self, pattern: str, recursive: bool = True) -> list[FileInfo]:
        """Find files matching pattern.
        
        Args:
            pattern: File pattern (glob)
            recursive: Whether to search recursively
        
        Returns:
            List of file info
        """
        results = []
        
        if recursive:
            for path in self.root_dir.rglob(pattern):
                stat = path.stat()
                results.append(FileInfo(
                    path=str(path),
                    name=path.name,
                    size=stat.st_size,
                    modified=stat.st_mtime,
                    is_dir=path.is_dir(),
                ))
        else:
            for path in self.root_dir.glob(pattern):
                stat = path.stat()
                results.append(FileInfo(
                    path=str(path),
                    name=path.name,
                    size=stat.st_size,
                    modified=stat.st_mtime,
                    is_dir=path.is_dir(),
                ))
        
        logger.info("files_found", pattern=pattern, count=len(results))
        return results
    
    def find_by_name(self, name: str, recursive: bool = True) -> list[FileInfo]:
        """Find files by name.
        
        Args:
            name: File name
            recursive: Whether to search recursively
        
        Returns:
            List of file info
        """
        results = []
        
        if recursive:
            for path in self.root_dir.rglob("*"):
                if path.name == name:
                    stat = path.stat()
                    results.append(FileInfo(
                        path=str(path),
                        name=path.name,
                        size=stat.st_size,
                        modified=stat.st_mtime,
                        is_dir=path.is_dir(),
                    ))
        else:
            for path in self.root_dir.iterdir():
                if path.name == name:
                    stat = path.stat()
                    results.append(FileInfo(
                        path=str(path),
                        name=path.name,
                        size=stat.st_size,
                        modified=stat.st_mtime,
                        is_dir=path.is_dir(),
                    ))
        
        return results
    
    def find_by_content(self, keyword: str, file_pattern: str = "*") -> list[FileInfo]:
        """Find files containing keyword.
        
        Args:
            keyword: Search keyword
            file_pattern: File pattern
        
        Returns:
            List of file info
        """
        results = []
        
        for path in self.root_dir.rglob(file_pattern):
            if path.is_file():
                try:
                    with open(path, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                        if keyword in content:
                            stat = path.stat()
                            results.append(FileInfo(
                                path=str(path),
                                name=path.name,
                                size=stat.st_size,
                                modified=stat.st_mtime,
                                is_dir=False,
                            ))
                except Exception as e:
                    logger.warning("bare_exception_caught", error=str(e))
                    pass
        
        logger.info("files_found_by_content", keyword=keyword, count=len(results))
        return results
    
    def get_file_tree(self, max_depth: int = 3) -> dict[str, Any]:
        """Get file tree.
        
        Args:
            max_depth: Maximum depth
        
        Returns:
            File tree
        """
        def build_tree(path: Path, depth: int) -> dict[str, Any]:
            if depth > max_depth:
                return {}
            
            tree = {
                "name": path.name,
                "path": str(path),
                "is_dir": path.is_dir(),
            }
            
            if path.is_dir():
                tree["children"] = []
                try:
                    for child in path.iterdir():
                        if not child.name.startswith("."):
                            tree["children"].append(build_tree(child, depth + 1))
                except PermissionError:
                    pass
            
            return tree
        
        return build_tree(self.root_dir, 0)


def create_file_locator(root_dir: str = ".") -> FileLocator:
    """Convenience function to create file locator.
    
    Args:
        root_dir: Root directory
    
    Returns:
        File locator
    """
    return FileLocator(root_dir)
