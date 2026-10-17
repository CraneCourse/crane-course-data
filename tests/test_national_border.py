"""Border accounting: possibly clipped search areas are unresolved, never absent or complete."""
from pyproj import Geod
from shapely.geometry import Point
from pipeline.national_border import parse_poly, search_envelope, classify, classify_all, SEARCH_METERS

POLY = """state
1
   -80.0 30.0
   -70.0 30.0
   -70.0 40.0
   -80.0 40.0
   -80.0 30.0
END
!2
   -76.0 34.0
   -75.0 34.0
   -75.0 35.0
   -76.0 35.0
   -76.0 34.0
END
END
"""


def test_envelope_contains_the_whole_geodesic_search_disc():
    geod = Geod(ellps='WGS84')
    for lon, lat in [(-100.0, 24.0), (-70.0, 49.9), (-122.4, 37.8), (-67.0, 44.9)]:
        envelope = search_envelope(lon, lat)
        for azimuth in range(0, 360, 3):
            x, y, _ = geod.fwd(lon, lat, azimuth, SEARCH_METERS)
            assert envelope.covers(Point(x, y)), (lon, lat, azimuth)


def test_poly_holes_and_classification():
    area = parse_poly(POLY)
    assert not area.covers(Point(-75.5, 34.5))
    c = lambda lon, lat: {'id': 'x', 'state': 'SC', 'center': {'lon': lon, 'lat': lat}}
    assert classify(c(-77.5, 37.5), area) == 'inside'
    assert classify(c(-79.99, 35.0), area) == 'border-unresolved'      # 1 km from the edge
    assert classify(c(-76.01, 34.5), area) == 'border-unresolved'      # 1 km outside a hole
    assert classify(c(-75.5, 34.5), area) == 'outside-extract'         # inside a hole
    assert classify(c(-81.0, 35.0), area) == 'outside-extract'


def test_every_row_keeps_an_entry_and_prior_exclusions_stay():
    rows = [{'candidate': {'id': str(i), 'state': s, 'center': {'lon': lon, 'lat': 35.0}}, 'status': st}
            for i, (s, lon, st) in enumerate([('SC', -77.0, 'geometry-withheld'), ('SC', -79.99, 'geometry-candidate'),
                                              ('AK', -150.0, 'outside-geography'), ('DC', -77.0, 'outside-extract')])]
    result, evidence = classify_all(rows, {'SC': 'south-carolina', 'DC': 'district-of-columbia'},
                                    fetcher=lambda e: (f'https://example.org/{e}.poly', POLY.encode()))
    assert result == {'0': 'inside', '1': 'border-unresolved', '2': 'not-screened', '3': 'not-screened'}
    assert set(evidence) == {'south-carolina', 'district-of-columbia'} and all(len(e['sha256']) == 64 for e in evidence.values())
