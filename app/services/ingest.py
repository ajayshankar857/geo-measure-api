"""Reading geospatial files (zipped Shapefile, KML) into plain Python objects."""
from __future__ import annotations

import math
import os
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

import numpy as np
import pyogrio
import shapely
from pyogrio import raw as pyogrio_raw
from pyproj import CRS
from shapely.geometry.base import BaseGeometry

# Let GDAL rebuild a missing .shx instead of failing on slightly broken zips.
os.environ.setdefault("SHAPE_RESTORE_SHX", "YES")

SUPPORTED_EXTENSIONS = {".zip": "shapefile", ".kml": "kml"}
WGS84 = CRS.from_epsg(4326)


class IngestError(Exception):
    """The uploaded file is unusable (bad archive, unreadable data, unknown CRS...)."""


@dataclass
class RawFeature:
    layer: str
    geometry: BaseGeometry | None
    properties: dict[str, Any]
    crs: CRS


@dataclass
class IngestResult:
    features: list[RawFeature] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def safe_extract(zip_path: Path, dest: Path, max_bytes: int) -> None:
    """Extract an archive defensively (zip-slip + zip-bomb protection)."""
    dest = dest.resolve()
    try:
        with zipfile.ZipFile(zip_path) as zf:
            total = 0
            for info in zf.infolist():
                if info.is_dir():
                    continue
                parts = PurePosixPath(info.filename.replace("\\", "/")).parts
                if "__MACOSX" in parts or parts[-1].startswith("."):
                    continue
                target = (dest.joinpath(*parts)).resolve()
                if not target.is_relative_to(dest):
                    raise IngestError("zip contains an unsafe path")
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, open(target, "wb") as out:
                    while chunk := src.read(1024 * 1024):
                        total += len(chunk)  # count real bytes, headers can lie
                        if total > max_bytes:
                            raise IngestError("zip exceeds the maximum uncompressed size")
                        out.write(chunk)
    except zipfile.BadZipFile as exc:
        raise IngestError("file is not a valid zip archive") from exc


def _clean(value: Any) -> Any:
    """Make an attribute value JSON-safe."""
    if isinstance(value, np.generic):
        value = value.item()
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", "replace")
    return str(value)


def _read_dataset(path: Path, result: IngestResult) -> None:
    try:
        layers = pyogrio.list_layers(str(path))
    except Exception as exc:  # noqa: BLE001
        raise IngestError(f"could not open {path.name}: {exc}") from exc

    for layer_name, _ in layers:
        try:
            meta, _index, wkb, field_data = pyogrio_raw.read(
                str(path), layer=layer_name, force_2d=True, datetime_as_string=True
            )
        except Exception as exc:  # noqa: BLE001
            raise IngestError(f"could not read layer '{layer_name}' of {path.name}: {exc}") from exc

        geoms = shapely.from_wkb(wkb) if len(wkb) else np.array([], dtype=object)

        if meta.get("crs"):
            try:
                crs = CRS.from_user_input(meta["crs"])
            except Exception as exc:  # noqa: BLE001
                raise IngestError(f"unrecognised CRS in layer '{layer_name}'") from exc
        else:
            # Typical for shapefiles missing their .prj. Only guess if it plausibly is lon/lat.
            valid = [g for g in geoms if g is not None and not g.is_empty]
            if valid:
                minx, miny, maxx, maxy = shapely.total_bounds(np.array(valid, dtype=object))
                if not (-180 <= minx and maxx <= 180 and -90 <= miny and maxy <= 90):
                    raise IngestError(
                        f"layer '{layer_name}' has no CRS and its coordinates are not lon/lat"
                    )
            crs = WGS84
            result.warnings.append(f"layer '{layer_name}' has no CRS; assumed EPSG:4326")

        names = [str(n) for n in meta["fields"]]
        for i, geom in enumerate(geoms):
            props = {names[j]: _clean(field_data[j][i]) for j in range(len(names))}
            result.features.append(RawFeature(str(layer_name), geom, props, crs))


def read_features(path: Path, kind: str, workdir: Path, max_uncompressed_bytes: int) -> IngestResult:
    """Read every feature from an uploaded file.

    ``kind`` is "shapefile" (a .zip) or "kml". ``workdir`` is scratch space for extraction.
    """
    result = IngestResult()
    if kind == "kml":
        _read_dataset(path, result)
    elif kind == "shapefile":
        extract_dir = workdir / "extracted"
        if extract_dir.exists():
            shutil.rmtree(extract_dir)
        extract_dir.mkdir(parents=True)
        safe_extract(path, extract_dir, max_uncompressed_bytes)
        shapefiles = sorted(p for p in extract_dir.rglob("*") if p.suffix.lower() == ".shp")
        if not shapefiles:
            raise IngestError("zip archive does not contain a .shp file")
        for shp in shapefiles:
            _read_dataset(shp, result)
    else:
        raise IngestError(f"unsupported file kind: {kind}")
    return result
