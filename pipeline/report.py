from collections import Counter
from pathlib import Path
import os
from pipeline.common import cloud_only, sample, state_for, read, write, now

def safe(value):
    return str(value).replace('|', '/').replace('\n', ' ').replace('<', '&lt;').replace('>', '&gt;')

def main():
    cloud_only()
    sources = [read(p) for p in sorted(Path('work/results').glob('*.json'))]
    measured = {row['candidate']['id']: row for s in sources for row in s['rows']}
    baseline = {r['entry']['candidate']['id']: r for r in read('inputs/baseline.json')['rows']}
    rows = []
    for candidate in sample():
        cid = candidate['id']
        if cid in measured:
            row = measured[cid]
        else:
            state = state_for(candidate)
            row = {'candidate': candidate, 'category': 'state-job-incomplete' if state else 'outside-state-scope',
                   'ruleResult': {'pass': False, 'rule': 'not-tested', 'reason': 'State extraction did not complete' if state else 'Outside contiguous-US bounds or no recognized US state in directory; not measured'}}
        row['baselineStatus'] = baseline.get(cid, {}).get('status', 'unknown')
        row['baselineReason'] = baseline.get(cid, {}).get('reason', '')
        rows.append(row)
    counts = Counter(r['category'] for r in rows)
    passing = [r for r in rows if r['category'] == 'provisional-pass']
    # The baseline status is recorded verbatim; only known geometry-ready means passed.
    unlocked = [r for r in passing if r['baselineStatus'] == 'unsupported']
    phase3 = [r for r in rows if r.get('phase3InventoryCandidate')]
    new3 = [r for r in phase3 if r['baselineStatus'] == 'unsupported']
    result = {'builtAt': now(), 'runID': os.environ['GITHUB_RUN_ID'], 'sourceCommit': os.environ['GITHUB_SHA'],
              'phase': 'measurement-only', 'publishedPlayableCourses': 0,
              'counts': dict(counts), 'phase2ProvisionalNew': len(unlocked), 'phase3InventoryOnly': len(phase3),
              'phase3InventoryNew': len(new3), 'stateSources': [{k:v for k,v in s.items() if k != 'rows'} for s in sources], 'rows': rows}
    date = result['builtAt'][:10]
    stem = f'reports/measure-{date}-{result["runID"]}'
    write(stem + '.json', result)
    lines = [f'# Course-data measurement — {date}', '',
             f'Run [{result["runID"]}](https://github.com/CraneCourse/crane-course-data/actions/runs/{result["runID"]}) · source `{result["sourceCommit"]}`', '',
             f'**{len(rows)} original sample entries; {len(measured)} measured from {len(sources)} state extracts.** This selected sample is not a national coverage estimate.', '',
             f'**{len(passing)} provisional rule passes**, including **{len(unlocked)} previously unsupported courses** that phase 2 could potentially recover.', '',
             f'**{len(phase3)} additional inventory candidates for numbering research** ({len(new3)} previously unsupported). These only have enough green/tee polygons by count. They are not validated routes, unique assignments, or promised coverage.', '',
             '**0 playable courses published.** No app changes, user data, on-device processing, aerial downloads, or inferred numbering in this phase.', '',
             '## Failure accounting', '', '| Category | Courses |', '|---|---:|']
    lines += [f'| {k} | {v} |' for k,v in sorted(counts.items())]
    lines += ['', '## What these results mean', '',
              '- Phase 2 potential is based on raw Geofabrik geometry, conservative spatial boundary association, and a provisional Python rule diagnostic. Swift parity, boundary identity, front/back containment, payload size and imagery still need validation.',
              '- Phase 3 inventory is disjoint from provisional passes. Tee polygons are not tee-to-hole assignments; multiple tee polygons can serve one hole. The actual unlock count may be zero.',
              '- Missing greens may motivate later imagery research, but this run cannot establish that green detection will work. No phase-4 unlock count is claimed.',
              '- No-boundary and ambiguous-boundary rows retain radius counts for investigation. Their rule results never count as course passes.',
              '- Relation greens are counted separately and not silently substituted for way greens. Parser errors and source timestamps are in the JSON report.',
              '- The original sample has international/non-contiguous results and unrecognized state fields. Those are separately excluded, never called missing OSM data.',
              '- Centers remain vertex averages. Metric area-centroid displacement is reported in JSON only; no center change is applied.',
              '- All numbered paths must pass; no partial course is accepted. API or runner failures remain operational failures, not unsupported-course evidence.', '',
              '## Course-by-course measurements', '',
              '| Course | State | Cards | Area link | Numbered lines | Unnumbered | Greens | Tee areas | Result / first rule |',
              '|---|---|---:|---|---|---:|---:|---:|---|']
    for r in rows:
        c = r['candidate']; n = r.get('counts', {})
        label = ','.join(map(str, n.get('numberedLines', []))) or '—'
        if n.get('duplicateNumbers'): label += ' (duplicates)'
        scope = ' [radius only]' if r.get('countScope') == 'unassociated-3500m-radius' else ''
        values = [c['name'], c.get('state', ''), r.get('scorecardHoleCount', '—'), r.get('association', {}).get('method', 'not measured'), label + scope,
                  n.get('unnumberedLines', '—'), n.get('greens', '—'), n.get('teeAreas', '—'), r['category'] + ': ' + r['ruleResult']['reason']]
        lines.append('| ' + ' | '.join(safe(v) for v in values) + ' |')
    lines += ['', '## Sources and licensing', '',
              '© [OpenStreetMap contributors](https://www.openstreetmap.org/copyright), distributed via [Geofabrik](https://download.geofabrik.de/north-america/us.html). [OpenGolfAPI](https://www.opengolfapi.org/attribution/) supplies directory and scorecards. Database and reports: [ODbL 1.0](https://opendatacommons.org/licenses/odbl/1-0/). See SOURCES.md for scope and rate policy.', '']
    text = '\n'.join(lines)
    Path(stem + '.md').write_text(text)
    Path('reports/LATEST.md').write_text(f'[Latest measurement]({Path(stem).name}.md)\n')
    with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as out:
        out.write('\n'.join(lines[:30]))
    print(dict(counts))
    print(f'Phase 2 provisional new: {len(unlocked)}; Phase 3 inventory only: {len(phase3)}')

if __name__ == '__main__':
    main()
