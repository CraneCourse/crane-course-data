"""Bounded cloud scorecards and full data gates; never deploys or changes centers."""
from collections import Counter
from pathlib import Path
import hashlib
import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
import urllib.error
import urllib.request
from pipeline.common import cloud_only, read, write, now
from pipeline.layouts import separate
from pipeline.quality import check, equivalent
from pipeline.export import validate_document, validate_controls, SOURCES, export_order
from pipeline.reviewed_cards import facts, fetch

BASELINE_RUN='38981324295'
BASELINE_SHA='ad8c6ebf5f4042bb471f796a69aa6e1b33fd2ffe'
ROOT=Path('work/national-validation')
SHARDS=4
LIMITATIONS=('Full data gates are distinct from field verification, app integration and imagery/download testing. '
              'This research workflow never publishes. Existing 38 published identities remain unchanged; frozen controls and newly acquired geometry are reported separately. '
              'No inferred numbering, guessed geometry or changed green centers. Budget or acquisition failures are incomplete, not unsupported-course findings.')


def verify():
    def get(run):
        assert run.isascii() and run.isdigit()
        req=urllib.request.Request(f'https://api.github.com/repos/{os.environ["GITHUB_REPOSITORY"]}/actions/runs/{run}',headers={
            'Authorization':'Bearer '+os.environ['GITHUB_TOKEN'],'Accept':'application/vnd.github+json','User-Agent':'Crane-Course-Data/1.0'})
        with urllib.request.urlopen(req,timeout=30) as response: return json.load(response)
    records={}
    for name,run,sha,path in [('geometry',os.environ['GEOMETRY_RUN'],os.environ['GEOMETRY_SHA'],'.github/workflows/national.yml'),
                              ('baseline',BASELINE_RUN,BASELINE_SHA,'.github/workflows/resolve.yml')]:
        meta=get(run)
        assert meta['head_repository']['full_name']==os.environ['GITHUB_REPOSITORY'] and meta['head_branch']=='main'
        assert meta['event']=='workflow_dispatch' and meta['status']=='completed' and meta['conclusion']=='success'
        assert meta['path']==path and meta['head_sha']==sha
        records[name]={k:meta[k] for k in ('id','head_sha','created_at','html_url','path')}
    previous=os.environ.get('PREVIOUS_RUN','').strip()
    prior_reports=[read(p) for p in Path('reports').glob('national-validation-*.json')]
    prior_reports=[r for r in prior_reports if str(r['inputs']['geometry']['id'])==os.environ['GEOMETRY_RUN']]
    if prior_reports:
        latest=max(prior_reports,key=lambda r:r['builtAt'])
        assert previous==str(latest['runID']), 'Resume the latest recorded validation run; a fresh dispatch cannot reset the request ledger'
    if previous:
        meta=get(previous)
        assert meta['head_repository']['full_name']==os.environ['GITHUB_REPOSITORY'] and meta['head_branch']=='main'
        assert meta['event']=='workflow_dispatch' and meta['status']=='completed'
        assert meta['path']=='.github/workflows/national-validation.yml'
        records['previous']={k:meta[k] for k in ('id','head_sha','created_at','html_url','path')}
    write(ROOT/'inputs.json',records)


def geometry_inputs():
    report=read(f'reports/national-geometry-{os.environ["GEOMETRY_RUN"]}.json')
    assert report['complete'] and report['sourceCommit']==os.environ['GEOMETRY_SHA']
    assert report['candidateCount']==len(report['rows'])==16776
    assert len({r['candidate']['id'] for r in report['rows']})==16776
    ids={r['candidate']['id'] for r in report['rows'] if r['status']=='geometry-candidate'}
    files={p.stem:p for p in Path('work/national/candidates').glob('*.json')}
    assert set(files)==ids, 'Missing, extra or duplicate geometry inputs'
    by_id={r['candidate']['id']:r['candidate'] for r in report['rows']}
    for cid,path in files.items(): assert read(path)['candidate']==by_id[cid], 'Geometry candidate identity differs from survey report'
    return report,files


def baseline_ids():
    result=read(f'reports/layout-resolution-{BASELINE_RUN}.json')
    assert result['sourceCommit']==BASELINE_SHA and not result['research']
    ids=set(result['validatedCourseIDs']); assert len(ids)==38
    return ids


class Client:
    """One run-wide budget includes official checks; no retry multiplication."""
    def __init__(self,budget,pause=time.sleep,usage=None):
        assert 1<=budget<=800
        self.budget=budget; self.requests=0; self.pause=pause; self.stopped=None
        self.usage=dict(usage or {}); self.resume_after=None

    def rate_stop(self,headers):
        headers={k.lower():v for k,v in headers.items()}
        reset=headers.get('x-ratelimit-reset'); retry=headers.get('retry-after')
        current=datetime.now(timezone.utc); targets=[]
        if reset:
            try:
                targets.append(datetime.fromtimestamp(float(reset),timezone.utc))
            except (ValueError,TypeError,OverflowError,OSError):
                try:
                    parsed=datetime.fromisoformat(reset.replace('Z','+00:00'))
                    targets.append(parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc))
                except (ValueError,TypeError): pass
        if retry:
            try:
                parsed=current+timedelta(seconds=int(retry)) if retry.isdigit() else parsedate_to_datetime(retry)
                targets.append(parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc))
            except (ValueError,TypeError,OverflowError): pass
        # Respect both headers when present; never choose the shorter wait.
        target=max(targets) if targets else (current+timedelta(days=1)).replace(hour=0,minute=0,second=0,microsecond=0)
        self.resume_after=target.isoformat()
        self.stopped='Source rate limit reached; resume only after '+self.resume_after

    def consume(self):
        if self.stopped: raise RuntimeError(self.stopped)
        if self.requests>=self.budget: raise RuntimeError('Shared request budget exhausted')
        day=datetime.now(timezone.utc).date().isoformat()
        if self.usage.get(day,0)>=800: raise RuntimeError('Shared UTC-day budget exhausted; wait for next source reset')
        self.pause(1.5); self.requests+=1; self.usage[day]=self.usage.get(day,0)+1

    def card(self,cid):
        self.consume()
        url=f'https://api.opengolfapi.org/v1/courses/{cid}/holes'
        metadata={'url':url,'fetchedAt':now(),'attempts':1}
        try:
            req=urllib.request.Request(url,headers={'User-Agent':'Crane-Course-Data/1.0','Accept':'application/json'})
            with urllib.request.urlopen(req,timeout=45) as response:
                body=response.read(5_000_001)
                metadata['rateHeaders']={k:v for k,v in response.headers.items() if 'ratelimit' in k.lower() or k.lower()=='retry-after'}
                if response.headers.get('X-RateLimit-Remaining')=='0': self.rate_stop(response.headers)
            if len(body)>5_000_000: raise ValueError('Response exceeds safety limit')
            data=json.loads(body)
            if not isinstance(data,dict) or not isinstance(data.get('holes'),list): raise ValueError('Missing holes array')
            metadata.update(response={'holes':data['holes']},sha256=hashlib.sha256(body).hexdigest())
        except urllib.error.HTTPError as exc:
            if exc.code==429: self.rate_stop(exc.headers)
            metadata['error']=f'HTTP {exc.code}; no retries'
        except (urllib.error.URLError,TimeoutError,ValueError) as exc: metadata['error']=str(exc)
        return metadata


def acquire():
    report,files=geometry_inputs(); floor=baseline_ids()
    frozen=Path('work/frozen/work/validation')
    assert all((frozen/f'{cid}.json').exists() for cid in floor)
    # Create the directory explicitly; never rely on artifact flattening.
    (ROOT/'baseline').mkdir(parents=True,exist_ok=True)
    for cid in floor: shutil.copyfile(frozen/f'{cid}.json',ROOT/'baseline'/f'{cid}.json')
    for path in frozen.glob('control-*.json'): shutil.copyfile(path,ROOT/'baseline'/path.name)
    previous={}; usage={}; prior=Path('work/previous')
    if os.environ.get('PREVIOUS_RUN','').strip():
        prior_inputs=read(prior/'inputs.json'); current_inputs=read(ROOT/'inputs.json')
        assert prior_inputs['geometry']==current_inputs['geometry'] and prior_inputs['baseline']==current_inputs['baseline']
        prior_acquisition=read(prior/'acquisition.json'); previous={r['candidate']['id']:r for r in prior_acquisition['rows']}
        assert set(previous)==set(files)
        usage=prior_acquisition['utcDayUsage']
        resume_after=prior_acquisition.get('resumeAfterUTC')
        if resume_after: assert datetime.now(timezone.utc)>=datetime.fromisoformat(resume_after), 'Source Retry-After/reset not reached'
    reviewed=read('inputs/reviewed-scorecards.json'); client=Client(int(os.environ.get('REQUEST_BUDGET','800')),usage=usage)
    rows=[]; cached=0; resumed=0; official={}
    # One sequential client for every state. Stable ordering favors existing IDs;
    # remaining IDs are round-robin by state for bounded-run geographic coverage.
    by_state={}
    for cid,path in sorted(files.items()): by_state.setdefault(read(path)['candidate']['state'],[]).append(cid)
    order=sorted(floor & files.keys())
    for group in by_state.values(): group[:]=[cid for cid in group if cid not in floor]
    while any(by_state.values()):
        for state in sorted(by_state):
            if by_state[state]: order.append(by_state[state].pop(0))
    for cid in order:
        candidate=read(files[cid])['candidate']; row={'candidate':candidate}
        record={}
        try:
            if previous.get(cid,{}).get('status')=='scorecard-acquired':
                record=read(prior/'scorecards'/f'{cid}.json'); assert 'response' in record
                if cid in reviewed: facts(reviewed[cid])
                resumed+=1
            elif cid in reviewed:
                client.consume(); source=fetch(reviewed[cid])  # Exact hash + expiry, even in research.
                record=dict(source,response={'holes':facts(reviewed[cid])})
                official[cid]=source
            elif (frozen/f'{cid}.json').exists():
                p=read(frozen/f'{cid}.json')
                record=dict(p['scorecardSource'],response=p['scorecard'],reusedFromRun=BASELINE_RUN)
                cached+=1
            else: record=client.card(cid)
            if 'response' not in record: raise RuntimeError(record.get('error','Scorecard acquisition failed'))
            row.update(status='scorecard-acquired',source={k:v for k,v in record.items() if k!='response'})
            write(ROOT/'scorecards'/f'{cid}.json',record)
        except Exception as exc:
            if isinstance(exc,urllib.error.HTTPError) and exc.code==429: client.rate_stop(exc.headers)
            row.update(status='incomplete',reason=f'{type(exc).__name__}: {exc}',source={k:v for k,v in record.items() if k!='response'})
        rows.append(row)
        write(ROOT/'acquisition.json',{'rows':rows,'complete':False,'requests':client.requests,'budget':client.budget,'stopped':client.stopped,'cachedCards':cached,'resumedCards':resumed,'utcDayUsage':client.usage,'resumeAfterUTC':client.resume_after,'officialSources':official})
        print(cid,row['status'],flush=True)
    assert {r['candidate']['id'] for r in rows}==set(files)
    write(ROOT/'acquisition.json',{'rows':rows,'complete':all(r['status']=='scorecard-acquired' for r in rows),
        'requests':client.requests,'budget':client.budget,'stopped':client.stopped,'cachedCards':cached,'resumedCards':resumed,'utcDayUsage':client.usage,'resumeAfterUTC':client.resume_after,'officialSources':official})


def prepare_payload(p,score):
    cards=score['response']['holes']
    p=dict(p,scorecard={'holes':cards},scorecardSource={k:v for k,v in score.items() if k!='response'})
    result,error=separate(p,p['boundaries'],cards)
    if result is None: return None,error
    result.pop('boundaries',None); result.pop('screen',None)
    py=result['python']
    error=check(result,py['holes'],0) if py['pass'] else py['reason']
    return result,error


def prepare_shard(shard):
    assert 0<=shard<SHARDS
    report,files=geometry_inputs(); acquisition=read(ROOT/'acquisition.json')
    assert len(acquisition['rows'])==len(files)
    sources={r['candidate']['id']:r for r in acquisition['rows']}
    rows=[]
    for cid in sorted(files):
        if int(cid.replace('-',''),16)%SHARDS!=shard: continue
        source=sources[cid]
        row={'candidate':source['candidate']}
        if source['status']!='scorecard-acquired':
            rows.append(dict(row,status='incomplete',reason=source['reason'])); continue
        try:
            p=read(files[cid]); score=read(ROOT/'scorecards'/f'{cid}.json')
            payload,error=prepare_payload(p,score)
            if payload is not None:
                write(f'work/validation/{cid}.json',payload)
                row['payloadPrepared']=True
            row.update(status='python-withheld' if error else 'python-candidate',reason=error or 'Full Python data checks pass; actual Swift/schema/Watch pending')
        except Exception as exc: row.update(status='incomplete',reason=f'{type(exc).__name__}: {exc}')
        rows.append(row)
    write(ROOT/'shards'/f'{shard}.json',{'shard':shard,'rows':rows})


def full_gate(payload,ref,built,version):
    py=payload['python']
    assert py['pass']==ref['pass'], 'Swift/Python decision mismatch'
    if not py['pass']: return None,py['reason']
    assert equivalent(py['holes'],ref['holes']), 'Swift/Python full Hole mismatch'
    error=check(payload,ref['holes'],ref['watchBytes'])
    if error: return None,error
    doc={'candidate':payload['candidate'],'holes':ref['holes'],'dataVersion':version,'builtAt':built,
         'sources':SOURCES+([payload['scorecardSource']['url']] if payload['scorecardSource'].get('type')=='reviewed-official-scorecard' else []),'numbering':'osm-mapped'}
    validate_document(doc,'schemas/course.schema.json')
    return doc,None


def finish():
    report,files=geometry_inputs(); floor=baseline_ids(); acquisition=read(ROOT/'acquisition.json')
    swift=read('work/swift.json'); built=now(); version=os.environ['GITHUB_RUN_ID']+'-'+os.environ['GITHUB_SHA'][:12]
    frozen={p.stem:read(p) for p in (ROOT/'baseline').glob('*.json') if not p.stem.startswith('control-')}
    assert set(frozen)==floor
    # Every published identity must still pass actual Swift and the existing gates.
    docs={}; seen={}; baseline_swift={}
    for cid,p in sorted(frozen.items()):
        ref=swift['baseline-'+cid]; baseline_swift[cid]=ref
        doc,error=full_gate(p,ref,built,version); assert not error and doc is not None,(cid,error)
        key=tuple((h['pathID'],h['greenID']) for h in p['python']['matchedHoles'])
        assert key not in seen; seen[key]=cid; docs[cid]=doc
    for cid in ('24925661-c5ad-4a89-87f2-b97a8f77fe83','2f8238b3-85d2-4d54-b1af-779dec846381'):
        baseline_swift['control-'+cid]=swift['control-'+cid]
    controls=validate_controls(baseline_swift,frozen)
    shards=[read(p) for p in (ROOT/'shards').glob('*.json')]
    assert len({s['shard'] for s in shards})==len(shards) and all(s['shard'] in range(SHARDS) for s in shards)
    rows=[r for s in shards for r in s['rows']]
    assert len({r['candidate']['id'] for r in rows})==len(rows)
    assert {r['candidate']['id'] for r in rows}<=set(files)
    missing=set(files)-{r['candidate']['id'] for r in rows}
    rows += [{'candidate':read(files[cid])['candidate'],'status':'incomplete','reason':'Validation shard/candidate did not finish'} for cid in sorted(missing)]
    assert len(rows)==len(files) and {r['candidate']['id'] for r in rows}==set(files)
    fresh_ready=set(); global_errors=[]
    for row in sorted(rows,key=lambda r:export_order(r['candidate']['id'],{},floor)):
        cid=row['candidate']['id']
        if not row.get('payloadPrepared'): continue
        try:
            p=read(f'work/validation/{cid}.json'); doc,error=full_gate(p,swift[cid],built,version)
            if error: row.update(status='data-withheld',reason=error); continue
            key=tuple((h['pathID'],h['greenID']) for h in p['python']['matchedHoles'])
            if key in seen and seen[key]!=cid:
                row.update(status='data-withheld',reason='Duplicate mapped course; represented by '+seen[key]); continue
            if cid in floor:
                original=tuple((h['pathID'],h['greenID']) for h in frozen[cid]['python']['matchedHoles'])
                if key!=original:
                    row.update(status='data-withheld',reason='Existing published identity changed path/green assignment; review required'); continue
            seen[key]=cid; docs[cid]=doc; fresh_ready.add(cid)
            row.update(status='full-data-gates-passed',reason='Actual Swift/Python, identity, polygon/center, schema and Watch gates pass',watchBytes=swift[cid]['watchBytes'])
        except Exception as exc:
            row.update(status='incomplete',reason=f'{type(exc).__name__}: {exc}')
            global_errors.append({'id':cid,'reason':row['reason']})
    assert floor<=docs.keys()
    # Reviewable candidate exports only: no Pages artifact/deployment or dist/ write.
    index=[]
    for cid,doc in sorted(docs.items()):
        path=ROOT/'candidate-export'/'courses'/f'{cid}.json'; write(path,doc)
        index.append(dict(doc['candidate'],holes=len(doc['holes']),tees=sorted(set.intersection(*(set(h['teeYardages']) for h in doc['holes']))),
            dataVersion=version,builtAt=built,file=f'courses/{cid}.json',sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    manifest={'dataVersion':version,'builtAt':built,'courses':index,'sources':SOURCES}
    validate_document(manifest,'schemas/index.schema.json'); write(ROOT/'candidate-export'/'index.json',manifest)
    by_id={r['candidate']['id']:r for r in rows}
    for row in report['rows']:
        cid=row['candidate']['id']
        row['geometryStage']=row['status']
        if cid in by_id: row.update(by_id[cid])
    complete=acquisition['complete'] and not any(r['status']=='incomplete' for r in rows)
    result={'runID':os.environ['GITHUB_RUN_ID'],'sourceCommit':os.environ['GITHUB_SHA'],'builtAt':built,'complete':complete,
        'inputs':read(ROOT/'inputs.json'),'geometryCounts':report['counts'],'counts':dict(Counter(r['status'] for r in report['rows'])),
        'requestCount':acquisition['requests'],'requestBudget':acquisition['budget'],'cachedCards':acquisition['cachedCards'],'resumedCards':acquisition['resumedCards'],'utcDayUsage':acquisition['utcDayUsage'],'resumeAfterUTC':acquisition.get('resumeAfterUTC'),
        'freshGeometryFullDataPassCount':len(fresh_ready),'frozenPublishedControlsPassCount':len(floor),
        'combinedDistinctCandidateCount':len(docs),'newDistinctValidatedCount':len(set(docs)-floor),
        'existingPublishedIDs':sorted(floor),'candidateValidatedIDs':sorted(docs),'freshPassedIDs':sorted(fresh_ready),
        'existingPublishedFreshGeometryNotPassed':sorted(floor-fresh_ready),'fieldVerifiedCount':0,
        'stateCounts':{s:dict(Counter(r['status'] for r in report['rows'] if r['candidate']['state']==s)) for s in sorted(report['stateCounts'])},
        'controls':controls,'errors':global_errors,'rows':report['rows'],'limitations':LIMITATIONS}
    stem='national-validation-'+os.environ['GITHUB_RUN_ID']; write('reports/'+stem+'.json',result)
    lines=['# National course data validation','',LIMITATIONS,'',
        f"Complete validation of screened candidates: **{complete}**. Fresh geometry inputs passing every data gate (recorded scorecards may be reused): **{len(fresh_ready)}**. Frozen published controls retained: **38/38**. Combined distinct reviewable candidate exports: **{len(docs)}** (**{len(set(docs)-floor)} new**). Published catalog remains **38**.",'',
        f"Requests: {acquisition['requests']}/{acquisition['budget']}; {acquisition['cachedCards']} existing public cards reused. Field-verified count added by this work: **0**.",'',
        f"Run: https://github.com/CraneCourse/crane-course-data/actions/runs/{os.environ['GITHUB_RUN_ID']}",'',
        'Frozen controls retain their recorded source dates. New geometry refresh failures do not replace earlier published data and are explicit in the JSON report. These totals cannot promise national field accuracy or imagery coverage.','',
        '| State | Directory | Full gates passed (fresh) | Other / withheld | Incomplete |','|---|---:|---:|---:|---:|']
    for s,counts in result['stateCounts'].items():
        lines.append(f"| {s} | {sum(counts.values())} | {counts.get('full-data-gates-passed',0)} | {sum(counts.values())-counts.get('full-data-gates-passed',0)-counts.get('incomplete',0)} | {counts.get('incomplete',0)} |")
    Path('reports/'+stem+'.md').write_text('\n'.join(lines)+'\n')
    Path('reports/LATEST-NATIONAL-VALIDATION.md').write_text(f'[Latest national validation]({stem}.md)\n')
    assert not global_errors, 'Validation execution/parity errors retained in report'


def main():
    cloud_only(); command=sys.argv[1]
    if command=='verify': verify()
    elif command=='acquire': acquire()
    elif command=='finish': finish()
    elif command=='controls':
        Path('work/validation').mkdir(parents=True,exist_ok=True)
        for p in (ROOT/'baseline').glob('*.json'):
            shutil.copyfile(p,Path('work/validation')/(p.name if p.name.startswith('control-') else 'baseline-'+p.name))
    else: prepare_shard(int(command))
if __name__=='__main__': main()
