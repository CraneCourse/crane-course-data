"""Fail-closed publication: all runners and parity checks must complete first."""
import hashlib, html, os, shutil
from collections import Counter
from pathlib import Path
from jsonschema import Draft202012Validator, FormatChecker
from pipeline.common import cloud_only, read, write, now, sample, state_for
from pipeline.quality import check, equivalent
from pipeline.match import distance

LAKESIDE='24925661-c5ad-4a89-87f2-b97a8f77fe83'
KIAWAH='2f8238b3-85d2-4d54-b1af-779dec846381'
SOURCES=['© OpenStreetMap contributors — ODbL 1.0 — https://www.openstreetmap.org/copyright',
         'OSM extracts by Geofabrik — https://download.geofabrik.de/',
         'Directory and scorecards via OpenGolfAPI — https://www.opengolfapi.org/attribution/']

def validate_document(doc, schema):
    Draft202012Validator(read(schema),format_checker=FormatChecker()).validate(doc)

def validate_controls(swift, payloads):
    control=swift['control-'+LAKESIDE]; extraction=swift[LAKESIDE]
    assert control['pass'] and extraction['pass'], 'Lakeside must match in both sources'
    assert [h['number'] for h in control['holes']]==list(range(1,19))
    shifts=[distance((a['center']['lon'],a['center']['lat']), (b['center']['lon'],b['center']['lat']))
            for a,b in zip(control['holes'],extraction['holes'])]
    assert len(shifts)==18 and max(shifts)<1, f'Lakeside center mismatch: {max(shifts)}m'
    assert not swift['control-'+KIAWAH]['pass'] and 'Mapped hole numbers' in swift['control-'+KIAWAH]['reason']
    from pipeline.match import number, diagnose
    api_geometry=swift['control-'+KIAWAH]['parsedGeometry']['elements']
    api_ways=[{'id':'way/'+str(w['id']), 'tags':w.get('tags') or {},
               'points':[(v['lon'],v['lat']) for v in w['geometry']]} for w in api_geometry]
    api_mapped=sorted(number(w) for w in api_ways if w['tags'].get('golf')=='hole' and number(w) is not None)
    expected=[1,2,3,7,8,9,10,11,16,17,18]
    assert api_mapped==expected, f'Kiawah API control mapping changed: {api_mapped}'
    c=payloads[KIAWAH]['candidate']['center']
    api_result=diagnose(payloads[KIAWAH]['scorecard']['holes'],api_ways,(c['lon'],c['lat']))
    assert not api_result['pass'] and api_result['rule']=='numbering'
    direct_mapped=sorted(number(w) for w in payloads[KIAWAH]['ways'] if w['tags'].get('golf')=='hole' and number(w) is not None)
    # Direct extracts contain all 18 lines: the API's eleven-line failure is not
    # a valid expectation for a different input. Verify each input honestly.
    assert direct_mapped==list(range(1,19)), f'Kiawah extract control changed: {direct_mapped}'
    assert swift[KIAWAH]['pass'], f'Kiawah direct matching changed: {swift[KIAWAH]}'
    return {'lakesideHoles':18,'lakesideMaximumCenterDifferenceMeters':max(shifts),
            'kiawahAPIMappedNumbers':api_mapped,'kiawahAPIFailure':swift['control-'+KIAWAH]['reason'],
            'kiawahDirectMappedNumbers':direct_mapped,'kiawahDirectMatchingPass':True,
            'referenceAppCommit':read('reference/provenance.json')['appCommit']}

def first_difference(a,b,path='holes'):
    if equivalent(a,b): return None
    if isinstance(a,dict) and isinstance(b,dict):
        for key in sorted(a.keys()|b.keys()):
            if not equivalent(a.get(key),b.get(key)):
                return first_difference(a.get(key),b.get(key),path+'.'+key)
    if isinstance(a,list) and isinstance(b,list) and len(a)==len(b):
        for i,(x,y) in enumerate(zip(a,b)):
            if not equivalent(x,y): return first_difference(x,y,f'{path}[{i}]')
    return {'path':path,'python':str(a)[:300],'swift':str(b)[:300]}

def main():
    cloud_only()
    swift=read('work/swift.json')
    payloads={p.stem:read(p) for p in Path('work/validation').glob('*.json') if not p.name.startswith('control-')}
    expected={c['id'] for c in sample() if state_for(c)}|{KIAWAH}
    assert set(payloads)==expected, 'Missing or unexpected course jobs; do not replace published index'
    controls=validate_controls(swift,payloads)
    baseline={r['entry']['candidate']['id']:r['status'] for r in read('inputs/baseline.json')['rows']}
    built=now(); version=os.environ['GITHUB_RUN_ID']+'-'+os.environ['GITHUB_SHA'][:12]
    rows=[]; accepted={}; seen={}
    # Prefer an originally supported entry when duplicate directory identities describe one map.
    for cid,p in sorted(payloads.items(), key=lambda item:(baseline.get(item[0])!='ready',item[0])):
        py=p['python']; ref=swift[cid]
        assert py['pass']==ref['pass'], f'{cid}: Swift/Python decision mismatch'
        if py['pass']:
            assert equivalent(py['holes'],ref['holes']), f'{cid}: Swift/Python full Hole mismatch: {first_difference(py["holes"],ref["holes"])}'
        row={'candidate':p['candidate'],'baselineStatus':baseline.get(cid,'control'),'association':p['association'],
             'matching':{k:v for k,v in py.items() if k!='holes'},'swift':{k:v for k,v in ref.items() if k!='holes'},
             'scorecardSource':p['scorecardSource'],'stateSource':p['source'],'parserErrors':p['parserErrors']}
        if not py['pass']:
            row.update(status='excluded',reason=py['reason'])
        else:
            error=check(p,ref['holes'],ref['watchBytes'])
            key=tuple((h['pathID'],h['greenID']) for h in py['matchedHoles'])
            if not error and key in seen:
                error='Duplicate mapped course; represented by '+seen[key]
            if error:
                row.update(status='excluded',reason=error)
            else:
                seen[key]=cid
                doc={'candidate':p['candidate'],'holes':ref['holes'],'dataVersion':version,'builtAt':built,'sources':SOURCES,'numbering':'osm-mapped'}
                validate_document(doc,'schemas/course.schema.json')
                accepted[cid]=doc
                row.update(status='data-ready',reason='All mapped-hole, schema, identity, geometry, Swift parity and Watch gates pass',watchBytes=ref['watchBytes'])
        rows.append(row)
    for c in sample():
        if not state_for(c): rows.append({'candidate':c,'status':'outside-state-scope','reason':'Not a recognized contiguous-US state candidate','baselineStatus':baseline.get(c['id'])})
    assert accepted, 'No courses pass; retain previous dataset for inspection'
    Path('dist/courses').mkdir(parents=True,exist_ok=True)
    index=[]
    for cid,doc in sorted(accepted.items(),key=lambda item:item[1]['candidate']['name']):
        file=f'courses/{cid}.json'; write('dist/'+file,doc)
        tees=sorted(set.intersection(*(set(h['teeYardages']) for h in doc['holes'])))
        index.append(dict(doc['candidate'],holes=len(doc['holes']),tees=tees,dataVersion=version,builtAt=built,file=file,
                          sha256=hashlib.sha256(Path('dist/'+file).read_bytes()).hexdigest()))
    manifest={'dataVersion':version,'builtAt':built,'courses':index,'sources':SOURCES}
    validate_document(manifest,'schemas/index.schema.json'); write('dist/index.json',manifest)
    shutil.copytree('schemas','dist/schemas',dirs_exist_ok=True)
    shutil.copyfile('LICENSE-DATA','dist/LICENSE-DATA'); Path('dist/.nojekyll').touch()
    original=[r for r in rows if r['candidate']['id']!=KIAWAH]
    passed=[r for r in original if r['status']=='data-ready']
    new=[r for r in passed if r['baselineStatus']=='unsupported']
    report={'builtAt':built,'runID':os.environ['GITHUB_RUN_ID'],'sourceCommit':os.environ['GITHUB_SHA'],
            'inputRun':read('work/input-run.json') if Path('work/input-run.json').exists() else {'id':os.environ['GITHUB_RUN_ID'],'head_sha':os.environ['GITHUB_SHA']},'sampleCount':len(original),'measuredSampleCount':len(expected)-1,'dataReady':len(accepted),'sampleDataReady':len(passed),'additionalControlDataReady':int(KIAWAH in accepted),
            'previouslyUnsupportedNowReady':len(new),'baselineReadyRetained':sum(r['baselineStatus']=='ready' for r in passed),
            'controls':controls,'rows':rows,'counts':dict(Counter(r['status'] for r in original)),
            'limitations':'Data-ready is not field-verified or an end-to-end app download. Aerial/elevation downloads and app integration are separate. No inferred numbering or changed green centers.'}
    stem=f'build-{built[:10]}-{os.environ["GITHUB_RUN_ID"]}'
    write(f'reports/{stem}.json',report); write('dist/report.json',report)
    lines=[f'# Phase 2 course data — {built[:10]}','',
           f'**{len(accepted)} distinct courses pass all data gates** ({len(passed)} from the original sample), including **{len(new)} previously unsupported**. '
           f'{len(original)} original candidates; {len(expected)-1} in state scope; Kiawah is an additional regression control.','',
           report['limitations'],'',
           f'Run: https://github.com/CraneCourse/crane-course-data/actions/runs/{os.environ["GITHUB_RUN_ID"]}',
           f'Source: `{os.environ["GITHUB_SHA"]}`','',
           f'Lakeside: 18/18 holes; maximum center difference from app/provider control {max(0,controls["lakesideMaximumCenterDifferenceMeters"]):.6f} m. Kiawah API: 11/18 mapped, rejected for missing numbers. The direct extract has 18/18 and passes matching; its publication decision is listed below.','',
           'Swift CoreLocation and JSONEncoder ran on a standard GitHub macOS runner. Full exported Hole structures match the independent Python port. All common tees fit the Watch budget, with a 1 KB metadata reserve.','',
           'Boundary names must agree after conservative word normalization; aliases and conflicting subcourse numbers are not guessed. Duplicate mapped layouts count once. Invalid polygons, centers outside greens, extra numbered hole lines and unresolved boundaries are withheld.','',
           '## Every course','', '| Course | State | Result | Reason |','|---|---|---|---|']
    def safe(s): return str(s).replace('|','/').replace('\n',' ').replace('<','&lt;').replace('>','&gt;')
    for r in sorted(rows,key=lambda x:x['candidate']['name']):
        lines.append('| '+' | '.join(map(safe,[r['candidate']['name'],r['candidate']['state'],r['status'],r['reason']]))+' |')
    lines+=['','© OpenStreetMap contributors · Geofabrik · OpenGolfAPI. Data licensed ODbL 1.0.']
    Path(f'reports/{stem}.md').write_text('\n'.join(lines)+'\n')
    Path('reports/LATEST-BUILD.md').write_text(f'[Latest phase-two report]({stem}.md)\n')
    links=''.join(f'<li><a href="{html.escape(c["file"])}">{html.escape(c["name"])} ({c["holes"]} holes)</a></li>' for c in index)
    Path('dist/index.html').write_text('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Crane Course data</title><style>body{font:17px system-ui;max-width:850px;margin:3rem auto;padding:0 1rem;line-height:1.6;background:#f5f8f5;color:#163d2c}a{color:#216047}</style><h1>Crane Course data</h1>'+f'<p><strong>{len(accepted)} courses pass the phase-two data checks.</strong></p><p>'+html.escape(report['limitations'])+'</p><p><a href="index.json">Download index</a> · <a href="report.json">Full validation report</a> · <a href="https://github.com/CraneCourse/crane-course-data">Source and documentation</a></p><ul>'+links+'</ul><footer>© OpenStreetMap contributors · Geofabrik · OpenGolfAPI · <a href="LICENSE-DATA">ODbL 1.0</a></footer></html>')
    assert sum(p.stat().st_size for p in Path('dist').rglob('*') if p.is_file())<100_000_000, 'Site exceeds project safety limit'
    # Preserve actual public API controls and extracted control inputs as regression fixtures.
    for cid in (LAKESIDE,KIAWAH):
        shutil.copyfile(f'work/validation/{cid}.json',f'tests/fixtures/extract-{cid}.json')
        shutil.copyfile(f'work/validation/control-{cid}.json',f'tests/fixtures/control-{cid}.json')
    print({k:v for k,v in report.items() if k not in {'rows'}},flush=True)

if __name__=='__main__': main()
