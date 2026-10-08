"""Orchestrates a file: read -> measure -> persist."""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from shapely.geometry import mapping

from app.db import Database
from app.services.ingest import IngestError, read_features
from app.services.measure import crs_label, measure_feature

log = logging.getLogger(__name__)


def process_file(db: Database, file_id: str, path: Path, kind: str, max_uncompressed_bytes: int) -> None:
    """Process an already-stored upload. Marks the file COMPLETED or FAILED."""
    try:
        with tempfile.TemporaryDirectory(prefix="geo-") as tmp:
            result = read_features(path, kind, Path(tmp), max_uncompressed_bytes)

        records = []
        for idx, feat in enumerate(result.features):
            geom = feat.geometry
            has_geom = geom is not None and not geom.is_empty
            m = measure_feature(geom, feat.crs)
            records.append({
                "idx": idx,
                "layer": feat.layer,
                "geometry_type": geom.geom_type if has_geom else None,
                "crs": crs_label(feat.crs),
                "geometry": mapping(geom) if has_geom else None,  # GeoJSON, in the file's own CRS
                "properties": feat.properties,
                "measurement_status": m.status,
                "area_m2": m.area_m2,
                "perimeter_m": m.perimeter_m,
                "length_m": m.length_m,
                "measurement_crs": m.measurement_crs,
                "measurement_method": m.method,
                "measurement_message": m.message,
            })

        crs_set = {r["crs"] for r in records}
        file_crs = next(iter(crs_set)) if len(crs_set) == 1 else ("MIXED" if crs_set else None)
        db.complete_file(file_id, file_crs, result.warnings, records)
    except IngestError as exc:
        db.fail_file(file_id, str(exc))
        raise
    except Exception as exc:  # noqa: BLE001
        log.exception("unexpected failure processing %s", file_id)
        db.fail_file(file_id, "internal error while processing file")
        raise
