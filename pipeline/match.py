"""Phase-one rule diagnostics, not an app exporter or a publication gate.

WGS84 geodesics, current vertex-average centers and strict comparison thresholds.
Phase two must validate these diagnostics against the actual Swift implementation.
"""
from collections import Counter
import math
import re
from pyproj import Geod

GEOD = Geod(ellps='WGS84')
YARDS_PER_METER = 1.0936133

def distance(a, b):
    return GEOD.inv(a[0], a[1], b[0], b[1])[2]

def center(way):
    points = way['points']
    points = points[:-1] if len(points) > 1 and points[0] == points[-1] else points
    return tuple(sum(p[axis] for p in points) / len(points) for axis in (0, 1))

def number(way):
    tags = way['tags']
    ref = tags.get('ref', '')
    if re.fullmatch(r'[+-]?[0-9]+', ref):
        return int(ref)
    digits = ''.join(c for c in tags.get('name', '') if c.isdecimal())
    return int(digits) if digits else None

def usable_yards(card):
    return {k: v for k, v in (card.get('yardages') or {}).items()
            if k and isinstance(v, int) and not isinstance(v, bool) and 30 <= v <= 900}

def card_error(cards):
    if not isinstance(cards, list) or len(cards) not in (9, 18):
        return 'scorecard: expected exactly 9 or 18 holes'
    if sorted(c.get('number', -1) for c in cards) != list(range(1, len(cards) + 1)):
        return 'scorecard: incomplete or duplicate hole numbers'
    if any(not isinstance(c.get('par'), int) or not 3 <= c['par'] <= 6 for c in cards):
        return 'scorecard: par outside 3–6'
    return None

def yardage_error(cards):
    if card_error(cards):
        return card_error(cards)
    tees = [set(usable_yards(c)) for c in cards]
    if not all(tees):
        return 'scorecard: a hole has no usable tee yardage'
    if not set.intersection(*tees):
        return 'scorecard: no common tee across all holes'
    return None

def valid_way(w, origin):
    return len(w['points']) >= 2 and all(
        math.isfinite(p[0]) and math.isfinite(p[1]) and -180 <= p[0] <= 180 and -90 <= p[1] <= 90
        and distance(p, origin) < 3500 for p in w['points'])

def counts(ways):
    paths = [w for w in ways if w['tags'].get('golf') == 'hole']
    nums = [number(w) for w in paths if number(w) is not None]
    return {'numberedLines': sorted(nums), 'duplicateNumbers': sorted(n for n, count in Counter(nums).items() if count > 1),
            'unnumberedLines': sum(number(w) is None for w in paths),
            'greens': sum(w['tags'].get('golf') == 'green' and len(w['points']) >= 4 for w in ways),
            'teeAreas': sum(w['tags'].get('golf') == 'tee' and len(w['points']) >= 4 and w['points'][0] == w['points'][-1] for w in ways),
            'holeLines': len(paths)}

def diagnose(cards, ways, origin):
    error = card_error(cards)
    if error:
        return {'pass': False, 'rule': 'scorecard', 'reason': error}
    ways = [w for w in ways if valid_way(w, origin)]
    paths = [w for w in ways if w['tags'].get('golf') == 'hole' and number(w) is not None]
    groups = {}
    for w in paths:
        groups.setdefault(number(w), []).append(w)
    missing = [c['number'] for c in cards if len(groups.get(c['number'], [])) != 1]
    if missing:
        return {'pass': False, 'rule': 'numbering', 'reason': 'Missing or ambiguous numbered lines: ' + ', '.join(map(str, sorted(missing)))}
    greens = [w for w in ways if w['tags'].get('golf') == 'green' and len(w['points']) >= 4]
    used = set()
    matches = []
    common = None
    for card in sorted(cards, key=lambda c: c['number']):
        n = card['number']
        path = groups[n][0]['points']
        near = sorted([(distance(center(w), path[-1]), w) for w in greens], key=lambda pair: pair[0])
        if not near or near[0][0] >= 60 or (len(near) > 1 and near[1][0] <= near[0][0] + 10) or near[0][1]['id'] in used:
            return {'pass': False, 'rule': 'green', 'reason': f'Hole {n}: green missing, ambiguous or shared', 'matchedHoles': matches}
        green = near[0][1]
        used.add(green['id'])
        if distance(path[0], center(green)) <= 25:
            return {'pass': False, 'rule': 'tee', 'reason': f'Hole {n}: tee-to-green is at most 25 m'}
        yards = usable_yards(card)
        if not yards:
            return {'pass': False, 'rule': 'scorecard', 'reason': f'Hole {n}: no usable yardages'}
        length = sum(distance(a, b) for a, b in zip(path, path[1:])) * YARDS_PER_METER
        if not max(yards.values()) * .45 < length < max(yards.values()) * 1.7:
            return {'pass': False, 'rule': 'length', 'reason': f'Hole {n}: mapped {length:.1f} yd versus longest scorecard {max(yards.values())} yd', 'matchedHoles': matches}
        common = set(yards) if common is None else common & set(yards)
        matches.append({'number': n, 'pathID': groups[n][0]['id'], 'greenID': green['id'], 'center': center(green)})
    if not common:
        return {'pass': False, 'rule': 'scorecard', 'reason': 'No common tee across every hole'}
    return {'pass': True, 'rule': 'pass', 'reason': 'Provisional matching rules pass; parity and publication validation pending', 'matchedHoles': matches}
