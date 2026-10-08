import os
import tempfile
import zipfile
from pathlib import Path

os.environ.setdefault("GEO_DATA_DIR", tempfile.mkdtemp(prefix="geo-test-import-"))

import numpy as np  # noqa: E402
import pytest  # noqa: E402
import shapely  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from pyogrio import raw as pyogrio_raw  # noqa: E402
from shapely.geometry import box  # noqa: E402

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402

SAMPLE_KML = Path(__file__).parent.parent / "sample_data" / "survey.kml"


@pytest.fixture()
def client(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path / "data"))) as c:
        yield c


def make_shapefile_zip(tmp_path: Path, geom, crs: str, geometry_type: str, name="layer") -> Path:
    """Write a one-feature shapefile with GDAL and zip it."""
    shp_dir = tmp_path / "shp"
    shp_dir.mkdir(exist_ok=True)
    shp = shp_dir / f"{name}.shp"
    pyogrio_raw.write(
        str(shp),
        geometry=shapely.to_wkb(np.array([geom], dtype=object)),
        field_data=[np.array(["feature-1"], dtype=object)],
        fields=["name"],
        crs=crs,
        geometry_type=geometry_type,
        driver="ESRI Shapefile",
    )
    zip_path = tmp_path / f"{name}.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for f in shp_dir.iterdir():
            zf.write(f, f.name)
    return zip_path


@pytest.fixture()
def shapefile_zip(tmp_path):
    # ~110.6 m x ~109.3 m box near 10.95N 78.08E, EPSG:4326
    return make_shapefile_zip(tmp_path, box(78.080, 10.950, 78.081, 10.951), "EPSG:4326", "Polygon")
