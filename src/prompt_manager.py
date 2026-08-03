"""Prompt version management: version-controlled router prompts with rollback.

Stores prompt versions as JSON, supports:
- Version tagging (auto-increment + timestamp + author)
- Rollback to any previous version
- Diff between versions
- Active version tracking

Usage:
    from prompt_manager import PromptManager
    pm = PromptManager()
    current = pm.get_prompt("router")
    pm.save_version("router", new_prompt, "Improved metric disambiguation")
    pm.rollback("router", to_version=2)
"""

from __future__ import annotations

import copy
import difflib
import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import structlog

logger = structlog.get_logger("prompt-manager")


@dataclass
class PromptVersion:
    version: int
    prompt: str
    checksum: str
    description: str
    author: str
    timestamp: str
    metrics: dict  # Optional: eval metrics snapshot for this version


@dataclass
class Prompt:
    """A managed prompt with version history."""
    name: str
    current_version: int
    active_prompt: str
    versions: list[PromptVersion] = field(default_factory=list)


class PromptManager:
    """Version-controlled prompt storage with rollback support."""

    def __init__(self, storage_path: str = "prompts.json"):
        self.storage_path = Path(storage_path)
        self._prompts: dict[str, Prompt] = {}
        self._restore()

    def _checksum(self, text: str) -> str:
        return hashlib.sha256(text.encode()).hexdigest()[:16]

    def _restore(self):
        if self.storage_path.exists():
            try:
                data = json.loads(self.storage_path.read_text())
                for name, raw in data.items():
                    versions = [PromptVersion(**v) for v in raw.get("versions", [])]
                    current = raw.get("current_version", 0)
                    active = raw.get("active_prompt", "")
                    self._prompts[name] = Prompt(
                        name=name, current_version=current,
                        active_prompt=active, versions=versions,
                    )
            except Exception as e:
                logger.warning("prompt_restore_failed", error=str(e))

    def _save(self):
        data = {}
        for name, p in self._prompts.items():
            data[name] = {
                "current_version": p.current_version,
                "active_prompt": p.active_prompt,
                "versions": [v.__dict__ for v in p.versions],
            }
        self.storage_path.write_text(json.dumps(data, ensure_ascii=False, indent=2))

    def register(
        self,
        name: str,
        initial_prompt: str,
        description: str = "Initial version",
        author: str = "system",
    ) -> int:
        """Register a new named prompt. Returns version number."""
        if name in self._prompts:
            return self.save_version(name, initial_prompt, description, author)

        v1 = PromptVersion(
            version=1,
            prompt=initial_prompt,
            checksum=self._checksum(initial_prompt),
            description=description,
            author=author,
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
            metrics={},
        )
        self._prompts[name] = Prompt(
            name=name, current_version=1, active_prompt=initial_prompt,
            versions=[v1],
        )
        self._save()
        logger.info("prompt_registered", name=name, version=1)
        return 1

    def save_version(
        self,
        name: str,
        new_prompt: str,
        description: str = "",
        author: str = "system",
        metrics: Optional[dict] = None,
    ) -> int:
        """Save a new version of a prompt. Returns new version number."""
        if name not in self._prompts:
            return self.register(name, new_prompt, description, author)

        entry = self._prompts[name]

        # Skip if unchanged
        if new_prompt == entry.active_prompt:
            logger.info("prompt_unchanged", name=name)
            return entry.current_version

        new_version = entry.current_version + 1
        v = PromptVersion(
            version=new_version,
            prompt=new_prompt,
            checksum=self._checksum(new_prompt),
            description=description,
            author=author,
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
            metrics=metrics or {},
        )

        entry.versions.append(v)
        entry.current_version = new_version
        entry.active_prompt = new_prompt

        self._save()
        logger.info("prompt_version_saved", name=name, version=new_version,
                    checksum=v.checksum, description=description)
        return new_version

    def get_prompt(self, name: str) -> Optional[str]:
        """Get the active version of a prompt."""
        entry = self._prompts.get(name)
        return entry.active_prompt if entry else None

    def get_version(self, name: str, version: int) -> Optional[str]:
        """Get a specific version of a prompt."""
        entry = self._prompts.get(name)
        if not entry:
            return None
        for v in entry.versions:
            if v.version == version:
                return v.prompt
        return None

    def rollback(self, name: str, to_version: int) -> bool:
        """Rollback to a specific version."""
        entry = self._prompts.get(name)
        if not entry:
            return False

        target = self.get_version(name, to_version)
        if not target:
            logger.warning("rollback_version_not_found", name=name, version=to_version)
            return False

        entry.active_prompt = target
        entry.current_version = to_version  # Don't create new version for rollback
        self._save()
        logger.info("prompt_rolled_back", name=name, to_version=to_version)
        return True

    def diff(self, name: str, v1: int, v2: Optional[int] = None) -> str:
        """Generate a unified diff between two versions."""
        p1 = self.get_version(name, v1)
        if not p1:
            return f"Version {v1} not found"

        if v2 is None:
            # Diff against active
            entry = self._prompts.get(name)
            p2 = entry.active_prompt if entry else ""
        else:
            p2 = self.get_version(name, v2)
            if not p2:
                return f"Version {v2} not found"

        diff = difflib.unified_diff(
            p1.splitlines(keepends=True),
            p2.splitlines(keepends=True),
            fromfile=f"{name} v{v1}",
            tofile=f"{name} v{v2 or 'active'}",
        )
        return "".join(diff)

    def history(self, name: str) -> list[dict]:
        """Get version history for a prompt."""
        entry = self._prompts.get(name)
        if not entry:
            return []
        return [
            {
                "version": v.version,
                "checksum": v.checksum,
                "description": v.description,
                "author": v.author,
                "timestamp": v.timestamp,
                "is_active": v.version == entry.current_version,
            }
            for v in sorted(entry.versions, key=lambda x: x.version, reverse=True)
        ]

    def list(self) -> list[str]:
        return list(self._prompts.keys())


# ── Global ──

_pm: PromptManager | None = None


def get_prompt_manager() -> PromptManager:
    global _pm
    if _pm is None:
        _pm = PromptManager()
    return _pm
