"""Replay the cloud audit through conservative layout/source corrections."""
import os, json, urllib.request
from pathlib import Path
from pipeline.common import cloud_only, read, write, now
from pipeline.layouts import separate
from pipeline.reviewed_cards import facts, fetch
from pipeline.quality import check

AUDIT_RUN='38976460499'

def verify():
    cloud_only()
    request=urllib.request.Request('https://api.github.com/repos/'+os.environ['GITHUB_REPOSITORY']+'/actions/runs/'+AUDIT_RUN,
        headers={'Authorization':'Bearer '+os.environ['GITHUB_TOKEN'],'Accept':'application/vnd.github+json','User-Agent':'Crane-Course-Data/1.0'})
    with urllib.request.urlopen(request,timeout=30) as response: run=json.load(response)
    assert run['head_repository']['full_name']==os.environ['GITHUB_REPOSITORY'] and run['head_branch']=='main'
    assert run['path']=='.github/workflows/layout.yml' and run['event']=='workflow_dispatch' and run['conclusion']=='success'
    assert run['head_sha']=='c3f26c3aa477c7d3b4d97f9fbf3d08a47096decf'
    write('work/layout-input-run.json',{k:run[k] for k in ('id','head_sha','path','html_url')})


def prepare():
    cloud_only()
    baseline=read('reports/build-2026-10-16-38973050174.json')
    ready={r['candidate']['id'] for r in baseline['rows'] if r['status']=='data-ready'}
    research=os.environ.get('PUBLISH')!='true'
    reviewed=read('inputs/reviewed-scorecards.json')
    source_evidence={cid:fetch(r,research=research) for cid,r in reviewed.items()}
    files=sorted(Path('work/layout-evidence').glob('*.json'))
    expected={r['candidate']['id'] for r in baseline['rows'] if r['status']!='outside-state-scope'}
    assert {f.stem for f in files}==expected, 'Missing or unexpected layout audit inputs'
    rows=[]
    for file in files:
        p=read(file); cid=p['candidate']['id']
        if cid in ready: continue  # Original exports retain their exact fixtures.
        cards=facts(reviewed[cid]) if cid in reviewed else None
        result,error=separate(p,p['boundaries'],cards)
        if result is not None:
            error=check(result,result['python']['holes'],0) if result['python']['pass'] else result['python']['reason']
        row={'candidate':p['candidate'],'resolutionError':error,'usedOfficialCard':cid in reviewed}
        if error is None:
            if cid in reviewed: result['scorecardSource']=source_evidence[cid]
            result.pop('detail',None); result.pop('boundaries',None); result.pop('relations',None)
            write(f'work/validation/{cid}.json',result)
            row['resolution']=result['layoutResolution']; row['association']=result['association']
        rows.append(row)
        print('RESOLVE',p['candidate']['name'],error or 'All Python data checks pass',flush=True)
    write('work/layout-resolution.json',{'rows':rows,'officialSources':source_evidence,'research':research,'auditRun':AUDIT_RUN})


def report():
    cloud_only()
    build=read('dist/report.json'); resolution=read('work/layout-resolution.json')
    baseline={r['candidate']['id'] for r in read('reports/build-2026-10-16-38973050174.json')['rows'] if r['status']=='data-ready'}
    ready={r['candidate']['id'] for r in build['rows'] if r['status']=='data-ready'}
    assert baseline<=ready, 'Existing verified courses regressed; do not publish'
    added=[r for r in build['rows'] if r['candidate']['id'] in ready-baseline]
    resolution.update(runID=os.environ['GITHUB_RUN_ID'],sourceCommit=os.environ['GITHUB_SHA'],builtAt=now(),
                      baselineCount=len(baseline),dataReady=len(ready),validatedCourseIDs=sorted(ready),additionalCourses=[r['candidate'] for r in added],
                      limitations='Data checks only, not field verification or app integration. No estimated yardages or inferred hole numbering. Official source corrections require content hashes and an unexpired review before publication.')
    stem='layout-resolution-'+os.environ['GITHUB_RUN_ID']
    write('reports/'+stem+'.json',resolution)
    lines=['# Course-layout and scorecard corrections','',f"{len(ready)} distinct courses pass the full data checks: {len(ready-baseline)} additional, all {len(baseline)} earlier courses retained.",'',resolution['limitations'],'',
           'This run is '+('research only; publication disabled.' if resolution['research'] else 'eligible for publication after every workflow job passes.'),'',
           '## Additional courses','']+[f"- {r['candidate']['name']} ({r['candidate']['state']})" for r in added]
    lines+=['','## Remaining resolution failures','','| Course | Reason |','|---|---|']
    for row in resolution['rows']:
        if row['resolutionError']: lines.append('| '+row['candidate']['name'].replace('|','/')+' | '+row['resolutionError'].replace('|','/')+' |')
    Path('reports/'+stem+'.md').write_text('\n'.join(lines)+'\n')
    Path('reports/LATEST-LAYOUT-RESOLUTION.md').write_text(f'[Latest layout resolution]({stem}.md)\n')
    for cid in ('9b9891db-3b45-48db-892f-149601b79d52','86620a66-2f67-409e-8aaa-8d503d820631','5b0dfcec-77bf-430e-bbe5-747e0199c565','2d201c28-68ec-4897-b7b1-8f19c8984d26','9379add5-9103-43fb-a659-b6c617b65eb0'):
        if cid in ready-baseline:
            original=read('work/layout-evidence/'+cid+'.json')
            write('tests/fixtures/layouts/'+cid+'.json',{'input':original,
                'expectedMatches':read('work/validation/'+cid+'.json')['python']['matchedHoles']})
    print('ADDITIONAL COURSES',resolution['additionalCourses'])
    print('OFFICIAL SOURCE EVIDENCE',resolution['officialSources'])

if __name__=='__main__':
    import sys
    {'verify':verify,'prepare':prepare,'report':report}[sys.argv[1]]()
