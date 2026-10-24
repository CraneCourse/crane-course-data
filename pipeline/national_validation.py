"""Bounded cloud scorecards and full data gates; never deploys or changes centers."""
from collections import Counter
from pathlib import Path
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone
import urllib.error
import urllib.request
from pipeline.common import cloud_only, read, write, now
from pipeline.layouts import separate
from pipeline.quality import check, equivalent
from pipeline.export import validate_document, validate_controls, SOURCES, export_order
from pipeline.reviewed_cards import facts, fetch
from pipeline.api_ledger import Budget, commit_files
from pipeline.national_border import REASONS as BORDER_REASON

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
    latest=latest_prior_run(os.environ['GEOMETRY_RUN'])
    if latest:
        assert previous==latest, f'Resume the latest recorded run {latest} for this geometry survey so its saved cards are reused'
    if previous:
        meta=get(previous); check_previous(meta,os.environ['GITHUB_REPOSITORY'])
        records['previous']={k:meta[k] for k in ('id','head_sha','created_at','html_url','path')}
    write(ROOT/'inputs.json',records)


RESUMABLE_EVENTS=('workflow_dispatch','schedule')


def check_previous(meta, repository):
    """A resumable predecessor: this workflow on main, finished, started by hand or by the daily schedule."""
    assert meta['head_repository']['full_name']==repository and meta['head_branch']=='main', 'Previous run is from another repository or branch'
    assert meta['event'] in RESUMABLE_EVENTS, f"Previous run event {meta['event']} cannot be resumed"
    assert meta['status']=='completed', 'Previous run has not finished'
    assert meta['path']=='.github/workflows/national-validation.yml', 'Previous run is a different workflow'


def latest_prior_run(geometry_run, reports=Path('reports')):
    """Latest run for this geometry survey, from finished reports OR acquisition summaries.

    Acquisition summaries are committed right after scorecard acquisition, so a run whose
    matching or report step later failed is still found. The request budget itself is
    enforced separately by the repository-wide ledger (pipeline/api_ledger.py).
    """
    runs=[]
    for pattern in ('national-validation-*.json','national-acquisition-*.json'):
        for path in reports.glob(pattern):
            r=read(path)
            if str(r['inputs']['geometry']['id'])==str(geometry_run): runs.append(int(r['runID']))
    return str(max(runs)) if runs else None


def scheduled_inputs(reports=Path('reports'), run_conclusion=None, at=None):
    """Inputs for the daily resume: continue the latest recorded run, or stop.

    Stops once that run's validation is complete, while its source reset is still in the
    future, or when that run did not succeed (a person should look before spending more).
    """
    records=[]
    for prefix in ('national-validation-','national-acquisition-'):
        for path in reports.glob(prefix+'*.json'):
            r=read(path); records.append((int(r['runID']),prefix,r))
    if not records: return {'proceed':'false'},'No recorded validation run to resume'
    latest=max(run for run,_,_ in records)
    mine={prefix:r for run,prefix,r in records if run==latest}
    record=next(iter(mine.values())); geometry=record['inputs']['geometry']
    values={'proceed':'false','geometry_run':str(geometry['id']),'geometry_sha':geometry['head_sha'],
            'previous_run':str(latest),'request_budget':'500'}
    validation=mine.get('national-validation-')
    if validation and validation['complete']: return values,f'Run {latest} completed validation; nothing to resume'
    reset=(validation or record).get('resumeAfterUTC')
    if reset and datetime.fromisoformat(reset)>(at or datetime.now(timezone.utc)): return values,f'Source reset {reset} not reached'
    conclusion=run_conclusion(latest) if run_conclusion else None
    if conclusion!='success': return values,f'Run {latest} concluded {conclusion}; review before resuming'
    return dict(values,proceed='true'),f'Resuming run {latest}'


def resolve():
    if os.environ.get('GITHUB_EVENT_NAME')=='workflow_dispatch':
        values={'proceed':'true','geometry_run':os.environ['INPUT_GEOMETRY_RUN'],'geometry_sha':os.environ['INPUT_GEOMETRY_SHA'],
                'previous_run':os.environ.get('INPUT_PREVIOUS_RUN',''),'request_budget':os.environ.get('INPUT_REQUEST_BUDGET') or '500'}
        reason='Manual dispatch'
    else:
        def conclusion(run):
            req=urllib.request.Request(f'https://api.github.com/repos/{os.environ["GITHUB_REPOSITORY"]}/actions/runs/{run}',headers={
                'Authorization':'Bearer '+os.environ['GITHUB_TOKEN'],'Accept':'application/vnd.github+json','User-Agent':'Crane-Course-Data/1.0'})
            with urllib.request.urlopen(req,timeout=30) as response: return json.load(response)['conclusion']
        values,reason=scheduled_inputs(run_conclusion=conclusion)
    print(reason,values,flush=True)
    with open(os.environ['GITHUB_OUTPUT'],'a') as output:
        for k,v in values.items(): output.write(f'{k}={v}\n')


def border_status():
    """Border classification from pipeline.national_border, keyed by candidate ID."""
    return read(ROOT/'border.json')['rows']


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
    """OpenGolfAPI scorecards through the repository-wide ledger budget; no retries."""
    def __init__(self,budget):
        self.budget=budget

    @property
    def requests(self): return self.budget.requests
    @property
    def stopped(self): return self.budget.stopped
    @property
    def resume_after(self): return self.budget.resume_after

    def consume(self): self.budget.consume()

    def card(self,cid):
        self.consume()
        url=f'https://api.opengolfapi.org/v1/courses/{cid}/holes'
        metadata={'url':url,'fetchedAt':now(),'attempts':1}
        try:
            req=urllib.request.Request(url,headers={'User-Agent':'Crane-Course-Data/1.0','Accept':'application/json'})
            with urllib.request.urlopen(req,timeout=45) as response:
                body=response.read(5_000_001)
                metadata['rateHeaders']={k:v for k,v in response.headers.items() if 'ratelimit' in k.lower() or k.lower()=='retry-after'}
                self.budget.observe(response.headers)
            if len(body)>5_000_000: raise ValueError('Response exceeds safety limit')
            data=json.loads(body)
            if not isinstance(data,dict) or not isinstance(data.get('holes'),list): raise ValueError('Missing holes array')
            metadata.update(response={'holes':data['holes']},sha256=hashlib.sha256(body).hexdigest())
        except urllib.error.HTTPError as exc:
            self.budget.http_error(exc)
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
    previous={}; prior=Path('work/previous'); border=border_status()
    if os.environ.get('PREVIOUS_RUN','').strip():
        prior_inputs=read(prior/'inputs.json'); current_inputs=read(ROOT/'inputs.json')
        assert prior_inputs['geometry']==current_inputs['geometry'] and prior_inputs['baseline']==current_inputs['baseline']
        prior_acquisition=read(prior/'acquisition.json'); previous={r['candidate']['id']:r for r in prior_acquisition['rows']}
        assert set(previous)==set(files)
        resume_after=prior_acquisition.get('resumeAfterUTC')
        if resume_after: assert datetime.now(timezone.utc)>=datetime.fromisoformat(resume_after), 'Source Retry-After/reset not reached'
    reviewed=read('inputs/reviewed-scorecards.json'); client=Client(Budget())
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
        if border[cid]!='inside':
            # A possibly clipped search area cannot prove a complete layout; spend nothing.
            row.update(status='border-unresolved',reason=BORDER_REASON[border[cid]]); rows.append(row)
            print(cid,row['status'],flush=True); continue
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
            if isinstance(exc,urllib.error.HTTPError): client.budget.http_error(exc)
            row.update(status='incomplete',reason=f'{type(exc).__name__}: {exc}',source={k:v for k,v in record.items() if k!='response'})
        rows.append(row)
        write(ROOT/'acquisition.json',acquisition_state(rows,False,client,cached,resumed,official))
        print(cid,row['status'],flush=True)
    assert {r['candidate']['id'] for r in rows}==set(files)
    write(ROOT/'acquisition.json',acquisition_state(rows,all(r['status'] in ('scorecard-acquired','border-unresolved') for r in rows),client,cached,resumed,official))


def need():
    """Requests acquisition can make: unresolved-free candidates without a saved, reused or prior card."""
    report,files=geometry_inputs(); border=border_status(); frozen=Path('work/frozen/work/validation')
    previous={}
    if os.environ.get('PREVIOUS_RUN','').strip():
        previous={r['candidate']['id']:r for r in read(Path('work/previous')/'acquisition.json')['rows']}
    reviewed=read('inputs/reviewed-scorecards.json')
    count=sum(1 for cid in files if border[cid]=='inside' and previous.get(cid,{}).get('status')!='scorecard-acquired'
              and (cid in reviewed or not (frozen/f'{cid}.json').exists()))
    return min(count,int(os.environ.get('REQUEST_BUDGET') or '500'))


def acquisition_state(rows,complete,client,cached,resumed,official):
    b=client.budget
    return {'rows':rows,'complete':complete,'requests':client.requests,'budget':b.budget,'reservation':{'key':b.key,'day':b.day,'granted':b.budget},
            'stopped':client.stopped,'cachedCards':cached,'resumedCards':resumed,'resumeAfterUTC':client.resume_after,'officialSources':official}


def summarize():
    """Commit counts and remaining IDs right after acquisition, before matching can fail."""
    if not (ROOT/'acquisition.json').exists():
        print('No acquisition state to summarize',flush=True); return
    a=read(ROOT/'acquisition.json')
    summary={'runID':os.environ['GITHUB_RUN_ID'],'sourceCommit':os.environ['GITHUB_SHA'],'builtAt':now(),'inputs':read(ROOT/'inputs.json'),
        'complete':a['complete'],'counts':dict(Counter(r['status'] for r in a['rows'])),'requests':a['requests'],'reservation':a['reservation'],
        'stopped':a['stopped'],'resumeAfterUTC':a.get('resumeAfterUTC'),'cachedCards':a['cachedCards'],'resumedCards':a['resumedCards'],
        'remainingIDs':sorted(r['candidate']['id'] for r in a['rows'] if r['status']=='incomplete'),
        'note':'Scorecard bodies remain in the national-acquisition artifact; resume with previous_run set to this run.'}
    name=f'reports/national-acquisition-{os.environ["GITHUB_RUN_ID"]}.json'
    commit_files(lambda root: write(root/name,summary),f'Record national scorecard acquisition {os.environ["GITHUB_RUN_ID"]}')


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
        if source['status']=='border-unresolved':
            rows.append(dict(row,status='border-unresolved',reason=source['reason'])); continue
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


def feature_partition_differs(a, b):
    """Recognize only a repartition of unchanged optional feature geometry.

    This changes failure classification, never acceptance: a recognized course is
    withheld. Missing/changed features, hole fields, numbering and scorecards still
    raise the existing execution/parity error. Frozen controls still must pass.
    """
    if not isinstance(a, list) or not isinstance(b, list) or not a or len(a) != len(b):
        return False
    if not all(isinstance(h, dict) and isinstance(h.get('features'), list) for h in a + b):
        return False
    core = lambda holes: [{k: v for k, v in h.items() if k != 'features'} for h in holes]
    if not equivalent(core(a), core(b)):
        return False
    def feature_keys(holes):
        # The reference omits nil optional rings, as equivalent() already allows.
        return [json.dumps({k: v for k, v in f.items()
                            if not (k == 'interiorRings' and v is None)}, sort_keys=True)
                for h in holes for f in h['features']]
    # Exact source vertices and all attributes must survive, with multiplicity.
    return sorted(feature_keys(a)) == sorted(feature_keys(b))


def full_gate(payload,ref,built,version):
    py=payload['python']
    assert py['pass']==ref['pass'], 'Swift/Python decision mismatch'
    if not py['pass']: return None,py['reason']
    if not equivalent(py['holes'],ref['holes']):
        if feature_partition_differs(py['holes'], ref['holes']):
            # Both implementations retained identical holes and feature geometry,
            # but assigned a feature to different holes. Reject this course; do
            # not choose either result or relax the full-Hole parity requirement.
            return None, 'Swift/Python feature assignment differs; course withheld'
        raise AssertionError('Swift/Python full Hole mismatch')
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
    by_id={r['candidate']['id']:r for r in rows}; border=border_status()
    for row in report['rows']:
        cid=row['candidate']['id']
        row['geometryStage']=row['status']
        if cid in by_id: row.update(by_id[cid])
        elif row['status']=='geometry-withheld' and border.get(cid) in BORDER_REASON:
            # A possibly clipped search is unresolved, not evidence of absent mapping.
            row.update(status='border-unresolved',reason=BORDER_REASON[border[cid]])
        row['border']=border.get(cid)
    complete=acquisition['complete'] and not any(r['status']=='incomplete' for r in rows)
    provenance=scorecard_provenance(fresh_ready,acquisition)
    facilities=sorted({read(f'work/validation/{cid}.json')['layoutResolution']['boundaryID'] for cid in fresh_ready})
    result={'runID':os.environ['GITHUB_RUN_ID'],'sourceCommit':os.environ['GITHUB_SHA'],'builtAt':built,'complete':complete,
        'inputs':read(ROOT/'inputs.json'),'geometryCounts':report['counts'],'counts':dict(Counter(r['status'] for r in report['rows'])),
        'requestCount':acquisition['requests'],'requestBudget':acquisition['budget'],'cachedCards':acquisition['cachedCards'],'resumedCards':acquisition['resumedCards'],'reservation':acquisition['reservation'],'resumeAfterUTC':acquisition.get('resumeAfterUTC'),
        'freshGeometryFullDataPassCount':len(fresh_ready),'freshPassDistinctBoundaryCount':len(facilities),'freshPassScorecardProvenance':provenance,
        'frozenPublishedControlsPassCount':len(floor),'borderUnresolvedCount':sum(r['status']=='border-unresolved' for r in report['rows']),
        'geometryCandidateDirectoryEntries':report['counts'].get('geometry-candidate',0),
        'geometryCandidateDistinctBoundaries':len({r['boundaryID'] for r in report['rows'] if r.get('geometryStage')=='geometry-candidate' and r.get('boundaryID')}),
        'combinedDistinctCandidateCount':len(docs),'newDistinctValidatedCount':len(set(docs)-floor),
        'existingPublishedIDs':sorted(floor),'candidateValidatedIDs':sorted(docs),'freshPassedIDs':sorted(fresh_ready),
        'existingPublishedFreshGeometryNotPassed':sorted(floor-fresh_ready),'fieldVerifiedCount':0,
        'stateCounts':{s:dict(Counter(r['status'] for r in report['rows'] if r['candidate']['state']==s)) for s in sorted(report['stateCounts'])},
        'controls':controls,'errors':global_errors,'rows':report['rows'],'limitations':LIMITATIONS}
    stem='national-validation-'+os.environ['GITHUB_RUN_ID']; write('reports/'+stem+'.json',result)
    lines=report_lines(result,complete,acquisition)
    for s,counts in result['stateCounts'].items():
        lines.append(f"| {s} | {sum(counts.values())} | {counts.get('full-data-gates-passed',0)} | {counts.get('border-unresolved',0)} | {sum(counts.values())-counts.get('full-data-gates-passed',0)-counts.get('incomplete',0)-counts.get('border-unresolved',0)} | {counts.get('incomplete',0)} |")
    Path('reports/'+stem+'.md').write_text('\n'.join(lines)+'\n')
    Path('reports/LATEST-NATIONAL-VALIDATION.md').write_text(f'[Latest national validation]({stem}.md)\n')
    assert not global_errors, 'Validation execution/parity errors retained in report'


def scorecard_provenance(ids,acquisition):
    """How each fresh pass got its card, with source-date ranges: reused cards keep old dates."""
    groups={}
    for row in acquisition['rows']:
        if row['candidate']['id'] not in ids: continue
        source=row.get('source',{})
        kind='reused-published-card' if source.get('reusedFromRun') else 'reviewed-official-card' if source.get('type')=='reviewed-official-scorecard' else 'fetched-or-resumed'
        g=groups.setdefault(kind,{'count':0,'earliestFetchedAt':None,'latestFetchedAt':None}); g['count']+=1
        date=source.get('fetchedAt')
        if date:
            g['earliestFetchedAt']=min(filter(None,[g['earliestFetchedAt'],date]))
            g['latestFetchedAt']=max(filter(None,[g['latestFetchedAt'],date]))
    return groups


def report_lines(result,complete,acquisition):
    fresh=result['freshGeometryFullDataPassCount']; boundaries=result['freshPassDistinctBoundaryCount']
    stale=result['existingPublishedFreshGeometryNotPassed']; remaining=result['counts'].get('incomplete',0)
    provenance='; '.join(f"{k}: {v['count']} (card dates {(v['earliestFetchedAt'] or '?')[:10]} to {(v['latestFetchedAt'] or '?')[:10]})"
                         for k,v in sorted(result['freshPassScorecardProvenance'].items())) or 'none'
    return ['# National course data validation','',
        f"**Newly surveyed geometry passing every data gate: {fresh} layouts** ({boundaries} distinct course boundaries). "
        'Layouts are not necessarily facilities: 27-hole combinations and alias listings can share a boundary.','',
        f"Scorecards behind those passes: {provenance}. Reused cards keep their original source dates.",'',
        f"Validation complete: **{complete}**. Still incomplete: **{remaining}** directory entries"
        +(f"; source rate limit until {result['resumeAfterUTC']}" if result.get('resumeAfterUTC') else '')+'. '
        f"Border-unresolved (search area may be clipped by its state extract; not absent mapping): **{result['borderUnresolvedCount']}**.",'',
        f"Separately: the frozen published controls retained {result['frozenPublishedControlsPassCount']}/38 on their recorded inputs; "
        f"{len(stale)} of them did not pass on current geometry ({', '.join(stale) or 'none'}). "
        f"The candidate export folder holds {result['combinedDistinctCandidateCount']} files (fresh passes plus frozen controls); it is not a supported-course count. Published catalog remains **38**.",'',
        f"Preliminary geometry candidates counted {result['geometryCandidateDirectoryEntries']} directory entries (aliases counted separately) "
        f"across {result['geometryCandidateDistinctBoundaries']} distinct boundaries.",'',
        f"Requests this run: {acquisition['requests']}/{acquisition['budget']} reserved in ledger entry `{acquisition['reservation']['key']}`. "
        f"Field-verified courses added: **0**. Data gates do not establish imagery availability or on-course accuracy.",'',
        f"Run: https://github.com/CraneCourse/crane-course-data/actions/runs/{os.environ['GITHUB_RUN_ID']}",'',LIMITATIONS,'',
        '| State | Directory | Full gates passed (fresh) | Border-unresolved | Other / withheld | Incomplete |','|---|---:|---:|---:|---:|---:|']


def main():
    cloud_only(); command=sys.argv[1]
    if command=='verify': verify()
    elif command=='acquire': acquire()
    elif command=='summarize': summarize()
    elif command=='resolve': resolve()
    elif command=='need': print(need())
    elif command=='finish': finish()
    elif command=='controls':
        Path('work/validation').mkdir(parents=True,exist_ok=True)
        for p in (ROOT/'baseline').glob('*.json'):
            shutil.copyfile(p,Path('work/validation')/(p.name if p.name.startswith('control-') else 'baseline-'+p.name))
    else: prepare_shard(int(command))
if __name__=='__main__': main()
