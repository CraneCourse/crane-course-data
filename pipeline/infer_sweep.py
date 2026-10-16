"""Cloud-only phase-three research, including blind recovery on real controls."""
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from collections import Counter
import hashlib
import os

from pipeline.common import cloud_only, read, write, now, sample, state_for
from pipeline.infer import infer, inventory, DEFAULT
from pipeline.match import number
from pipeline.export import KIAWAH


def masked(payload, holes=None, all_lines=False):
    result = deepcopy(payload)
    # No expected Python match or pre-encoded geometry is available to inference.
    result.pop('python', None)
    result.pop('geometry', None)
    for w in result['ways']:
        if w['tags'].get('golf') == 'hole' and (all_lines or number(w) in holes):
            w['tags'].pop('ref', None)
            w['tags'].pop('name', None)
    return result


def recovery(payload, holes=None, all_lines=False):
    expected = {h['number']: (h['pathID'], h['greenID']) for h in payload['python']['matchedHoles']}
    test_input = masked(payload, holes, all_lines)
    result = infer(test_input)
    correct = None
    if result['status'] == 'unique-hypothesis':
        actual = {h['number']: (h['pathID'], h['greenID']) for h in result['exampleAssignments'][0]}
        correct = actual == expected
    return {'candidate': payload['candidate'], 'mask': 'all' if all_lines else holes,
            'exactRecovery': correct, 'result': result}


def main():
    cloud_only()
    payloads = {p.stem: read(p) for p in Path('work/validation').glob('*.json') if not p.name.startswith('control-')}
    expected = {c['id'] for c in sample() if state_for(c)} | {KIAWAH}
    assert set(payloads) == expected, 'Incomplete cloud input set'
    baseline = read('reports/build-2026-10-16-38973050174.json')
    baseline_rows = {r['candidate']['id']: r for r in baseline['rows']}
    rows, controls = [], []
    for cid, payload in sorted(payloads.items()):
        prior = baseline_rows[cid]
        row = {'candidate': payload['candidate'], 'inventory': inventory(payload),
               'baselineStatus': prior['status'], 'baselineReason': prior['reason'],
               'stateSource': payload['source'], 'scorecardSource': payload['scorecardSource']}
        if prior['status'] == 'data-ready':
            row['status'] = 'already-published'
            # Two different single-hole masks plus a full-numbering blind trial.
            for mask in ([1], [len(payload['scorecard']['holes'])], None):
                control = recovery(payload, mask, all_lines=mask is None)
                controls.append(control)
                print('CONTROL', payload['candidate']['name'], control['mask'], control['result']['status'], control['exactRecovery'], flush=True)
        elif payload['python'].get('rule') != 'numbering':
            row.update(status='other-blocker', reason=prior['reason'])
        else:
            mapped = infer(payload, 'mapped-paths')
            paired = infer(payload, 'tee-green')
            row.update(status='numbering-research', mappedPaths=mapped, teeGreen=paired)
            print('RESEARCH', payload['candidate']['name'], mapped['status'], paired['status'], flush=True)
        rows.append(row)
    # Preserve real input fixtures, without selecting successes by tuning policy.
    # These are public OSM/scorecard data only, never player information.
    fixture_dir = Path('tests/fixtures/inference')
    fixture_dir.mkdir(parents=True, exist_ok=True)
    # Prefer recovering an entire blinded course over the easier single-hole case.
    good = next((c for c in controls if c['exactRecovery'] is True and c['mask']=='all'), None)
    if good is None:
        good = next((c for c in controls if c['exactRecovery'] is True), None)
    selected = {'correct': good, 'ambiguous': None}
    # Save a naturally ambiguous failing course when available, not only a masked
    # known-good course. Both experiments stay covered by the full sweep report.
    for r in rows:
        for mode, key in [('mapped-paths','mappedPaths'), ('tee-green','teeGreen')]:
            if r.get(key, {}).get('status') == 'ambiguous':
                selected['ambiguous'] = {'candidate':r['candidate'], 'mask':None, 'mode':mode, 'result':r[key], 'exactRecovery':None}
                break
        if selected['ambiguous']:
            break
    if selected['ambiguous'] is None:
        selected['ambiguous'] = next((c for c in controls if c['result']['status']=='ambiguous'), None)
    fixture_manifest = {}
    for name, control in selected.items():
        if control is None:
            continue
        cid = control['candidate']['id']
        mode = control.get('mode', 'mapped-paths')
        p = payloads[cid]
        if control['mask'] is not None:
            p = masked(p, None if control['mask']=='all' else control['mask'], control['mask']=='all')
        else:
            p = deepcopy(p)
            p.pop('python', None)
            p.pop('geometry', None)
        write(fixture_dir / (name+'.json'), p)
        fixture_manifest[name] = {'courseID':cid, 'mode':mode, 'mask':control['mask'],
                                  'status':control['result']['status'],
                                  'assignment':control['result'].get('exampleAssignments', [None])[0] if name=='correct' else None}
    write(fixture_dir/'manifest.json', fixture_manifest)
    wrong = [c for c in controls if c['exactRecovery'] is False]
    counts = dict(Counter(r['status'] for r in rows))
    research = [r for r in rows if r['status']=='numbering-research']
    blockers = dict(Counter(r['mappedPaths']['reason'] for r in research if r['mappedPaths']['status']=='ineligible'))
    report = {'builtAt':now(), 'runID':os.environ['GITHUB_RUN_ID'], 'sourceCommit':os.environ['GITHUB_SHA'],
              'inputRun':read('work/input-run.json'), 'baselineRunID':baseline['runID'], 'policy':asdict(DEFAULT),
              'sampleCount':207, 'stateScopedSampleCount':146, 'extraControls':1,
              'counts':counts, 'mappedPathResults':dict(Counter(r['mappedPaths']['status'] for r in research)),
              'earlyBlockers':blockers,
              'teeGreenResults':dict(Counter(r['teeGreen']['status'] for r in research)),
              'controlTrials':len(controls), 'correctUniqueRecoveries':sum(c['exactRecovery'] is True for c in controls),
              'incorrectUniqueRecoveries':len(wrong), 'newPublishedCourses':0, 'publishedCoursesUnchanged':32,
              'fixtures':fixture_manifest, 'rows':rows, 'controls':controls,
              'inputChecksums':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in Path('work/validation').glob('*.json')},
              'limitation':'Research only. Unique heuristic assignments are not independently verified numbering. No inferred course is published, no missing path geometry is invented, and no app data source is changed.'}
    stem=f'inference-{report["builtAt"][:10]}-{report["runID"]}'
    write(f'reports/{stem}.json',report)
    lines=['# Phase 3: hole-numbering research','',report['limitation'],'',
           f'Run: https://github.com/CraneCourse/crane-course-data/actions/runs/{report["runID"]}',
           f'Input extraction run: {report["inputRun"]["id"]}. Source commit: `{report["sourceCommit"]}`.','',
           f'Original sample: 207; state-scoped: 146, plus Kiawah control. Published courses remain 32. **Newly published: 0.**','',
           f'Blind controls: {len(controls)} trials; {report["correctUniqueRecoveries"]} exact unique recoveries; {len(wrong)} incorrect unique recoveries. Unresolved trials are withheld, not counted as correct.','',
           'Mapped-path outcomes: '+str(report['mappedPathResults']),
           'Tee/green-only outcomes: '+str(report['teeGreenResults']),'',
           'Numbers/par already present are preserved. All assignments share no greens/starts; all transitions, including last→first, are checked. Search uses enlarged yardage/transfer tolerances to expose near alternatives. Finding two solutions or reaching a search budget withholds the course. No clubhouse/entrance anchors were present in the saved extraction format.','',
           '## Earlier blockers','', '| Reason | Candidates |','|---|---|']
    lines += [f'| {reason} | {count} |' for reason,count in sorted(blockers.items(),key=lambda item:-item[1])]
    lines += ['', 'These are first blocking reasons, not proof that each course has only one problem. Resolve course identity/subcourse separation and missing scorecards before expecting numbering inference to increase coverage. This run does not establish that all remaining courses are impossible to support.',
              '', '## Course outcomes','', '| Course | State | Mapped paths | Tee/green hypotheses |','|---|---|---|---|']
    def safe(s):
        return str(s).replace('|','/').replace('\n',' ').replace('<','&lt;').replace('>','&gt;')
    for r in sorted(rows,key=lambda r:r['candidate']['name']):
        cells=[r['candidate']['name'],r['candidate']['state']]
        for key in ('mappedPaths','teeGreen'):
            s=r.get(key)
            cells.append((s['status']+': '+s['reason']) if s else r['status']+': '+r.get('reason','already passes phase-two checks'))
        lines.append('| '+' | '.join(map(safe,cells))+' |')
    lines+=['','© OpenStreetMap contributors · Geofabrik · OpenGolfAPI. Data: ODbL 1.0.']
    Path(f'reports/{stem}.md').write_text('\n'.join(lines)+'\n')
    Path('reports/LATEST-INFERENCE.md').write_text(f'[Latest inference research report]({stem}.md)\n')
    write('work/inference-summary.json', {k:v for k,v in report.items() if k not in {'rows','controls','inputChecksums'}})
    print({k:v for k,v in report.items() if k not in {'rows','controls','inputChecksums'}}, flush=True)
    assert not wrong, 'A blind recovery produced wrong numbering; research cannot be promoted'
    assert good, 'No real correct inference fixture; inspect model limitations'
    assert selected['ambiguous'], 'No real ambiguity fixture; inspect research evidence'


if __name__=='__main__': main()
