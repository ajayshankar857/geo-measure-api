"""Measurement logic: CRS selection + area / length computation.

Rule of thumb: *never* measure in degrees.

* Geographic CRS (e.g. EPSG:4326, NAD83 geographic)  -> reproject to the UTM zone
  that contains the feature's centroid (UPS near the poles).
* Projected CRS with sensible distances (UTM, State Plane, national grids)
  -> measure natively, converting the axis unit (feet, km, ...) to metres.
* Mercator-family CRS (incl. Web Mercator EPSG:3857), whose distortion grows
  quickly with latitude -> treated like a geographic CRS: reproject to UTM.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache

from pyproj import CRS, Transformer
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as shapely_transform

# Measurement statuses
MEASURED = "MEASURED"
NOT_APPLICABLE = "NOT_APPLICABLE"  # e.g. points: nothing to measure
UNSUPPORTED = "UNSUPPORTED"        # e.g. GeometryCollection
NO_GEOMETRY = "NO_GEOMETRY"        # null / empty geometry
ERROR = "ERROR"                    # measurement attempted but failed

AREA_TYPES = {"Polygon", "MultiPolygon"}
LENGTH_TYPES = {"LineString", "MultiLineString"}
POINT_TYPES = {"Point", "MultiPoint"}


class MeasurementError(Exception):
    pass


@dataclass(frozen=True)
class Measurement:
    status: str
    area_m2: float | None = None
    perimeter_m: float | None = None
    length_m: float | None = None
    measurement_crs: str | None = None
    method: str | None = None  # "utm" | "native_projected"
    message: str | None = None


def crs_label(crs: CRS) -> str:
    authority = crs.to_authority()
    return f"{authority[0]}:{authority[1]}" if authority else crs.name


def utm_epsg(lon: float, lat: float) -> int:
    """EPSG code of the WGS84 UTM zone containing (lon, lat); UPS beyond 84N / 80S."""
    if lat > 84:
        return 32661  # WGS 84 / UPS North
    if lat < -80:
        return 32761  # WGS 84 / UPS South
    zone = min(max(int((lon + 180) // 6) + 1, 1), 60)
    return (32600 if lat >= 0 else 32700) + zone
    # Note: the Norway / Svalbard zone exceptions are intentionally ignored.


@lru_cache(maxsize=256)
def _transformer(src: CRS, dst: CRS) -> Transformer:
    return Transformer.from_crs(src, dst, always_xy=True)


def _is_mercator(crs: CRS) -> bool:
    op = crs.coordinate_operation
    return bool(op and "mercator" in (op.method_name or "").lower())


def _lonlat_centroid(geom: BaseGeometry, src: CRS) -> tuple[float, float]:
    c = geom.centroid
    if src.is_geographic:
        lon, lat = c.x, c.y
    else:
        lon, lat = _transformer(src, CRS.from_epsg(4326)).transform(c.x, c.y)
    if not (math.isfinite(lon) and math.isfinite(lat)) or not -90 <= lat <= 90:
        raise MeasurementError("could not determine a valid lon/lat location for the feature")
    return lon, lat


def choose_measurement_crs(geom: BaseGeometry, src: CRS) -> tuple[CRS, str, float]:
    """Return (crs to measure in, method name, metres-per-unit of that CRS)."""
    if src.is_projected and not _is_mercator(src):
        factor = src.axis_info[0].unit_conversion_factor or 1.0
        return src, "native_projected", factor
    if not (src.is_geographic or src.is_projected):
        raise MeasurementError(f"unsupported CRS type: {src.name}")
    lon, lat = _lonlat_centroid(geom, src)
    return CRS.from_epsg(utm_epsg(lon, lat)), "utm", 1.0


def measure_feature(geom: BaseGeometry | None, src: CRS) -> Measurement:
    """Measure a single geometry. Never raises: failures are reported in the result."""
    if geom is None or geom.is_empty:
        return Measurement(NO_GEOMETRY, message="feature has no geometry")

    gtype = geom.geom_type
    if gtype in POINT_TYPES:
        return Measurement(NOT_APPLICABLE, message="points have no area or length")
    if gtype not in AREA_TYPES | LENGTH_TYPES:
        return Measurement(UNSUPPORTED, message=f"measurement not supported for {gtype}")

    try:
        target, method, factor = choose_measurement_crs(geom, src)
        projected = geom if target == src else shapely_transform(
            _transformer(src, target).transform, geom
        )
        notes = None if geom.is_valid else "geometry is invalid; result may be unreliable"
        label = crs_label(target)

        if gtype in AREA_TYPES:
            area, perimeter = projected.area * factor**2, projected.length * factor
            if not (math.isfinite(area) and math.isfinite(perimeter)):
                raise MeasurementError("projection produced non-finite coordinates")
            return Measurement(MEASURED, area_m2=area, perimeter_m=perimeter,
                               measurement_crs=label, method=method, message=notes)

        length = projected.length * factor
        if not math.isfinite(length):
            raise MeasurementError("projection produced non-finite coordinates")
        return Measurement(MEASURED, length_m=length, measurement_crs=label,
                           method=method, message=notes)
    except Exception as exc:  # noqa: BLE001 - one bad feature must not sink the file
        return Measurement(ERROR, message=f"measurement failed: {exc}")
