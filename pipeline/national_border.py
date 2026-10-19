"""Border accounting from official Geofabrik extract extents; no extraction is repeated.

Each directory candidate was screened only inside the extract for its directory state.
Its 3.5 km search area (match.valid_way) can be clipped when it reaches the extract's
edge, so an incomplete or missing layout there is not evidence of absent mapping, and
a complete-looking layout there cannot be trusted as complete.

The extent is each extract's official `.poly`, fetched and hashed on the runner. The
search area is tested through a conservative latitude/longitude envelope that contains
the whole geodesic disc. Classifications:
  inside            envelope fully covered: the existing survey result stands
  border-unresolved center inside, envelope not covered: possibly clipped
  outside-extract   center outside the assigned extract: identity/location unresolved
"""
import hashlib
import math
import time
import urllib.error
import urllib.request
from pyproj import Geod
from shapely.geometry import Point, Polygon, MultiPolygon, box
from pipeline.common import cloud_only, read, write, now

SEARCH_METERS = 3500          # pipeline.match.valid_way
MARGIN = 1.02                 # extra 2% beyond the geodesic extent
GEOD = Geod(ellps='WGS84')
BASE = 'https://download.geofabrik.de/north-america/us/'
REASONS = {
    'border-unresolved': 'Search area reaches the assigned extract edge and may be clipped; not evidence of absent or complete mapping',
    'outside-extract': 'Directory center outside the assigned extract extent; identity/location unresolved',
}


def parse_poly(text):
    """Osmosis .poly: name, then sections of 'lon lat' lines ending in END; '!' marks holes."""
    lines = iter(text.splitlines()[1:]); shells = []; holes = []
    for line in lines:
        name = line.strip()
        if name == 'END':
            break
        if not name:
            continue
        points = []
        for point in lines:
            if point.strip() == 'END':
                break
            x, y = map(float, point.split()); points.append((x, y))
        (holes if name.startswith('!') else shells).append(points)
    assert shells, 'No extract polygon'
    area = MultiPolygon([Polygon(s, [h for h in holes if Polygon(s).covers(Polygon(h))]) for s in shells])
    assert area.is_valid, 'Invalid extract polygon'
    return area


def search_envelope(lon, lat, meters=SEARCH_METERS):
    """A lon/lat box that contains every point within `meters` (geodesic) of the center."""
    north = GEOD.fwd(lon, lat, 0, meters * MARGIN)[1]
    south = GEOD.fwd(lon, lat, 180, meters * MARGIN)[1]
    # Longitude span per meter is largest at the poleward edge of the disc.
    edge = max(abs(north), abs(south))
    meters_per_degree = GEOD.inv(lon, edge, lon + 1, edge)[2]
    dlon = meters * MARGIN / meters_per_degree
    return box(lon - dlon, south, lon + dlon, north)


def classify(candidate, area):
    c = candidate['center']; lon, lat = c['lon'], c['lat']
    if not area.covers(Point(lon, lat)):
        return 'outside-extract'
    return 'inside' if area.covers(search_envelope(lon, lat)) else 'border-unresolved'


BACKOFF_SECONDS = (5, 15, 45)   # pauses between the 4 attempts


def fetch(extract, opener=urllib.request.urlopen, pause=time.sleep):
    """Transient failures (timeouts, resets, 429 and 5xx) are retried with backoff; other
    HTTP errors such as 404 fail at once. The response is still read and hashed whole."""
    url = BASE + extract + '.poly'
    request = urllib.request.Request(url, headers={'User-Agent': 'Crane-Course-Data/1.0'})
    for attempt in range(len(BACKOFF_SECONDS) + 1):
        try:
            with opener(request, timeout=45) as response:
                body = response.read(5_000_001)
            break
        except urllib.error.HTTPError as error:
            if error.code != 429 and error.code < 500 or attempt == len(BACKOFF_SECONDS):
                raise
        except OSError:   # URLError, TimeoutError, connection resets
            if attempt == len(BACKOFF_SECONDS):
                raise
        print(f'Retrying {url} after attempt {attempt + 1}', flush=True)
        pause(BACKOFF_SECONDS[attempt])
    assert len(body) <= 5_000_000
    return url, body


def classify_all(rows, extracts_by_state, fetcher=fetch):
    """Every row keeps an entry. Rows outside geography/extract already are left as-is."""
    areas = {}; evidence = {}; result = {}
    for extract in sorted(set(extracts_by_state.values())):
        url, body = fetcher(extract)
        areas[extract] = parse_poly(body.decode('utf-8'))
        evidence[extract] = {'url': url, 'sha256': hashlib.sha256(body).hexdigest(), 'fetchedAt': now()}
    for row in rows:
        c = row['candidate']
        if row['status'] in ('outside-geography', 'outside-extract') or c['state'] not in extracts_by_state:
            result[c['id']] = 'not-screened'
        else:
            result[c['id']] = classify(c, areas[extracts_by_state[c['state']]])
    return result, evidence


def main():
    import os
    from pathlib import Path
    from pipeline.national import EXTRACTS
    cloud_only()
    report = read(f'reports/national-geometry-{os.environ["GEOMETRY_RUN"]}.json')
    assert report['complete'] and len(report['rows']) == report['candidateCount'] == 16776
    rows, evidence = classify_all(report['rows'], EXTRACTS)
    assert set(rows) == {r['candidate']['id'] for r in report['rows']}
    write(Path('work/national-validation/border.json'), {'builtAt': now(), 'searchMeters': SEARCH_METERS, 'margin': MARGIN,
          'extracts': evidence, 'rows': rows, 'reasons': REASONS})
    from collections import Counter
    print(Counter(rows.values()), flush=True)


if __name__ == '__main__':
    main()
