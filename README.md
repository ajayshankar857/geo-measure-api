# Geospatial File Measurement API

A FastAPI service that accepts a **zipped Shapefile** or a **KML**, extracts every feature
(ID, geometry type, geometry, CRS, properties) and returns **area** (polygons) and
**length** (lines), always computed in a projected CRS, never in degrees.

## Setup

Requires Python 3.10+. `shapely`, `pyproj` and `pyogrio` ship wheels that bundle GEOS, PROJ and
GDAL, so no system GDAL install is needed.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

uvicorn app.main:app --reload          # http://127.0.0.1:8000  (interactive docs at /docs)
pytest                                 # run the test-suite
```

Docker alternative:

```bash
docker build -t geo-measure-api . && docker run -p 8000:8000 -v geo-data:/srv/data geo-measure-api
```

| Env var | Default | Meaning |
|---|---|---|
| `GEO_DATA_DIR` | `./data` | Where uploads and the SQLite DB live |
| `GEO_MAX_UPLOAD_MB` | `50` | Max upload size (HTTP 413 beyond it) |
| `GEO_MAX_UNCOMPRESSED_MB` | `300` | Max total size of an extracted zip (zip-bomb guard) |

## API

All list endpoints are paginated with `?limit=` (1-1000, default 100) and `?offset=`.

### `POST /api/files/` - upload and process
Multipart field `file`: a `.zip` (containing a `.shp` + sidecars) or a `.kml`.

```bash
curl -F "file=@sample_data/survey.kml" http://127.0.0.1:8000/api/files/
```
```json
{ "id": "3f1c0d9e5a7b4c2e8b6a1d2f4e5c7a90", "filename": "survey.kml", "feature_count": 4,
  "crs": "EPSG:4326", "status": "COMPLETED", "error": null, "warnings": [],
  "created_at": "2026-10-08T10:15:00+00:00" }
```
Errors: `400` unsupported extension, `413` too large, `422` unreadable/invalid file
(body contains the `id` of the stored `FAILED` record), `500` unexpected failure.

### `GET /api/files/{id}/` - file information
Same shape as above. `404` if unknown.

### `GET /api/files/{id}/features/` - extracted features
```json
{ "file_id": "3f1c...", "total": 4, "limit": 100, "offset": 0,
  "features": [ { "index": 0, "layer": "Document", "geometry_type": "Polygon", "crs": "EPSG:4326",
                  "geometry": { "type": "Polygon", "coordinates": [[[78.08, 10.95], "..."]] },
                  "properties": { "Name": "Plot A", "Description": "Small test parcel (~0.12 ha)" } } ] }
```
Geometry is GeoJSON in the file's **own** CRS (the `crs` field says which).

### `GET /api/files/{id}/measurements/` - measurements
```json
{ "file_id": "3f1c...",
  "summary": { "feature_count": 4, "measured_count": 2, "skipped_count": 1,
               "total_area_m2": 12090.4, "total_length_m": 1105.9 },
  "limit": 100, "offset": 0,
  "measurements": [
    { "feature_index": 0, "layer": "Document", "geometry_type": "Polygon", "status": "MEASURED",
      "area_m2": 12090.4, "perimeter_m": 439.8, "length_m": null,
      "source_crs": "EPSG:4326", "measurement_crs": "EPSG:32644", "method": "utm", "message": null },
    { "feature_index": 1, "geometry_type": "LineString", "status": "MEASURED", "length_m": 1105.9,
      "measurement_crs": "EPSG:32644", "method": "utm", "...": "..." },
    { "feature_index": 2, "geometry_type": "Point", "status": "NOT_APPLICABLE",
      "message": "points have no area or length", "...": "..." },
    { "feature_index": 3, "geometry_type": "GeometryCollection", "status": "UNSUPPORTED",
      "message": "measurement not supported for GeometryCollection", "...": "..." } ] }
```
(Numbers are illustrative.) Per-feature `status`: `MEASURED`, `NOT_APPLICABLE` (points),
`UNSUPPORTED` (e.g. GeometryCollection), `NO_GEOMETRY`, `ERROR`. A bad feature never fails the request.
`409` if the file is not `COMPLETED`.

## Architecture

```
app/
  main.py              app factory, wiring
  config.py            env-based settings
  api/routes.py        HTTP layer only: validation, status codes, serialisation
  schemas.py           pydantic response models
  db.py                SQLite persistence (files + features tables)
  services/
    ingest.py          safe zip extraction, GDAL reading, CRS detection
    measure.py         CRS selection + area/length maths (pure, no I/O)
    processor.py       orchestration: ingest -> measure -> persist
```

**File-processing flow:** upload is streamed to disk (size-capped) -> a `files` row is created
(`PROCESSING`) -> zip is extracted defensively into a temp dir (zip-slip and zip-bomb checks) ->
every layer of every `.shp` / the KML is read through GDAL (`pyogrio`) -> each feature is measured ->
all feature rows are written with the status flip to `COMPLETED` in **one transaction**
(`FAILED` + error message on failure).

**Measurement flow:** per feature, `measure_feature(geom, source_crs)` -> choose measurement CRS ->
transform (cached `pyproj.Transformer`) -> Shapely `.area` / `.length` -> convert units to metres.
Polygons/MultiPolygons give area and perimeter, LineStrings/MultiLineStrings give length, points are
`NOT_APPLICABLE`, anything else `UNSUPPORTED`. Exceptions are caught per feature and reported as `ERROR`.

**CRS handling:**
1. Geographic CRS (EPSG:4326, NAD83...) -> reproject to the **UTM zone of the feature's centroid**
   (UPS beyond 84°N / 80°S). Zone is chosen per feature, so a file spanning several zones stays accurate.
2. Projected CRS (UTM, State Plane, national grids) -> measure **natively**, scaling the axis unit
   (e.g. US survey feet) to metres.
3. Mercator-family CRS (incl. EPSG:3857) -> distances are badly distorted away from the equator, so
   these are reprojected to UTM too.
4. KML is always EPSG:4326. A shapefile with no `.prj` is assumed 4326 only if its coordinates fit
   within lon/lat bounds (with a warning); otherwise it is rejected.

The CRS actually used is returned as `measurement_crs` for transparency.

## Design Decisions

| Decision | Why / alternatives considered |
|---|---|
| **FastAPI** over Django+DRF | Small API surface, typed schemas, free OpenAPI docs. Django would earn its keep once auth/admin/ORM migrations are needed. |
| **pyogrio (GDAL) + Shapely + pyproj**, no GeoPandas | Same I/O engine as GeoPandas/Fiona without pandas; fewer dependencies and one reader for both formats. Hand-parsing KML with `xml.etree` was considered, but GDAL also covers folders, ExtendedData and malformed edge cases. |
| **Per-feature UTM** vs one CRS per file | Higher accuracy for multi-zone files and no need to scan the whole file first. Trade-off: slightly more transforms (mitigated by transformer cache). |
| **UTM vs equal-area / geodesic** | UTM is ~0.04% accurate for area within its zone and easy to explain. Geodesic (`pyproj.Geod`) is exact on the ellipsoid and a good future cross-check; a per-feature Lambert azimuthal equal-area projection would be the best choice for very large polygons. |
| **Synchronous processing** | Gives the `COMPLETED` status immediately and keeps the system simple. The `status` field and `process_file()` split already allow a drop-in move to a task queue. |
| **SQLite, stdlib `sqlite3`** | Zero setup. Isolated in `db.py`; measurements are real columns so totals are one SQL query. Geometry is stored as GeoJSON text (PostGIS would be the upgrade). |
| **Measurement failures are per-feature** | One bad geometry must not lose a 10,000-feature upload. |
| **Hardened upload** | Streaming size cap, zip-slip check, uncompressed-size cap measured on actual bytes, filenames never used as paths. |

## Learnings

- Latitude/longitude are angles: 0.001° of longitude is ~111 m at the equator but ~55 m at 60°N, so
  the UTM step is not optional. Axis order (lat/lon vs lon/lat) is a classic trap; `always_xy=True` removes it.
- A "projected" CRS is not automatically safe: Web Mercator reports metres that can be off by 2x at
  high latitude. Projected CRS units are not always metres either (feet, ftUS).
- KML `MultiGeometry` becomes a `GeometryCollection`, and real-world files often have missing `.prj`
  files, mixed layers and invalid polygons; the API has to describe these instead of crashing.
- Treating zip uploads as untrusted input (zip-slip, zip bombs) is part of the feature, not an extra.

## Future Scope

- Background processing (Celery/RQ) with progress reporting, and streaming ingestion for very large files.
- PostGIS storage, spatial queries, and an output GeoJSON endpoint reprojected to EPSG:4326.
- Measure `GeometryCollection` parts, add geodesic cross-checks, configurable CRS strategy per request, perimeter/centroid/bbox.
- More formats: KMZ, GeoJSON, GeoPackage, and a Shapefile-in-zip with multiple layers selectable by name.
- Geometry validation/repair report (`make_valid`), auth + rate limiting, file deletion/TTL cleanup, metrics, CI pipeline.
