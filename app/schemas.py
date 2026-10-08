"""Pydantic response models (these also drive the OpenAPI docs at /docs)."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class FileInfo(BaseModel):
    id: str
    filename: str
    feature_count: int
    crs: str | None = Field(None, description="CRS of the file, or MIXED if layers differ")
    status: str = Field(..., description="PROCESSING | COMPLETED | FAILED")
    error: str | None = None
    warnings: list[str] = []
    created_at: str


class FeatureOut(BaseModel):
    index: int
    layer: str | None
    geometry_type: str | None
    crs: str | None
    geometry: dict[str, Any] | None = Field(None, description="GeoJSON geometry, in the file's own CRS")
    properties: dict[str, Any]


class FeatureList(BaseModel):
    file_id: str
    total: int
    limit: int
    offset: int
    features: list[FeatureOut]


class MeasurementOut(BaseModel):
    feature_index: int
    layer: str | None
    geometry_type: str | None
    status: str = Field(..., description="MEASURED | NOT_APPLICABLE | UNSUPPORTED | NO_GEOMETRY | ERROR")
    area_m2: float | None = None
    perimeter_m: float | None = None
    length_m: float | None = None
    source_crs: str | None = None
    measurement_crs: str | None = Field(None, description="Projected CRS actually used for the calculation")
    method: str | None = Field(None, description="utm | native_projected")
    message: str | None = None


class MeasurementSummary(BaseModel):
    feature_count: int
    measured_count: int
    skipped_count: int
    total_area_m2: float
    total_length_m: float


class MeasurementList(BaseModel):
    file_id: str
    summary: MeasurementSummary
    limit: int
    offset: int
    measurements: list[MeasurementOut]
