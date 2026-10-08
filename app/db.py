"""Tiny SQLite persistence layer (stdlib only).

A connection is opened per operation, so the layer is safe to use from FastAPI's
threadpool. Swapping to Postgres/PostGIS would only touch this module.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    id            TEXT PRIMARY KEY,
    filename      TEXT NOT NULL,
    file_type     TEXT NOT NULL,
    status        TEXT NOT NULL,
    error         TEXT,
    feature_count INTEGER NOT NULL DEFAULT 0,
    crs           TEXT,
    warnings      TEXT NOT NULL DEFAULT '[]',
    created_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS features (
    file_id             TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    idx                 INTEGER NOT NULL,
    layer               TEXT,
    geometry_type       TEXT,
    crs                 TEXT,
    geometry            TEXT,
    properties          TEXT NOT NULL,
    measurement_status  TEXT NOT NULL,
    area_m2             REAL,
    perimeter_m         REAL,
    length_m            REAL,
    measurement_crs     TEXT,
    measurement_method  TEXT,
    measurement_message TEXT,
    PRIMARY KEY (file_id, idx)
);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            with conn:  # commit / rollback
                yield conn
        finally:
            conn.close()

    # -- files -------------------------------------------------------------
    def create_file(self, file_id: str, filename: str, file_type: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO files (id, filename, file_type, status, created_at) VALUES (?,?,?,?,?)",
                (file_id, filename, file_type, "PROCESSING", datetime.now(timezone.utc).isoformat()),
            )

    def complete_file(self, file_id: str, crs: str | None, warnings: list[str],
                      features: list[dict[str, Any]]) -> None:
        """Persist all features and flip the file to COMPLETED in a single transaction."""
        rows = [
            (
                file_id, f["idx"], f["layer"], f["geometry_type"], f["crs"],
                json.dumps(f["geometry"]) if f["geometry"] is not None else None,
                json.dumps(f["properties"]),
                f["measurement_status"], f["area_m2"], f["perimeter_m"], f["length_m"],
                f["measurement_crs"], f["measurement_method"], f["measurement_message"],
            )
            for f in features
        ]
        with self._connect() as conn:
            conn.executemany("INSERT INTO features VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            conn.execute(
                "UPDATE files SET status='COMPLETED', feature_count=?, crs=?, warnings=? WHERE id=?",
                (len(rows), crs, json.dumps(warnings), file_id),
            )

    def fail_file(self, file_id: str, error: str) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE files SET status='FAILED', error=? WHERE id=?", (error, file_id))

    def get_file(self, file_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM files WHERE id=?", (file_id,)).fetchone()
        if row is None:
            return None
        data = dict(row)
        data["warnings"] = json.loads(data["warnings"])
        return data

    # -- features ----------------------------------------------------------
    def get_features(self, file_id: str, limit: int, offset: int) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM features WHERE file_id=? ORDER BY idx LIMIT ? OFFSET ?",
                (file_id, limit, offset),
            ).fetchall()
        out = []
        for row in rows:
            d = dict(row)
            d["geometry"] = json.loads(d["geometry"]) if d["geometry"] else None
            d["properties"] = json.loads(d["properties"])
            out.append(d)
        return out

    def measurement_summary(self, file_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                """SELECT COUNT(*) AS feature_count,
                          COALESCE(SUM(measurement_status = 'MEASURED'), 0) AS measured_count,
                          COALESCE(SUM(measurement_status IN ('UNSUPPORTED','ERROR','NO_GEOMETRY')), 0)
                              AS skipped_count,
                          COALESCE(SUM(area_m2), 0.0) AS total_area_m2,
                          COALESCE(SUM(length_m), 0.0) AS total_length_m
                   FROM features WHERE file_id=?""",
                (file_id,),
            ).fetchone()
        return dict(row)
