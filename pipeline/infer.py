"""Conservative, bounded hole-numbering research. Never a publication gate.

A unique solution to these heuristics is a hypothesis, not verified numbering.
Mapped paths are never moved/reversed. Tee/green-only hypotheses never become paths.
"""
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
import time

from shapely.geometry import Point, Polygon
from pipeline.match import center, distance, number, usable_yards, yardage_error, YARDS_PER_METER
from pipeline.quality import identity_error


@dataclass(frozen=True)
class Policy:
    transition_m: float = 300
    relaxed_multiplier: float = 1.25
    tee_start_m: float = 25
    max_nodes: int = 20000
    max_seconds: float = 12


DEFAULT = Policy()


def polygon_ok(way):
    p = way['points']
    return (len(p) >= 4 and p[0] == p[-1] and Polygon(p).is_valid
            and Polygon(p).area > 0 and Polygon(p).covers(Point(center(way))))


def tolerance(card):
    # Fixed before the sweep. These are research assumptions, not golf standards.
    longest = max(usable_yards(card).values())
    return max(20, longest * {3: .15, 4: .20, 5: .25, 6: .25}[card['par']])


def eligible(option, card, multiplier=1):
    if card['number'] in option.get('reservedNumbers', []):
        return False
    if option['anchor'] is not None and option['anchor'] != card['number']:
        return False
    if option['par'] is not None and option['par'] != card['par']:
        return False
    if card['par'] == 3 and option['yards'] > 275 * multiplier:
        return False
    values = usable_yards(card).values()
    # A mapped route normally starts at a rear tee. With no route, the tee color
    # is unknown, so consider every scorecard tee instead of inventing an identity.
    references = [max(values)] if option['kind'] == 'mapped-path' else values
    return any(abs(option['yards'] - y) <= tolerance(card) * multiplier for y in references)


def solve(cards, options, policy=DEFAULT):
    """Enumerate up to two relaxed solutions; budget exhaustion always withholds.

    Different physical tee choices count as ambiguity. No beam search, nearest-only
    pairing, or arbitrary tie-break may turn multiple solutions into a unique one.
    """
    cards = sorted(cards, key=lambda c: c['number'])
    n = len(cards)
    domains = [[j for j, o in enumerate(options) if eligible(o, c, policy.relaxed_multiplier)] for c in cards]
    empty = [cards[i]['number'] for i, d in enumerate(domains) if not d]
    if empty:
        return {'status': 'no-assignment', 'reason': 'No compatible candidate for holes ' + ', '.join(map(str, empty)),
                'domainSizes': list(map(len, domains)), 'nodes': 0, 'searchComplete': True}
    resources = [frozenset((('green', o['greenID']), ('start', o['startID']))) for o in options]
    started = time.monotonic()
    nodes = 0
    limited = False
    solutions = []
    assigned = {}

    @lru_cache(maxsize=200000)
    def transfer(a, b):
        return distance(options[a]['end'], options[b]['start'])

    def fits(i, j, used):
        if resources[j] & used:
            return False
        prev, after = (i - 1) % n, (i + 1) % n
        limit = policy.transition_m * policy.relaxed_multiplier
        return ((prev not in assigned or transfer(assigned[prev], j) <= limit)
                and (after not in assigned or transfer(j, assigned[after]) <= limit))

    def visit(used):
        nonlocal nodes, limited
        nodes += 1
        if nodes > policy.max_nodes or time.monotonic() - started > policy.max_seconds:
            limited = True
            return
        if len(assigned) == n:
            solutions.append([assigned[i] for i in range(n)])
            return
        # Minimum remaining domain, including both already assigned neighbours.
        choices = []
        for i in range(n):
            if i in assigned:
                continue
            possible = [j for j in domains[i] if fits(i, j, used)]
            if not possible:
                return
            choices.append((len(possible), i, possible))
        _, i, possible = min(choices)
        for j in possible:
            assigned[i] = j
            visit(used | resources[j])
            del assigned[i]
            if limited or len(solutions) >= 2:
                return

    visit(frozenset())
    result = {'domainSizes': list(map(len, domains)), 'nodes': nodes,
              'searchComplete': not limited and len(solutions) < 2,
              'solutionsFound': len(solutions), 'solutionCountIsLowerBound': limited or len(solutions) >= 2}
    if limited:
        return dict(result, status='search-limit', reason='Search budget exhausted; uniqueness not established')
    if not solutions:
        return dict(result, status='no-assignment', reason='No complete one-to-one layout meets the relaxed route and yardage checks')
    def describe(solution):
        return [dict(number=i+1, pathID=options[j].get('pathID'), greenID=options[j]['greenID'],
                     startID=options[j]['startID'], yards=round(options[j]['yards'], 2),
                     nextTeeMeters=round(transfer(j, solution[(i+1) % n]), 2)) for i, j in enumerate(solution)]
    result['exampleAssignments'] = [describe(s) for s in solutions]
    if len(solutions) >= 2:
        return dict(result, status='ambiguous', reason='At least two complete layouts fit the relaxed evidence; neither is selected')
    solution = solutions[0]
    strict = all(eligible(options[j], cards[i]) and transfer(j, solution[(i+1) % n]) <= policy.transition_m
                 for i, j in enumerate(solution))
    if not strict:
        return dict(result, status='weak-fit', reason='The sole relaxed assignment fails the stricter yardage or route checks')
    return dict(result, status='unique-hypothesis', reason='One strict assignment and no alternative in the enlarged tolerance search; independent numbering evidence still required')


def inventory(payload):
    kinds = Counter(w['tags'].get('golf') for w in payload['ways'])
    paths = [w for w in payload['ways'] if w['tags'].get('golf') == 'hole']
    return {'holes': len(payload['scorecard']['holes']), 'lines': len(paths),
            'numberedLines': sorted(number(w) for w in paths if number(w) is not None),
            'unnumberedLines': sum(number(w) is None for w in paths),
            'greens': kinds['green'], 'teeAreas': kinds['tee']}


def prepare(payload, mode, policy=DEFAULT):
    cards = payload['scorecard']['holes']
    error = yardage_error(cards) or identity_error(payload['candidate'], payload['association'])
    if error or payload['boundary'] is None:
        return [], error or 'No unambiguous course boundary'
    paths = [w for w in payload['ways'] if w['tags'].get('golf') == 'hole']
    greens = [w for w in payload['ways'] if w['tags'].get('golf') == 'green']
    tees = [w for w in payload['ways'] if w['tags'].get('golf') == 'tee']
    if any(not polygon_ok(w) for w in greens + tees):
        return [], 'Invalid green/tee polygon or vertex-average center outside it'
    if len(greens) < len(cards):
        return [], 'Fewer mapped green polygons than scorecard holes'
    numbered = [number(w) for w in paths if number(w) is not None]
    if len(numbered) != len(set(numbered)) or any(n < 1 or n > len(cards) for n in numbered):
        return [], 'Duplicate or out-of-range mapped numbers; existing evidence cannot be overwritten'
    if any(number(w) is None and w['tags'].get('ref', '').strip() for w in paths):
        return [], 'Non-integer mapped refs require interpretation; not silently discarded'
    if any(w['tags'].get('par') and w['tags']['par'] not in {'3','4','5','6'} for w in paths):
        return [], 'Unsupported mapped par tag; evidence cannot be ignored'
    options, used_anchors = [], set()
    invalid_anchors = []
    for w in paths:
        n = number(w)
        near = sorted((distance(w['points'][-1], center(g)), i) for i, g in enumerate(greens))
        if not near or near[0][0] >= 60 or (len(near) > 1 and near[1][0] <= near[0][0] + 10):
            if n is not None:
                invalid_anchors.append(n)
            continue
        green = greens[near[0][1]]
        start = w['points'][0]
        if distance(start, center(green)) <= 25:
            if n is not None:
                invalid_anchors.append(n)
            continue
        # New numbering needs corroboration that the path starts on a mapped tee.
        near_tees = [t for t in tees if distance(center(t), start) <= policy.tee_start_m]
        if n is None and not near_tees:
            continue
        option = {'kind': 'mapped-path', 'pathID': w['id'], 'startID': w['id'], 'greenID': green['id'],
                  'anchor': n, 'par': int(w['tags']['par']) if w['tags'].get('par') else None,
                  'start': start, 'end': center(green),
                  'yards': sum(distance(a,b) for a,b in zip(w['points'], w['points'][1:])) * YARDS_PER_METER}
        options.append(option)
        if n is not None:
            used_anchors.add(n)
    if invalid_anchors or used_anchors != set(numbered):
        return [], 'A numbered path has missing/ambiguous green geometry; inference cannot repair it'
    anchored_greens = {o['greenID'] for o in options if o['anchor'] is not None}
    if len(anchored_greens) != len(numbered):
        return [], 'Mapped numbered holes share a green'
    # Unnumbered lines must not compete with known lines or conceal extra layouts.
    options = [o for o in options if o['anchor'] is not None or o['greenID'] not in anchored_greens]
    if mode == 'tee-green':
        # This is a separate endpoint-only experiment, retaining numbered
        # anchors. Never turn a hypothetical segment into exported hole geometry.
        options = [o for o in options if o['anchor'] is not None]
        for tee in tees:
            for green in greens:
                if green['id'] in anchored_greens:
                    continue
                start, end = center(tee), center(green)
                yards = distance(start, end) * YARDS_PER_METER
                if not 30 <= yards <= 950:
                    continue
                options.append({'kind': 'tee-green', 'pathID': None, 'startID': tee['id'],
                                'greenID': green['id'], 'anchor': None, 'par': None,
                                'start': start, 'end': end, 'yards': yards})
    elif mode != 'mapped-paths':
        raise ValueError(mode)
    # An existing numbered path must be used for its hole, even when another
    # nearby unnumbered route would have a smaller scorecard error.
    for o in options:
        o['reservedNumbers'] = sorted(used_anchors) if o['anchor'] is None else []
    return options, None


def infer(payload, mode='mapped-paths', policy=DEFAULT):
    options, error = prepare(payload, mode, policy)
    if error:
        return {'status': 'ineligible', 'reason': error, 'searchComplete': False}
    # Keep anchor enforcement separate from soft numeric matching.
    cards = payload['scorecard']['holes']
    anchors = {o['anchor'] for o in options if o['anchor'] is not None}
    result = solve(cards, options, policy)
    return dict(result, mode=mode, options=len(options), anchors=sorted(anchors),
                numbering='inferred', publicationEligible=False,
                limitation='Hypothesis only. No clubhouse/entrance evidence is present in these saved extracts; closed-loop routing is not a substitute. Tee-only distances do not reconstruct doglegs.')
