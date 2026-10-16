"""Research only: measure an area-centroid alternative without changing exports."""
from collections import Counter
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
import json
import os
import urllib.request

from pyproj import Transformer
from shapely.geometry import Polygon, Point
from shapely.ops import transform
from pipeline.common import cloud_only, read, write, now
from pipeline.match import center as vertex_center, distance
from pipeline.full_match import match
from pipeline.layouts import separate
from pipeline.quality import check
from pipeline.reviewed_cards import facts

INPUT_RUN = '38978798804'
INPUT_SHA = 'ddac8e4e06f148c0e89bcdb893d66d846e06d19e'


def verify():
    cloud_only()
    repo = os.environ['GITHUB_REPOSITORY']
    assert repo == 'CraneCourse/crane-course-data'
    req = urllib.request.Request(f'https://api.github.com/repos/{repo}/actions/runs/{INPUT_RUN}',
        headers={'Authorization': 'Bearer '+os.environ['GITHUB_TOKEN'], 'Accept': 'application/vnd.github+json'})
    with urllib.request.urlopen(req, timeout=30) as response:
        run = json.load(response)
    assert run['head_repository']['full_name'] == repo and run['head_branch'] == 'main'
    assert run['path'] == '.github/workflows/resolve.yml' and run['event'] == 'workflow_dispatch'
    assert run['conclusion'] == 'success' and run['head_sha'] == INPUT_SHA


def area_center(way):
    # Only greens change. Other feature centers and all source vertices stay intact.
    if way['tags'].get('golf') != 'green':
        return vertex_center(way)
    points = way['points']
    if len(points) < 4 or points[0] != points[-1]:
        raise ValueError('Open or incomplete green polygon')
    polygon = Polygon(points)
    if not polygon.is_valid or polygon.is_empty or polygon.area <= 0:
        raise ValueError('Invalid green polygon; no repair attempted')
    origin = vertex_center(way)
    crs = f'+proj=aeqd +lat_0={origin[1]} +lon_0={origin[0]} +datum=WGS84 +units=m'
    forward = Transformer.from_crs('EPSG:4326', crs, always_xy=True).transform
    backward = Transformer.from_crs(crs, 'EPSG:4326', always_xy=True).transform
    metric = transform(forward, polygon)
    c = metric.centroid
    # A polygon's area centroid can also lie outside a concave green. Do not
    # silently substitute an arbitrary point or relax the containment gate.
    if not metric.covers(c):
        raise ValueError('Area centroid is outside its green')
    return backward(c.x, c.y)


@contextmanager
def center_mode(area=False):
    if area:
        with patch('pipeline.match.center', area_center), patch('pipeline.full_match.center', area_center):
            yield
    else:
        yield


def evaluate(payload, area=False):
    p = deepcopy(payload)
    c = p['candidate']['center']
    try:
        with center_mode(area):
            p['python'] = match(p['scorecard']['holes'], p['ways'], (c['lon'], c['lat']))
            result = p['python']
            error = check(p, result['holes'], 0) if result['pass'] else result['reason']
        return {'pass': error is None, 'reason': error, 'matches': result.get('matchedHoles', []),
                'holes': result.get('holes', [])}
    except ValueError as exc:
        return {'pass': False, 'reason': str(exc), 'matches': [], 'holes': []}


def comparison(old, new):
    old_m = {h['number']: h for h in old['matches']}
    new_m = {h['number']: h for h in new['matches']}
    changes = []
    shifts = []
    for n in sorted(old_m.keys() & new_m.keys()):
        a, b = old_m[n], new_m[n]
        if (a['pathID'], a['greenID']) != (b['pathID'], b['greenID']):
            changes.append({'number': n, 'oldPath': a['pathID'], 'newPath': b['pathID'],
                            'oldGreen': a['greenID'], 'newGreen': b['greenID']})
        else:
            shifts.append({'number': n, 'greenID': a['greenID'],
                           'meters': round(distance(a['center'], b['center']), 3)})
    return {'changedAssignments': changes, 'centerShifts': shifts,
            'maximumCenterShiftMeters': max((h['meters'] for h in shifts), default=None),
            'uncomparedHoleNumbers': sorted(old_m.keys() ^ new_m.keys())}


def group(reason):
    reason = reason or ''
    if 'boundary' in reason.lower() and 'numbered' not in reason:
        return 'boundary identity'
    if 'numbered layout' in reason or 'numbered lines' in reason:
        return 'incomplete or ambiguous hole numbering'
    if 'par ' in reason or 'scorecard' in reason or 'yardage' in reason:
        return 'scorecard / routing conflict'
    if 'outside its green' in reason or 'polygon' in reason:
        return 'polygon / center geometry'
    if 'green missing' in reason:
        return 'missing / ambiguous green'
    return 'other'


def main():
    cloud_only()
    baseline = read(f'reports/layout-resolution-{INPUT_RUN}.json')
    ready = set(baseline['validatedCourseIDs'])
    assert len(ready) == 38
    payloads = {f.stem: read(f) for f in Path('work/validation').glob('*.json') if not f.name.startswith('control-')}
    raw = {f.stem: read(f) for f in Path('work/layout-evidence').glob('*.json')}
    assert set(payloads) == set(raw) and len(raw) == 147, 'Incomplete trusted course inputs'
    reviewed = read('inputs/reviewed-scorecards.json')
    rows = []
    signatures = set()
    for cid in ready:
        signatures.add(tuple((m['pathID'], m['greenID']) for m in payloads[cid]['python']['matchedHoles']))
    for cid, source in sorted(raw.items()):
        # Controls use their exact already-published boundary selection. Failed
        # courses use the newly audited separation with reviewed cards, if any.
        error = None
        if cid in ready:
            p = payloads[cid]
        else:
            cards = facts(reviewed[cid]) if cid in reviewed else None
            p, error = separate(source, source['boundaries'], cards)
        row = {'candidate': source['candidate'], 'alreadyPublished': cid in ready}
        if p is None:
            row.update(status='earlier-blocker', reason=error, category=group(error))
        else:
            old, new = evaluate(p), evaluate(p, True)
            row.update(original={k:v for k,v in old.items() if k!='holes'},
                       areaCentroid={k:v for k,v in new.items() if k!='holes'},
                       comparison=comparison(old, new), category=group(old['reason']))
            sig = tuple((m['pathID'], m['greenID']) for m in new['matches'])
            row['duplicatePublishedLayout'] = sig in signatures if new['pass'] and cid not in ready else False
            row['status'] = ('published-control' if cid in ready else
                'potential-recovery' if not old['pass'] and new['pass'] and not row['duplicatePublishedLayout'] else 'still-withheld')
            if row['status'] == 'potential-recovery':
                signatures.add(sig)
            # Save failures and their alternatives as diagnostic evidence only.
            if cid not in ready and old['reason'] and ('green' in old['reason'] or 'polygon' in old['reason']):
                write(f'work/center-evidence/{cid}.json', {'input':p, 'original':old, 'areaCentroid':new})
        rows.append(row)
        print(source['candidate']['name'], row['status'], row.get('reason', row.get('areaCentroid', {}).get('reason')), flush=True)
    controls = [r for r in rows if r['alreadyPublished']]
    report = {'runID': os.environ['GITHUB_RUN_ID'], 'sourceCommit': os.environ['GITHUB_SHA'], 'builtAt': now(),
              'inputRun': INPUT_RUN, 'publishedCountUnchanged': 38, 'inputCount': len(rows),
              'potentialRecoveries': [r['candidate'] for r in rows if r['status']=='potential-recovery'],
              'controlFailures': [r['candidate'] for r in controls if not r['areaCentroid']['pass']],
              'controlAssignmentChanges': [r['candidate'] for r in controls if r['comparison']['changedAssignments']],
              'firstBlockers': dict(Counter(r['category'] for r in rows if not r['alreadyPublished'])), 'rows': rows,
              'limitations': 'Research only. No exports or app changes. Area centroids are not surveyed green centers. Python results are not Swift parity, Watch/schema certification, imagery testing, or field verification. No missing geometry is invented.'}
    stem = 'center-study-'+os.environ['GITHUB_RUN_ID']
    write('reports/'+stem+'.json', report)
    lines = ['# Green-center research and remaining coverage blockers', '', report['limitations'], '',
        f"Inputs: {len(rows)}; published dataset unchanged at 38.",
        f"Potential additional layouts from area centroids: {len(report['potentialRecoveries'])}.",
        f"Published controls failing alternate model: {len(report['controlFailures'])}; changed green/path assignments: {len(report['controlAssignmentChanges'])}.", '',
        '## First blockers (not independent or national prevalence)', '']
    lines += [f'- {k}: {v}' for k,v in sorted(report['firstBlockers'].items())]
    lines += ['', '## Per-course result', '', '| Course | Result | Old / earlier blocker | Alternate blocker | Max center shift (m) |', '|---|---|---|---|---|']
    for r in rows:
        values = [r['candidate']['name'], r['status'], r.get('reason', r.get('original', {}).get('reason')) or 'pass',
                  r.get('areaCentroid', {}).get('reason') or ('pass' if 'areaCentroid' in r else 'not reached'),
                  str(r.get('comparison', {}).get('maximumCenterShiftMeters'))]
        lines.append('| '+' | '.join(str(v).replace('|','/') for v in values)+' |')
    Path('reports/'+stem+'.md').write_text('\n'.join(lines)+'\n')
    Path('reports/LATEST-CENTER-STUDY.md').write_text(f'[Latest center research]({stem}.md)\n')
    print(json.dumps({k:v for k,v in report.items() if k!='rows'}, indent=2))

if __name__ == '__main__':
    import sys
    verify() if sys.argv[1:] == ['verify'] else main()
