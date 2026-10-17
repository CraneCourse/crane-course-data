"""Cloud-only audit of source identities, boundaries and scorecard provenance."""
import sys, os, time, json, hashlib, urllib.request, urllib.error
from pathlib import Path
from shapely.geometry import LineString, mapping, Point
from shapely.ops import transform
from pyproj import Transformer
from pipeline.common import cloud_only, read, write, now, sample, state_for
from pipeline.extract import GolfHandler
from pipeline.match import valid_way, number, distance, center, usable_yards, yardage_error
from pipeline.full_match import match, geometry
from pipeline.quality import identity_error, check
from pipeline.build_state import KINDS
from pipeline.export import KIAWAH


class AuditHandler(GolfHandler):
    def __init__(self):
        super().__init__()
        self.relations=[]

    def way(self,w):
        before=len(self.ways)
        super().way(w)
        if len(self.ways)>before:
            self.ways[-1]['tags']={t.k:t.v for t in w.tags if t.k in
                {'golf','ref','name','par','golf:course','course','distance','length','source','source:ref','alt_name','old_name'}}

    def relation(self,r):
        if r.tags.get('golf') or r.tags.get('leisure')=='golf_course':
            self.relations.append({'id':'relation/'+str(r.id),'tags':{t.k:t.v for t in r.tags if t.k in
                {'type','name','golf','ref','leisure','course','golf:course'}},
                'members':[{'id':('way/' if m.type=='w' else 'relation/' if m.type=='r' else 'node/')+str(m.ref),'role':m.role} for m in r.members]})


def details():
    """Course details through the repository-wide ledger; no retries after HTTP 429."""
    from pipeline.api_ledger import Budget
    cloud_only(); budget=Budget()
    baseline=read('reports/build-2026-10-16-38973050174.json')
    for row in baseline['rows']:
        if row['status'] in ('data-ready','outside-state-scope'): continue
        c=row['candidate']; url='https://api.opengolfapi.org/v1/courses/'+c['id']
        result={'url':url,'fetchedAt':now()}
        for attempt in range(2):
            budget.consume()
            try:
                req=urllib.request.Request(url,headers={'User-Agent':'Crane-Course-Data/1.0','Accept':'application/json'})
                with urllib.request.urlopen(req,timeout=30) as response:
                    budget.observe(response.headers)
                    body=response.read(5_000_001)
                if len(body)>5_000_000: raise ValueError('Response exceeds limit')
                data=json.loads(body)
                # Public course facts only. No contact, player, nearby-place or account data.
                allowed={'id','course_name','name','city','state','latitude','longitude','lat','lon','holes','scorecard','tees','tee_boxes','hole_count','num_holes','par','par_total','total_yardage','osm_id','osm_type','source','sources','provenance','updated_at'}
                result.update(response={k:v for k,v in data.items() if k in allowed},responseKeys=sorted(data),sha256=hashlib.sha256(body).hexdigest())
                break
            except urllib.error.HTTPError as exc:
                result['error']=f'HTTP {exc.code}; no retries'; budget.http_error(exc); break
            except (urllib.error.URLError,ValueError,TimeoutError) as exc:
                result['error']=str(exc)
                if attempt==0: time.sleep(5)
        if 'response' in result: result.pop('error',None)
        write('work/details/'+c['id']+'.json',result)
        print('DETAIL',c['name'],'received' if 'response' in result else result['error'],flush=True)


def audit_state(state):
    cloud_only()
    h=AuditHandler(); h.apply_file(f'work/extracts/{state}/golf.osm.pbf',locations=True,idx='flex_mem')
    baseline={r['candidate']['id']:r for r in read('reports/build-2026-10-16-38973050174.json')['rows']}
    candidates=[c for c in sample() if state_for(c)==state]
    if state=='south-carolina': candidates.append(read('inputs/kiawah-control.json'))
    rows=[]
    for c in candidates:
        cid=c['id']; old=read(f'work/validation/{cid}.json')
        cards=old['scorecard']['holes']; origin=(c['center']['lon'],c['center']['lat'])
        project=Transformer.from_crs('EPSG:4326',f'+proj=aeqd +lat_0={origin[1]} +lon_0={origin[0]} +datum=WGS84 +units=m',always_xy=True).transform
        ways=[w for w in h.ways if w['tags'].get('golf') in KINDS and valid_way(w,origin)]
        areas=[]; trials=[]
        for a in h.areas:
            b=a['geometry'].bounds
            if not (b[0]-.02<=origin[0]<=b[2]+.02 and b[1]-.02<=origin[1]<=b[3]+.02): continue
            area=transform(project,a['geometry']); d=area.distance(Point(0,0))
            if d>500: continue
            meta={'id':a['id'],'name':a['name'],'distanceMeters':round(d,1),'areaMeters2':round(area.area)}
            link={'selectedID':a['id'],'method':'named-boundary-audit','confidence':'research','candidates':[meta]}
            identity=identity_error(c,link)
            area_ways=[w for w in ways if area.buffer(100).covers(transform(project,LineString(w['points'])))]
            meta.update(identityError=identity,geometry=mapping(area),paths=[{'id':w['id'],'tags':w['tags'],
                'yards':round(sum(distance(a,b) for a,b in zip(w['points'],w['points'][1:]))*1.0936133,1)} for w in area_ways if w['tags'].get('golf')=='hole'])
            areas.append(meta)
            if identity: continue
            for margin in (100,0):
                selected=[w for w in area_ways if w['tags'].get('golf')!='hole' or area.buffer(margin).covers(transform(project,LineString(w['points'])))]
                result=match(cards,selected,origin)
                payload=dict(old,ways=selected,geometry=geometry(selected),python=result,boundary=mapping(area),association=link)
                error=check(payload,result['holes'],0) if result['pass'] else result['reason']
                trial={'boundaryID':a['id'],'pathMarginMeters':margin,'matching':{k:v for k,v in result.items() if k!='holes'},'qualityError':error}
                trials.append(trial)
                if error is None:
                    write(f'work/layout-candidates/{cid}-{a["id"].replace("/","_")}-{margin}.json',payload)
        detail_path=Path(f'work/details/{cid}.json')
        detail=read(detail_path) if detail_path.exists() else None
        relevant_ids={w['id'] for w in ways}
        relations=[r for r in h.relations if any(m['id'] in relevant_ids for m in r['members'])]
        row={'candidate':c,'baselineStatus':baseline[cid]['status'],'baselineReason':baseline[cid]['reason'],
             'scorecard':cards,'scorecardError':yardage_error(cards),'detail':detail,
             'boundaries':areas,'relations':relations,'trials':trials}
        write(f'work/layout-evidence/{cid}.json',dict(old,ways=ways,boundaries=areas,relations=relations,detail=detail))
        rows.append(row)
        print('AUDIT',c['name'],'boundaries',[(a['name'],a['identityError']) for a in areas],
              'trials',[(t['pathMarginMeters'],t['qualityError']) for t in trials],flush=True)
    write(f'work/layout-results/{state}.json',{'rows':rows,'state':state})


def report():
    cloud_only()
    rows=[r for p in Path('work/layout-results').glob('*.json') for r in read(p)['rows']]
    expected={c['id'] for c in sample() if state_for(c)}|{KIAWAH}
    assert {r['candidate']['id'] for r in rows}==expected
    recovered=[r for r in rows if r['baselineStatus']!='data-ready' and any(t['qualityError'] is None for t in r['trials'])]
    data={'runID':os.environ['GITHUB_RUN_ID'],'sourceCommit':os.environ['GITHUB_SHA'],'builtAt':now(),
          'potentialRecoveries':[r['candidate'] for r in recovered],'rows':rows,
          'limitations':'Audit only: multiple possible layouts, Swift parity, Watch budget, scorecard provenance and publication approval gates still apply.'}
    stem=f'layout-audit-{data["builtAt"][:10]}-{data["runID"]}'
    write(f'reports/{stem}.json',data)
    lines=['# Layout and scorecard source audit','',data['limitations'],'',
           f'Potential boundary-only recoveries: {len(recovered)} (not yet published).','',
           '| Course | Existing failure | Passing boundary trials |','|---|---|---|']
    for r in sorted(rows,key=lambda r:r['candidate']['name']):
        passes=[t for t in r['trials'] if t['qualityError'] is None]
        if r['baselineStatus']!='data-ready':
            lines.append('| '+' | '.join(str(x).replace('|','/').replace('\n',' ') for x in
                [r['candidate']['name'],r['baselineReason'],[(t['boundaryID'],t['pathMarginMeters']) for t in passes]])+' |')
    Path(f'reports/{stem}.md').write_text('\n'.join(lines)+'\n')
    Path('reports/LATEST-LAYOUT.md').write_text(f'[Layout source audit]({stem}.md)\n')
    print('POTENTIAL RECOVERIES',data['potentialRecoveries'],flush=True)


if __name__=='__main__':
    if sys.argv[1]=='details': details()
    elif sys.argv[1]=='report': report()
    else: audit_state(sys.argv[1])
