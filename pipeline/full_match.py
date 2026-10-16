"""Complete export port; the frozen Swift kernel independently verifies every match."""
import math
from pipeline.match import diagnose, valid_way, number, center, distance, usable_yards

def point(p):
    return {'lat': p[1], 'lon': p[0]}

def geometry(ways):
    return {'elements': [{'id': int(w['id'].split('/')[1]), 'tags': w['tags'],
                          'geometry': list(map(point, w['points']))} for w in ways]}

def match(cards, ways, origin):
    result = diagnose(cards, ways, origin)
    if not result['pass']:
        return result
    ways = [w for w in ways if valid_way(w, origin)]
    paths = [w for w in ways if w['tags'].get('golf') == 'hole' and number(w) is not None]
    by_id = {w['id']: w for w in ways}
    features = {}
    for w in ways:
        if w['tags'].get('golf') == 'hole':
            continue
        closest = min(paths, key=lambda p: min(distance(v, center(w)) for v in p['points']))
        features.setdefault(number(closest), []).append({'kind': w['tags']['golf'], 'points': list(map(point, w['points']))})
    holes = []
    for card, matched in zip(sorted(cards, key=lambda c: c['number']), result['matchedHoles']):
        path = by_id[matched['pathID']]['points']; green = by_id[matched['greenID']]
        c = center(green); tee = path[0]; scale = math.cos(math.radians(c[1]))
        ax = (c[0] - tee[0]) * scale; ay = c[1] - tee[1]
        def projection(p):
            return (p[0] - c[0]) * scale * ax + (p[1] - c[1]) * ay
        holes.append({'number': card['number'], 'par': card['par'], 'teeYardages': usable_yards(card),
                      'tee': point(tee), 'front': point(min(green['points'], key=projection)),
                      'center': point(c), 'back': point(max(green['points'], key=projection)),
                      'path': list(map(point, path[:-1] + [c])), 'features': features.get(card['number'], [])})
    return dict(result, holes=holes)
