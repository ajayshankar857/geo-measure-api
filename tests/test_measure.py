import pytest
from pyproj import CRS
from shapely.geometry import GeometryCollection, LineString, MultiPolygon, Point, Polygon, box

from app.services.measure import (ERROR, MEASURED, NO_GEOMETRY, NOT_APPLICABLE, UNSUPPORTED,
                                  measure_feature, utm_epsg)

WGS84 = CRS.from_epsg(4326)


@pytest.mark.parametrize("lon,lat,expected", [
    (78.08, 10.95, 32644), (-122.4, 37.8, 32610), (151.2, -33.9, 32756),
    (180.0, 0.0, 32660), (0, 89, 32661), (0, -85, 32761),
])
def test_utm_epsg(lon, lat, expected):
    assert utm_epsg(lon, lat) == expected


def test_polygon_area_not_in_degrees():
    m = measure_feature(box(78.080, 10.950, 78.081, 10.951), WGS84)
    assert m.status == MEASURED and m.method == "utm" and m.measurement_crs == "EPSG:32644"
    assert m.area_m2 == pytest.approx(12_090, rel=0.02)   # square metres, not 1e-6 deg^2
    assert m.perimeter_m == pytest.approx(440, rel=0.02)


def test_line_length_one_hundredth_degree_of_latitude():
    m = measure_feature(LineString([(78.08, 10.95), (78.08, 10.96)]), WGS84)
    assert m.length_m == pytest.approx(1106, rel=0.01)


def test_multipolygon_sums_parts():
    a, b = box(78.080, 10.950, 78.081, 10.951), box(78.090, 10.950, 78.091, 10.951)
    single = measure_feature(a, WGS84).area_m2
    assert measure_feature(MultiPolygon([a, b]), WGS84).area_m2 == pytest.approx(2 * single, rel=0.01)


def test_native_projected_crs_with_feet():
    # 100 ft x 100 ft square in a US-foot projected CRS (NAD83 / California zone 5, ftUS).
    crs = CRS.from_epsg(2229)
    m = measure_feature(box(6_000_000, 2_000_000, 6_000_100, 2_000_100), crs)
    assert m.method == "native_projected"
    assert m.area_m2 == pytest.approx(10_000 * 0.3048006096**2, rel=1e-4)


def test_web_mercator_is_reprojected_not_measured_natively():
    # 1000 "metres" of Web Mercator at 60N is really ~500 m on the ground.
    crs = CRS.from_epsg(3857)
    y = 8_399_737.9  # ~60N
    m = measure_feature(LineString([(0, y), (1000, y)]), crs)
    assert m.method == "utm"
    assert m.length_m == pytest.approx(500, rel=0.02)


def test_points_and_unsupported_and_empty_are_graceful():
    assert measure_feature(Point(78, 10), WGS84).status == NOT_APPLICABLE
    gc = GeometryCollection([Point(0, 0), LineString([(0, 0), (1, 1)])])
    assert measure_feature(gc, WGS84).status == UNSUPPORTED
    assert measure_feature(None, WGS84).status == NO_GEOMETRY
    assert measure_feature(Polygon(), WGS84).status == NO_GEOMETRY


def test_failure_is_reported_not_raised():
    m = measure_feature(LineString([(0, 0), (1, 1)]), CRS.from_user_input("LOCAL_CS[\"x\"]"))
    assert m.status == ERROR
