# -*- coding: utf-8 -*-
"""FastAPI adapter for the controlled workspace contract."""
from __future__ import unicode_literals

import os
from pathlib import Path
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from workspace_control_plane import WorkspaceControlPlane, WorkspaceError

PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
workspace = WorkspaceControlPlane(os.environ.get("DATA_AGENT_WORKSPACE_ROOT", PROJECT_ROOT))
router = APIRouter(prefix="/api/workspace", tags=["workspace"])


class ModeRequest(BaseModel):
    session_id: str = Field(default="default", min_length=1, max_length=120)
    mode: str = Field(..., min_length=3, max_length=8)


class RoundRequest(BaseModel):
    session_id: str = Field(default="default", min_length=1, max_length=120)
    label: str = Field(default="工作区更新", max_length=240)
    trace_id: str = Field(default=None, max_length=160)


class OutputRequest(BaseModel):
    session_id: str = Field(default="default", min_length=1, max_length=120)
    path: str = Field(..., min_length=1, max_length=500)


def _error(exc):
    raise HTTPException(status_code=400, detail={"contract": "workspace_error_v1", "code": exc.code, "message": exc.message})


@router.get("/tree")
def tree(path: str = Query(default="", max_length=500)):
    try: return workspace.tree(path)
    except WorkspaceError as exc: _error(exc)


@router.get("/file")
def file_view(path: str = Query(..., min_length=1, max_length=500)):
    try: return workspace.file_view(path)
    except WorkspaceError as exc: _error(exc)


@router.get("/download")
def download(path: str = Query(..., min_length=1, max_length=500)):
    try:
        absolute, name = workspace.download_path(path)
        return FileResponse(absolute, filename=name, media_type="application/octet-stream")
    except WorkspaceError as exc: _error(exc)


@router.get("/activity")
def activity(session_id: str = Query(default="default", max_length=120), limit: int = Query(default=100, ge=1, le=200)):
    return workspace.activities(session_id, limit)


@router.post("/mode")
def set_mode(request: ModeRequest):
    try:
        event = workspace.set_mode(request.session_id, request.mode)
        return {"contract": "workspace_mode_v1", "session_id": request.session_id, "mode": workspace.mode(request.session_id), "event": event}
    except WorkspaceError as exc: _error(exc)


@router.get("/rounds")
def rounds(session_id: str = Query(default="default", max_length=120)):
    return workspace.rounds(session_id)


@router.post("/rounds")
def begin_round(request: RoundRequest):
    try: return workspace.begin_round(request.session_id, request.label, request.trace_id)
    except WorkspaceError as exc: _error(exc)


@router.post("/rounds/{round_id}/outputs")
def add_output(round_id: str, request: OutputRequest):
    try: return workspace.add_round_output(request.session_id, round_id, request.path)
    except WorkspaceError as exc: _error(exc)


@router.get("/rounds/{round_id}/outputs/{output_id}/download")
def output_download(round_id: str, output_id: str, session_id: str = Query(default="default", max_length=120)):
    try:
        absolute, name = workspace.output_download_path(session_id, round_id, output_id)
        return FileResponse(absolute, filename=name, media_type="application/octet-stream")
    except WorkspaceError as exc: _error(exc)
