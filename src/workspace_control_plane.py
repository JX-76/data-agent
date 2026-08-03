# -*- coding: utf-8 -*-
"""Controlled workspace browser, activity ledger and versioned-output contracts.

This module intentionally does not give the model/browser arbitrary filesystem or
shell access.  It exposes only a configured project root, rejects hidden and
sensitive paths, records each visible operation, and makes immutable per-round
copies of declared output files for download.
"""
from __future__ import unicode_literals

import hashlib
import os
import shutil
import time
import uuid


WORKSPACE_CONTRACT = "workspace_control_plane_v1"
WORKSPACE_ACTIVITY_CONTRACT = "workspace_activity_v1"
WORKSPACE_OUTPUT_CONTRACT = "workspace_versioned_output_v1"
WORKSPACE_MODES = ("plan", "act", "auto", "exit")

# Git internals, credentials, virtual environments and generated output storage
# must never be discoverable or downloadable through the browser API.
_BLOCKED_PARTS = set((
    ".git", ".env", ".venv", "venv", "env", "node_modules", "__pycache__",
    ".data_agent_workspace_outputs", ".pytest_cache", ".mypy_cache",
))
_BLOCKED_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".sqlite", ".db")
_BLOCKED_NAMES = set(("id_rsa", "id_ed25519", "credentials.json", "secrets.json"))
_SAFE_TEXT_BYTES = 512 * 1024
_SAFE_TREE_LIMIT = 1000
try:
    _STRING_TYPES = (basestring,)
    _TEXT_TYPE = unicode
except NameError:
    _STRING_TYPES = (str,)
    _TEXT_TYPE = str


class WorkspaceError(Exception):
    def __init__(self, code, message):
        Exception.__init__(self, message)
        self.code = code
        self.message = message


def _now():
    return time.time()


def _safe_text(value, limit=240):
    if value is None:
        value = u""
    if not isinstance(value, _TEXT_TYPE):
        try:
            value = _TEXT_TYPE(value, "utf-8")
        except (TypeError, UnicodeDecodeError):
            value = _TEXT_TYPE(str(value), "utf-8", "replace")
    value = value.replace(u"\r", u" ").replace(u"\n", u" ")
    return value[:limit]


def _path_parts(relative_path):
    return [piece for piece in relative_path.replace("\\", "/").split("/") if piece and piece != "."]


class WorkspaceControlPlane(object):
    """Filesystem-safe project workspace contract with an append-only activity view."""

    def __init__(self, root, output_root=None, clock=None):
        self.root = os.path.realpath(os.path.abspath(root))
        if not os.path.isdir(self.root):
            raise WorkspaceError("workspace_root_missing", "工作区根目录不存在。")
        self.output_root = os.path.realpath(output_root or os.path.join(self.root, ".data_agent_workspace_outputs"))
        self.clock = clock or _now
        self._modes = {}
        self._activities = {}
        self._rounds = {}

    def _is_within_root(self, path):
        # os.path.commonpath is unavailable on the supported Python 2.7 runtime.
        root_prefix = self.root if self.root.endswith(os.sep) else self.root + os.sep
        return path == self.root or path.startswith(root_prefix)

    def _blocked(self, relative_path):
        pieces = _path_parts(relative_path)
        if not pieces:
            return False
        lower = [piece.lower() for piece in pieces]
        if any(piece in _BLOCKED_PARTS for piece in lower):
            return True
        name = lower[-1]
        return name in _BLOCKED_NAMES or name.endswith(_BLOCKED_SUFFIXES)

    def resolve(self, relative_path, allow_root=False):
        if relative_path is None:
            relative_path = ""
        if not isinstance(relative_path, _STRING_TYPES):
            raise WorkspaceError("invalid_path", "文件路径必须为字符串。")
        relative_path = relative_path.strip().replace("\\", "/")
        if not relative_path and allow_root:
            return self.root, ""
        if not relative_path or os.path.isabs(relative_path) or ".." in _path_parts(relative_path):
            raise WorkspaceError("workspace_path_denied", "文件路径不在允许的项目工作区内。")
        if self._blocked(relative_path):
            raise WorkspaceError("sensitive_path_denied", "该文件按安全策略不可浏览或下载。")
        candidate = os.path.realpath(os.path.join(self.root, relative_path))
        if not self._is_within_root(candidate) or candidate == self.output_root or candidate.startswith(self.output_root + os.sep):
            raise WorkspaceError("workspace_path_denied", "文件路径不在允许的项目工作区内。")
        return candidate, relative_path

    def _entry(self, absolute_path, relative_path):
        is_dir = os.path.isdir(absolute_path)
        return {
            "path": relative_path.replace("\\", "/"),
            "name": os.path.basename(relative_path) if relative_path else os.path.basename(self.root),
            "kind": "directory" if is_dir else "file",
            "size_bytes": None if is_dir else int(os.path.getsize(absolute_path)),
            "modified_at": int(os.path.getmtime(absolute_path)),
        }

    def tree(self, relative_path="", max_entries=_SAFE_TREE_LIMIT):
        absolute_path, relative_path = self.resolve(relative_path, allow_root=True)
        if not os.path.isdir(absolute_path):
            raise WorkspaceError("not_a_directory", "目标不是目录。")
        entries = []
        for name in sorted(os.listdir(absolute_path), key=lambda value: (not os.path.isdir(os.path.join(absolute_path, value)), value.lower())):
            child_relative = "/".join(filter(None, (relative_path, name)))
            if self._blocked(child_relative):
                continue
            child = os.path.realpath(os.path.join(absolute_path, name))
            if not self._is_within_root(child):
                continue
            entries.append(self._entry(child, child_relative))
            if len(entries) >= max(1, min(int(max_entries), _SAFE_TREE_LIMIT)):
                break
        return {"contract": WORKSPACE_CONTRACT, "root_name": os.path.basename(self.root),
                "path": relative_path, "entries": entries, "sensitive_paths_hidden": True,
                "truncated": len(entries) >= max_entries}

    def file_view(self, relative_path):
        absolute_path, relative_path = self.resolve(relative_path)
        if not os.path.isfile(absolute_path):
            raise WorkspaceError("not_a_file", "目标不是文件。")
        size = os.path.getsize(absolute_path)
        if size > _SAFE_TEXT_BYTES:
            raise WorkspaceError("file_too_large", "文件过大，不能在工作区预览。请使用受控下载。")
        try:
            with open(absolute_path, "rb") as handle:
                raw = handle.read()
            content = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise WorkspaceError("binary_preview_denied", "二进制文件不支持在线预览。")
        return {"contract": WORKSPACE_CONTRACT, "file": self._entry(absolute_path, relative_path),
                "content": content, "content_sha256": hashlib.sha256(raw).hexdigest(), "truncated": False}

    def download_path(self, relative_path):
        absolute_path, relative_path = self.resolve(relative_path)
        if not os.path.isfile(absolute_path):
            raise WorkspaceError("not_a_file", "只能下载文件。")
        return absolute_path, relative_path

    def mode(self, session_id):
        return self._modes.get(session_id or "default", "plan")

    def set_mode(self, session_id, mode, actor="anonymous"):
        mode = (mode or "").lower()
        if mode not in WORKSPACE_MODES:
            raise WorkspaceError("invalid_mode", "模式必须是 plan、act、auto 或 exit。")
        session_id = session_id or "default"
        self._modes[session_id] = mode
        return self.record_activity(session_id, "mode_change", "completed", "模式已切换为 %s" % mode,
                                    details={"mode": mode, "actor": _safe_text(actor, 80)})

    def record_activity(self, session_id, activity_type, status, summary, details=None, trace_id=None, files=None):
        session_id = session_id or "default"
        event = {"contract": WORKSPACE_ACTIVITY_CONTRACT, "activity_id": "act_" + uuid.uuid4().hex,
                 "session_id": session_id, "type": _safe_text(activity_type, 48),
                 "status": _safe_text(status, 32), "summary": _safe_text(summary),
                 "details": dict(details or {}), "trace_id": trace_id,
                 "files": list(files or []), "created_at": self.clock()}
        self._activities.setdefault(session_id, []).append(event)
        self._activities[session_id] = self._activities[session_id][-200:]
        return dict(event)

    def activities(self, session_id, limit=100):
        session_id = session_id or "default"
        limited = self._activities.get(session_id, [])[-max(1, min(int(limit), 200)):]
        return {"contract": WORKSPACE_ACTIVITY_CONTRACT, "session_id": session_id,
                "mode": self.mode(session_id), "items": [dict(item) for item in limited]}

    def begin_round(self, session_id, label=None, trace_id=None):
        session_id = session_id or "default"
        number = len(self._rounds.get(session_id, [])) + 1
        round_id = "round_%04d_%s" % (number, uuid.uuid4().hex[:8])
        record = {"contract": WORKSPACE_OUTPUT_CONTRACT, "round_id": round_id, "number": number,
                  "session_id": session_id, "label": _safe_text(label or "工作区更新"), "trace_id": trace_id,
                  "created_at": self.clock(), "outputs": []}
        self._rounds.setdefault(session_id, []).append(record)
        self.record_activity(session_id, "round_started", "running", "开始第 %d 轮更新" % number,
                             details={"round_id": round_id}, trace_id=trace_id)
        return dict(record)

    def add_round_output(self, session_id, round_id, relative_path):
        session_id = session_id or "default"
        absolute_path, relative_path = self.download_path(relative_path)
        record = next((item for item in self._rounds.get(session_id, []) if item["round_id"] == round_id), None)
        if record is None:
            raise WorkspaceError("round_not_found", "找不到对应的更新轮次。")
        target_dir = os.path.join(self.output_root, session_id, round_id)
        if not os.path.isdir(target_dir):
            os.makedirs(target_dir)
        target = os.path.join(target_dir, os.path.basename(relative_path))
        # Preserve the exact delivered version; suffix collisions deterministically.
        base, ext = os.path.splitext(target)
        index = 2
        while os.path.exists(target):
            target = "%s_v%d%s" % (base, index, ext)
            index += 1
        shutil.copy2(absolute_path, target)
        with open(target, "rb") as handle:
            digest = hashlib.sha256(handle.read()).hexdigest()
        output_id = "out_" + uuid.uuid4().hex
        output = {"output_id": output_id, "source_path": relative_path, "version_name": os.path.basename(target),
                  "sha256": digest, "size_bytes": os.path.getsize(target), "created_at": self.clock()}
        record["outputs"].append(output)
        self.record_activity(session_id, "versioned_output", "completed", "已生成可下载版本：%s" % output["version_name"],
                             details={"round_id": round_id, "output_id": output_id}, files=[relative_path])
        return dict(output)

    def rounds(self, session_id):
        return {"contract": WORKSPACE_OUTPUT_CONTRACT, "session_id": session_id or "default",
                "rounds": [dict(item) for item in self._rounds.get(session_id or "default", [])]}

    def output_download_path(self, session_id, round_id, output_id):
        record = next((item for item in self._rounds.get(session_id or "default", []) if item["round_id"] == round_id), None)
        if not record:
            raise WorkspaceError("round_not_found", "找不到对应的更新轮次。")
        output = next((item for item in record["outputs"] if item["output_id"] == output_id), None)
        if not output:
            raise WorkspaceError("output_not_found", "找不到对应的版本化输出。")
        candidate = os.path.realpath(os.path.join(self.output_root, session_id or "default", round_id, output["version_name"]))
        if not candidate.startswith(self.output_root + os.sep) or not os.path.isfile(candidate):
            raise WorkspaceError("output_not_found", "版本化输出不可用。")
        return candidate, output["version_name"]


__all__ = ["WorkspaceControlPlane", "WorkspaceError", "WORKSPACE_CONTRACT", "WORKSPACE_ACTIVITY_CONTRACT", "WORKSPACE_OUTPUT_CONTRACT", "WORKSPACE_MODES"]
