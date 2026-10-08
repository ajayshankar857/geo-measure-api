import io
import zipfile

import pytest
from pytest import approx

from tests.conftest import SAMPLE_KML


def upload(client, name, data, ctype="application/octet-stream"):
    return client.post("/api/files/", files={"file": (name, data, ctype)})


def test_kml_end_to_end(client):
    r = upload(client, "survey.kml", SAMPLE_KML.read_bytes())
    assert r.status_code == 201
    info = r.json()
    assert info["status"] == "COMPLETED" and info["crs"] == "EPSG:4326"
    assert info["feature_count"] == 4 and info["filename"] == "survey.kml"
    fid = info["id"]

    assert client.get(f"/api/files/{fid}/").json()["feature_count"] == 4

    feats = client.get(f"/api/files/{fid}/features/").json()["features"]
    assert sorted(f["properties"]["Name"] for f in feats) == ["Mixed", "Plot A", "Road 1", "Well"]
    assert feats[0]["geometry"]["type"] == "Polygon" and feats[0]["crs"] == "EPSG:4326"

    body = client.get(f"/api/files/{fid}/measurements/").json()
    by_type = {m["geometry_type"]: m for m in body["measurements"]}
    assert by_type["Polygon"]["area_m2"] == approx(12_090, rel=0.02)
    assert by_type["LineString"]["length_m"] == approx(1106, rel=0.01)
    assert by_type["Point"]["status"] == "NOT_APPLICABLE"
    assert by_type["GeometryCollection"]["status"] == "UNSUPPORTED"   # handled, no crash
    assert body["summary"]["measured_count"] == 2
    assert body["summary"]["skipped_count"] == 1


def test_shapefile_zip(client, shapefile_zip):
    r = upload(client, "parcels.zip", shapefile_zip.read_bytes(), "application/zip")
    assert r.status_code == 201, r.text
    fid = r.json()["id"]
    m = client.get(f"/api/files/{fid}/measurements/").json()["measurements"][0]
    assert m["status"] == "MEASURED" and m["measurement_crs"] == "EPSG:32644"
    assert m["area_m2"] == approx(12_090, rel=0.02)


def test_pagination(client):
    fid = upload(client, "survey.kml", SAMPLE_KML.read_bytes()).json()["id"]
    page = client.get(f"/api/files/{fid}/measurements/?limit=2&offset=3").json()
    assert [m["feature_index"] for m in page["measurements"]] == [3]
    assert page["summary"]["feature_count"] == 4


def test_rejects_unsupported_extension(client):
    assert upload(client, "notes.txt", b"hello").status_code == 400


def test_invalid_zip_is_422_and_recorded_as_failed(client):
    r = upload(client, "bad.zip", b"definitely not a zip")
    assert r.status_code == 422
    detail = r.json()["detail"]
    info = client.get(f"/api/files/{detail['id']}/").json()
    assert info["status"] == "FAILED" and info["error"]
    assert client.get(f"/api/files/{detail['id']}/measurements/").status_code == 409


def test_zip_without_shp(client):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("readme.txt", "no shapefile here")
    assert upload(client, "empty.zip", buf.getvalue()).status_code == 422


def test_zip_slip_rejected(client):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../evil.shp", "x")
    assert upload(client, "evil.zip", buf.getvalue()).status_code == 422


def test_malformed_kml_is_422(client):
    assert upload(client, "broken.kml", b"<kml><Placemark>").status_code == 422


def test_unknown_id_404(client):
    assert client.get("/api/files/nope/").status_code == 404
    assert client.get("/api/files/nope/measurements/").status_code == 404
