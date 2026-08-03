# -*- coding: utf-8 -*-
"""HTTP adapter for governed enterprise data-source setup and verification."""
from __future__ import unicode_literals

from typing import List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from enterprise_data_source import EnterpriseDataSourceError, EnterpriseDataSourceService

router = APIRouter(prefix="/api/data-sources", tags=["enterprise-data-source"])
service = EnterpriseDataSourceService()


class EnterpriseDataSourceRequest(BaseModel):
    source_id: str = Field(default="enterprise_default", min_length=1, max_length=80)
    display_name: str = Field(default="企业数据源", min_length=1, max_length=100)
    db_type: str = Field(..., min_length=2, max_length=32)
    host: str = Field(..., min_length=1, max_length=253)
    port: int = Field(..., ge=1, le=65535)
    database: str = Field(..., min_length=1, max_length=128)
    schema_name: str = Field(default="public", alias="schema", min_length=1, max_length=128)
    username: str = Field(default="", max_length=100)
    credential_reference: str = Field(..., min_length=6, max_length=160)
    ssl_mode: str = Field(default="require", min_length=4, max_length=16)
    connect_timeout_seconds: int = Field(default=5, ge=1, le=30)
    allowed_tables: List[str] = Field(..., min_items=1, max_items=100)
    tenant_scope_mode: str = Field(default="external_rls_required", max_length=40)

    class Config:
        allow_population_by_field_name = True

    def to_config(self):
        data = self.dict(by_alias=True)
        return data


def _error(exc):
    raise HTTPException(status_code=exc.status_code,
                        detail={"contract": "enterprise_data_source_error_v1", "code": exc.code,
                                "message": exc.message})


@router.get("/capabilities")
def capabilities():
    return service.capabilities()


@router.get("/config")
def get_config():
    return service.public_config()


@router.put("/config")
def put_config(request: EnterpriseDataSourceRequest):
    try:
        return service.configure(request.to_config())
    except EnterpriseDataSourceError as exc:
        _error(exc)


@router.post("/test")
def test_connection():
    try:
        return service.test_connection()
    except EnterpriseDataSourceError as exc:
        _error(exc)


@router.post("/schema-probe")
def schema_probe():
    try:
        return service.probe_schema()
    except EnterpriseDataSourceError as exc:
        _error(exc)


@router.post("/disconnect")
def disconnect():
    return service.disconnect()
