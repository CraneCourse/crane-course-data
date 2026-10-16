"""Prepare strict way-only matching inputs from the same cached public state extract."""
import sys
from pathlib import Path
from shapely.geometry import LineString, mapping
from shapely.ops import transform
from pipeline.common import cloud_only, read, write, sample, state_for
from pipeline.extract import GolfHandler, association, measure
from pipeline.match import valid_way
from pipeline.full_match import match, geometry

KINDS = {'hole', 'green', 'tee', 'fairway', 'bunker', 'water_hazard', 'lateral_water_hazard'}

def main():
    cloud_only()
    state = sys.argv[1]
    handler = GolfHandler()
    handler.apply_file(f'work/extracts/{state}/golf.osm.pbf', locations=True, idx='flex_mem')
    candidates = [c for c in sample() if state_for(c) == state]
    if state == 'south-carolina':
        candidates.append(read('inputs/kiawah-control.json'))
    rows = []
    for c in candidates:
        origin = (c['center']['lon'], c['center']['lat'])
        score = read(f'work/scorecards/{c["id"]}.json')
        if 'response' not in score: raise RuntimeError(f'Scorecard acquisition failed: {c["id"]}; retain previous publication')
        row = measure(c, handler, score)
        boundary, project, link = association(handler.areas, origin)
        ways = [w for w in handler.ways if w['tags'].get('golf') in KINDS and valid_way(w, origin)]
        if boundary is not None:
            ways = [w for w in ways if boundary.buffer(100).covers(transform(project, LineString(w['points'])))]
        cards = score.get('response', {}).get('holes', [])
        result = match(cards, ways, origin)
        row['fullResult'] = {k:v for k,v in result.items() if k != 'holes'}
        payload = {'candidate': c, 'scorecard': {'holes': cards}, 'geometry': geometry(ways),
                   'ways': ways, 'python': result, 'association': link,
                   'boundary': mapping(boundary) if boundary is not None else None,
                   'state': state, 'source': read(f'work/extracts/{state}/source.json'),
                   'scorecardSource': row['scorecardSource'], 'parserErrors': handler.errors}
        write(f'work/validation/{c["id"]}.json', payload)
        rows.append(row)
        print(c['name'], result['rule'], result['reason'], flush=True)
    write(f'work/results/{state}.json', {'state': state, 'rows': rows})

if __name__ == '__main__': main()
