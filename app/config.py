"""Runtime configuration, read from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("GEO_DATA_DIR", "./data")))
    max_upload_mb: int = int(os.getenv("GEO_MAX_UPLOAD_MB", "50"))
    # Guards against zip bombs: cap on the total *uncompressed* size of an archive.
    max_uncompressed_mb: int = int(os.getenv("GEO_MAX_UNCOMPRESSED_MB", "300"))

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "geo.sqlite3"
