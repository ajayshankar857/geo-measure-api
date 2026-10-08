"""FastAPI application factory."""
from __future__ import annotations

import logging

from fastapi import FastAPI

from app.api.routes import router
from app.config import Settings
from app.db import Database


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    settings.upload_dir.mkdir(parents=True, exist_ok=True)

    app = FastAPI(
        title="Geospatial File Measurement API",
        version="1.0.0",
        description="Upload a zipped Shapefile or a KML and get per-feature area / length "
                    "measurements, computed in an appropriate projected CRS.",
    )
    app.state.settings = settings
    app.state.db = Database(settings.db_path)
    app.include_router(router)

    @app.get("/health", tags=["meta"])
    def health() -> dict:
        return {"status": "ok"}

    return app


logging.basicConfig(level=logging.INFO)
app = create_app()
