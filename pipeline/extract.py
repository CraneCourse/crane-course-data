"""Read filtered state extracts. Retain only public geometry and an allowlist of tags."""
import json
import osmium
from shapely.geometry import shape, Point, LineString
from shapely.ops import transform
from pyproj import Transformer
from pipeline.match import distance, valid_way, counts, diagnose, yardage_error, center

class GolfHandler(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.ways = []
        self.areas = []
        self.relation_greens = []
        self.errors = []
        self.factory = osmium.geom.GeoJSONFactory()

    def way(self, w):
        if not w.tags.get('golf'):
            return
        try:
            points = [(n.lon, n.lat) for n in w.nodes]
            if len(points) >= 2:
                self.ways.append({'id': 'way/' + str(w.id), 'tags': {t.k: t.v for t in w.tags if t.k in {'golf', 'ref', 'name', 'par'}}, 'points': points})
        except (osmium.InvalidLocationError, RuntimeError) as exc:
            self.errors.append({'id': 'way/' + str(w.id), 'error': str(exc)})

    def area(self, a):
        boundary = a.tags.get('leisure') == 'golf_course'
        relation_green = not a.from_way() and a.tags.get('golf') == 'green'
        if not boundary and not relation_green:
            return
        try:
            geometry = shape(json.loads(self.factory.create_multipolygon(a)))
            if not geometry.is_valid or geometry.is_empty:
                raise ValueError('Invalid course/green polygon')
            row = {'id': ('way/' if a.from_way() else 'relation/') + str(a.orig_id()), 'name': a.tags.get('name', ''), 'geometry': geometry}
            (self.areas if boundary else self.relation_greens).append(row)
        except (RuntimeError, ValueError) as exc:
            self.errors.append({'id': 'area/' + str(a.id), 'error': str(exc)})

def association(areas, origin):
    # Local metric projection is only for boundaries. Matcher distances use WGS84.
    project = Transformer.from_crs('EPSG:4326', f'+proj=aeqd +lat_0={origin[1]} +lon_0={origin[0]} +datum=WGS84 +units=m', always_xy=True).transform
    point = Point(0, 0)
    nearby = []
    for a in areas:
        bounds = a['geometry'].bounds
        if not (bounds[0] - .02 <= origin[0] <= bounds[2] + .02 and bounds[1] - .02 <= origin[1] <= bounds[3] + .02):
            continue
        projected = transform(project, a['geometry'])
        d = projected.distance(point)
        if d <= 500:
            nearby.append((d, a, projected))
    containing = [x for x in nearby if x[0] == 0]
    choices = containing or nearby
    metadata = [{'id': a['id'], 'name': a['name'], 'distanceMeters': round(d, 1)} for d, a, _ in nearby]
    if len(choices) != 1:
        return None, project, {'method': 'ambiguous' if choices else 'none', 'confidence': 'unresolved', 'candidates': metadata}
    d, a, geometry = choices[0]
    return geometry, project, {'method': 'contains' if d == 0 else 'near', 'confidence': 'spatial-only-unverified', 'selectedID': a['id'], 'candidates': metadata}

def measure(candidate, handler, scorecard):
    origin = (candidate['center']['lon'], candidate['center']['lat'])
    nearby = [w for w in handler.ways if valid_way(w, origin)]
    boundary, project, link = association(handler.areas, origin)
    # No boundary fallback is ever called a linked course. Radius data is separately labelled.
    if boundary is not None:
        expanded = boundary.buffer(100)
        ways = [w for w in nearby if expanded.covers(transform(project, LineString(w['points'])))]
    else:
        ways = nearby
    cards = scorecard.get('response', {}).get('holes', [])
    row = {'candidate': candidate, 'scorecardHoleCount': len(cards), 'scorecardSource': {k: v for k, v in scorecard.items() if k != 'response'},
           'association': link, 'countScope': 'boundary-plus-100m' if boundary is not None else 'unassociated-3500m-radius',
           'counts': counts(ways), 'radiusCounts': counts(nearby),
           'relationGreensInRadius': sum(distance((g['geometry'].centroid.x, g['geometry'].centroid.y), origin) < 3500 for g in handler.relation_greens)}
    if 'response' not in scorecard:
        row.update(category='service-error', ruleResult={'pass': False, 'rule': 'not-tested', 'reason': scorecard.get('error', 'Scorecard not fetched')})
    else:
        row['ruleResult'] = diagnose(cards, ways, origin)
        c = row['counts']
        if yardage_error(cards):
            category = 'scorecard-problem'
        elif boundary is None:
            category = 'ambiguous-course-area' if link['method'] == 'ambiguous' else 'no-osm-course-area'
        elif row['ruleResult']['pass']:
            category = 'provisional-pass'
        elif not c['holeLines'] and not c['greens'] and not c['teeAreas']:
            category = 'nothing-mapped'
        elif not c['holeLines'] and c['greens'] and c['teeAreas']:
            category = 'greens-and-tees-no-lines'
        elif row['ruleResult']['rule'] == 'numbering':
            category = 'partial-or-ambiguous-lines'
        else:
            category = 'rule-rejection-data-present'
        row['category'] = category
    c = row['counts']
    row['phase3InventoryCandidate'] = (boundary is not None and not yardage_error(cards)
        and row['ruleResult']['rule'] == 'numbering' and c['greens'] >= len(cards) and c['teeAreas'] >= len(cards))
    row['phase3Caveat'] = 'Counts only: tee polygons can belong to the same hole; no numbering inferred or validated.'
    # Report displacement only; never change the center used by matching.
    shifts = []
    from shapely.geometry import Polygon
    for w in ways:
        if w['tags'].get('golf') != 'green' or len(w['points']) < 4:
            continue
        polygon = Polygon(w['points'])
        if not polygon.is_valid or polygon.area == 0:
            continue
        projected = transform(project, polygon)
        old = transform(project, Point(center(w)))
        shifts.append({'id': w['id'], 'meters': round(old.distance(projected.centroid), 3)})
    row['centroidDisplacement'] = shifts
    return row
