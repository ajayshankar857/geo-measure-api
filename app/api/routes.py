"""HTTP endpoints."""
from __future__ import annotations

import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile

from app.config import Settings
from app.db import Database
from app.schemas import FeatureList, FeatureOut, FileInfo, MeasurementList, MeasurementOut, MeasurementSummary
from app.services.ingest import SUPPORTED_EXTENSIONS, IngestError
from app.services.processor import process_file

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/files", tags=["files"])


def _db(request: Request) -> Database:
    return request.app.state.db


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _file_or_404(db: Database, file_id: str) -> dict:
    record = db.get_file(file_id)
    if record is None:
        raise HTTPException(404, "file not found")
    return record


def _file_info(record: dict) -> FileInfo:
    return FileInfo(**{k: record[k] for k in FileInfo.model_fields})


def _require_completed(record: dict) -> None:
    if record["status"] != "COMPLETED":
        raise HTTPException(409, f"file is {record['status']}: {record.get('error') or 'not ready'}")


# Plain `def` handlers run in FastAPI's threadpool, so CPU-bound geometry work
# does not block the event loop.
@router.post("/", response_model=FileInfo, status_code=201, summary="Upload and process a file")
def upload_file(request: Request, file: UploadFile = File(...)) -> FileInfo:
    db, settings = _db(request), _settings(request)

    filename = Path(file.filename or "").name
    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise HTTPException(400, "unsupported file type; upload a .zip (Shapefile) or a .kml")

    file_id = uuid.uuid4().hex
    target_dir = settings.upload_dir / file_id
    target_dir.mkdir(parents=True)
    stored = target_dir / f"original{ext}"

    max_bytes = settings.max_upload_mb * 1024 * 1024
    written = 0
    with open(stored, "wb") as out:  # stream to disk; never hold the upload in memory
        while chunk := file.file.read(1024 * 1024):
            written += len(chunk)
            if written > max_bytes:
                out.close()
                stored.unlink(missing_ok=True)
                raise HTTPException(413, f"file exceeds the {settings.max_upload_mb} MB limit")
            out.write(chunk)

    db.create_file(file_id, filename, SUPPORTED_EXTENSIONS[ext])
    try:
        process_file(db, file_id, stored, SUPPORTED_EXTENSIONS[ext],
                     settings.max_uncompressed_mb * 1024 * 1024)
    except IngestError as exc:
        raise HTTPException(422, detail={"id": file_id, "status": "FAILED", "message": str(exc)})
    except Exception:  # already logged in the processor
        raise HTTPException(500, detail={"id": file_id, "status": "FAILED",
                                         "message": "internal error while processing file"})
    return _file_info(db.get_file(file_id))


@router.get("/{file_id}/", response_model=FileInfo, summary="File information")
def get_file(request: Request, file_id: str) -> FileInfo:
    return _file_info(_file_or_404(_db(request), file_id))


@router.get("/{file_id}/features/", response_model=FeatureList, summary="Extracted features")
def get_features(request: Request, file_id: str,
                 limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)) -> FeatureList:
    db = _db(request)
    record = _file_or_404(db, file_id)
    _require_completed(record)
    rows = db.get_features(file_id, limit, offset)
    return FeatureList(
        file_id=file_id, total=record["feature_count"], limit=limit, offset=offset,
        features=[FeatureOut(index=r["idx"], layer=r["layer"], geometry_type=r["geometry_type"],
                             crs=r["crs"], geometry=r["geometry"], properties=r["properties"])
                  for r in rows],
    )


@router.get("/{file_id}/measurements/", response_model=MeasurementList, summary="Per-feature measurements")
def get_measurements(request: Request, file_id: str,
                     limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)) -> MeasurementList:
    db = _db(request)
    record = _file_or_404(db, file_id)
    _require_completed(record)
    rows = db.get_features(file_id, limit, offset)
    return MeasurementList(
        file_id=file_id,
        summary=MeasurementSummary(**db.measurement_summary(file_id)),
        limit=limit, offset=offset,
        measurements=[MeasurementOut(
            feature_index=r["idx"], layer=r["layer"], geometry_type=r["geometry_type"],
            status=r["measurement_status"], area_m2=r["area_m2"], perimeter_m=r["perimeter_m"],
            length_m=r["length_m"], source_crs=r["crs"], measurement_crs=r["measurement_crs"],
            method=r["measurement_method"], message=r["measurement_message"],
        ) for r in rows],
    )
