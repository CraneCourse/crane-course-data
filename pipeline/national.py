"""Cloud-only national geometry inventory. No inferred routing or publication."""
from collections import Counter
from pathlib import Path
import json
import math
import os
import sys
import urllib.request
import uuid
from shapely import STRtree
from shapely.geometry import box, LineString
from pipeline.common import cloud_only, STATE_NAMES, read, write, now
from pipeline.extract import GolfHandler
from pipeline.build_state import KINDS
from pipeline.layouts import select_layout, nearby_boundaries
from pipeline.match import valid_way

INVENTORY_RUN='38981104633'
INVENTORY_SHA='dec9d728641ce1ec1a80d575e64b11a1e598a525'
EXTRACTS=dict(STATE_NAMES, DC='district-of-columbia')
ALL_STATES=set(EXTRACTS)|{'AK','HI'}
LIMITATIONS=('Geometry candidates are preliminary: explicit identity-compatible boundaries, unique 9/18 numbered hole paths and enough way greens only. '
              'No scorecard, full matching, polygon/center, actual Swift, schema, Watch, imagery-download or field verification is implied. '
              'Relation greens are recorded but never converted into invented ways. Unfinished states/candidates are incomplete, not absent mapping. '
              'AK/HI remain outside current app geography/imagery gates. Published catalog remains unchanged.')


def verify_run():
    repo=os.environ['GITHUB_REPOSITORY']
    request=urllib.request.Request(f'https://api.github.com/repos/{repo}/actions/runs/{INVENTORY_RUN}',headers={
        'Authorization':'Bearer '+os.environ['GITHUB_TOKEN'],'Accept':'application/vnd.github+json','User-Agent':'Crane-Course-Data/1.0'})
    with urllib.request.urlopen(request,timeout=30) as response: meta=json.load(response)
    assert meta['head_repository']['full_name']==repo and meta['head_branch']=='main'
    assert meta['event']=='workflow_dispatch' and meta['status']=='completed' and meta['conclusion']=='success'
    assert meta['head_sha']==INVENTORY_SHA and meta['path']=='.github/workflows/catalog.yml'
    write('work/national/input-run.json',{k:meta[k] for k in ('id','head_sha','created_at','html_url','path')})


def inventory():
    candidates=read('work/catalog/candidates.json')
    report=read(f'reports/catalog-inventory-{INVENTORY_RUN}.json')
    assert report['sourceCommit']==INVENTORY_SHA and str(report['runID'])==INVENTORY_RUN
    assert report['completeStatePaginationCount']==51
    assert len(candidates)==report['validUniqueDirectoryEntries']==16776
    assert len({c['id'] for c in candidates})==len(candidates), 'Duplicate inventory IDs'
    for c in candidates:
        uuid.UUID(c['id'])
        assert isinstance(c['name'],str) and c['name'].strip()
        lat,lon=c['center']['lat'],c['center']['lon']
        assert all(isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v) for v in (lat,lon))
        assert -90<=lat<=90 and -180<=lon<=180
    assert set(c['state'] for c in candidates)==ALL_STATES
    assert dict(Counter(c['state'] for c in candidates))==report['countsByState']
    return candidates


def verify_dc_extent(candidates):
    """The official extract polygon, not a state-name assumption, proves coverage."""
    import hashlib
    from shapely.geometry import Polygon, MultiPolygon, Point
    url='https://download.geofabrik.de/north-america/us/district-of-columbia.poly'
    with urllib.request.urlopen(url,timeout=45) as response: body=response.read(1_000_001)
    assert len(body)<=1_000_000
    lines=iter(body.decode('utf-8').splitlines()[1:]); polygons=[]; holes=[]
    for line in lines:
        name=line.strip()
        if name=='END': break
        points=[]
        for line in lines:
            if line.strip()=='END': break
            x,y=map(float,line.split()); points.append((x,y))
        if name.startswith('!'): holes.append(points)
        else: polygons.append(points)
    assert polygons
    area=MultiPolygon([Polygon(shell,[h for h in holes if Polygon(shell).covers(Polygon(h))]) for shell in polygons])
    assert area.is_valid
    anchors=[(-77.0365,38.8977),(-77.0091,38.8899),(-76.987,38.860),(-76.98,38.94),(-77.07,38.95)]
    assert all(area.covers(Point(p)) for p in anchors), 'District of Columbia extent does not establish representative DC coverage'
    dc=[c for c in candidates if c['state']=='DC']
    coverage={c['id']:area.covers(Point(c['center']['lon'],c['center']['lat'])) for c in dc}
    evidence={'url':url,'fetchedAt':now(),'sha256':hashlib.sha256(body).hexdigest(),
              'representativeDCAnchors':anchors,'directoryDCCenterCoverage':coverage}
    write('work/national/dc-extent.json',evidence)


def prepare():
    candidates=inventory()
    verify_dc_extent(candidates)
    extracts=sorted(set(EXTRACTS.values()))
    write('work/national/selection.json',{'extracts':extracts,'candidateCount':len(candidates),'stateToExtract':EXTRACTS,
        'inventoryRun':INVENTORY_RUN,'inventoryCommit':INVENTORY_SHA})
    with open(os.environ['GITHUB_OUTPUT'],'a') as output: output.write('extracts='+json.dumps(extracts)+'\n')


class SpatialIndex:
    """Conservative bbox superset; existing exact checks remain authoritative.

    In the current 24..50N geography, 0.1 degree exceeds 3.5 km even
    east/west at 50N. Intersect entire way bounds, never just its center.
    Boundary query exactly supersets nearby_boundaries' existing .02 check.
    """
    def __init__(self, ways, areas):
        self.ways=[]; boxes=[]
        for w in ways:
            points=w['points']
            if len(points)<2 or any(len(p)!=2 or any(not math.isfinite(v) for v in p) or not -180<=p[0]<=180 or not -90<=p[1]<=90 for p in points): continue
            self.ways.append(w); boxes.append(LineString(points).envelope)
        self.way_tree=STRtree(boxes)
        self.areas=areas; self.area_tree=STRtree([a['geometry'].envelope for a in areas])

    def query(self, candidate):
        c=candidate['center']; lon=c['lon']; lat=c['lat']
        ways=[self.ways[int(i)] for i in sorted(self.way_tree.query(box(lon-.1,lat-.1,lon+.1,lat+.1)))]
        areas=[self.areas[int(i)] for i in sorted(self.area_tree.query(box(lon-.02,lat-.02,lon+.02,lat+.02)))]
        return [w for w in ways if w['tags'].get('golf') in KINDS and valid_way(w,(lon,lat))],areas


def screen(candidate, index):
    c=candidate['center']
    row={'candidate':candidate,'stage':'directory-candidate'}
    if not (24<=c['lat']<=50 and -125<=c['lon']<=-66):
        return dict(row,status='outside-geography',reason='Outside existing app geography limits'),None
    ways,areas=index.query(candidate)
    boundaries=nearby_boundaries(candidate,areas)
    selections=[]; errors=[]
    for count in (9,18):
        selection,error=select_layout(candidate,ways,boundaries,count)
        if selection: selections.append((count,selection))
        else: errors.append(error)
    row.update(nearbyBoundaryCount=len(boundaries),wayCount=len(ways))
    if len(selections)!=1:
        reason=errors[0] if len(set(errors))==1 else 'No unique complete 9/18 numbered layout'
        return dict(row,status='geometry-withheld',reason=reason),None
    count,s=selections[0]
    greens=[w for w in s['ways'] if w['tags'].get('golf')=='green' and len(w['points'])>=4]
    if len(greens)<count:
        return dict(row,status='geometry-withheld',reason='Fewer way greens than numbered holes',holeCount=count,greenCount=len(greens)),None
    b=s['boundary']
    row.update(stage='complete-numbered-geometry-candidate',status='geometry-candidate',
        reason='Preliminary geometry only; full data gates pending',holeCount=count,greenCount=len(greens),
        boundaryID=b['id'],pathMarginMeters=s['margin'],compoundRefs=s['changes'])
    payload={'candidate':candidate,'ways':ways,'boundaries':boundaries,'screen':row}
    return row,payload


def survey(extract):
    assert extract in EXTRACTS.values()
    from pipeline.run_state import acquire
    states=sorted(s for s,e in EXTRACTS.items() if e==extract)
    dc_coverage=read('work/national/dc-extent.json')['directoryDCCenterCoverage']
    candidates=[c for c in inventory() if c['state'] in states]
    target=Path(f'work/national/states/{extract}.json')
    result={'extract':extract,'states':states,'startedAt':now(),'complete':False,'rows':[], 'errors':[]}
    write(target,result)
    try:
        root=acquire(extract)
        handler=GolfHandler(); handler.apply_file(str(root/'golf.osm.pbf'),locations=True,idx='flex_mem')
        result.update(source=read(root/'source.json'),parserErrors=handler.errors,relationGreenCount=len(handler.relation_greens))
        index=SpatialIndex(handler.ways,handler.areas)
        for candidate in candidates:
            try:
                if candidate['state']=='DC' and not dc_coverage.get(candidate['id'],False):
                    row={'candidate':candidate,'stage':'directory-candidate','status':'outside-extract','reason':'Directory center outside verified DC extract extent; identity/location unresolved'}; payload=None
                else:
                    row,payload=screen(candidate,index)
                if payload:
                    payload.update(state=extract,source=result['source'],parserErrors=handler.errors)
                    write(f'work/national/candidates/{candidate["id"]}.json',payload)
                result['rows'].append(row)
            except Exception as exc:
                result['rows'].append({'candidate':candidate,'stage':'directory-candidate','status':'incomplete','reason':f'{type(exc).__name__}: {exc}'})
        assert {r['candidate']['id'] for r in result['rows']}=={c['id'] for c in candidates}
        result['complete']=not any(r['status']=='incomplete' for r in result['rows'])
    except Exception as exc:
        result['errors'].append(f'{type(exc).__name__}: {exc}')
        raise
    finally:
        result['finishedAt']=now(); write(target,result)
    print(extract,Counter(r['status'] for r in result['rows']),flush=True)
    assert result['complete'], 'Candidate processing failed; report remains incomplete'


def aggregate(candidates, state_results):
    rows={}; extracts={}; by_id={c["id"]:c for c in candidates}
    for result in state_results:
        extract=result['extract']
        assert extract in set(EXTRACTS.values()) and extract not in extracts
        extracts[extract]=result
        expected={c['id'] for c in candidates if EXTRACTS.get(c['state'])==extract}
        seen=set()
        for row in result['rows']:
            cid=row['candidate']['id']
            assert cid in expected and cid not in rows, 'Unexpected, cross-state or duplicate result ID'
            assert row['candidate']==by_id[cid]
            seen.add(cid); rows[cid]=row
        if result['complete']: assert seen==expected and not result.get('errors')
    for c in candidates:
        cid=c['id']
        if c['state'] in ('AK','HI'):
            assert cid not in rows
            rows[cid]={'candidate':c,'stage':'directory-candidate','status':'outside-geography','reason':'AK/HI require separate current app geography/imagery design'}
        elif cid not in rows:
            rows[cid]={'candidate':c,'stage':'directory-candidate','status':'incomplete','reason':'State/candidate job did not finish; no no-data conclusion'}
    assert set(rows)=={c['id'] for c in candidates}
    state_counts={s:dict(Counter(r['status'] for r in rows.values() if r['candidate']['state']==s)) for s in sorted(ALL_STATES)}
    return rows,extracts,state_counts


def report():
    candidates=inventory()
    rows,extracts,state_counts=aggregate(candidates,[read(p) for p in Path('work/national/states').glob('*.json')])
    complete=len(extracts)==49 and all(r['complete'] for r in extracts.values()) and not any(r['status']=='incomplete' for r in rows.values())
    result={'builtAt':now(),'runID':os.environ['GITHUB_RUN_ID'],'sourceCommit':os.environ['GITHUB_SHA'],
        'inputRun':read('work/national/input-run.json'),'dcExtentEvidence':read('work/national/dc-extent.json'),'complete':complete,'counts':dict(Counter(r['status'] for r in rows.values())),
        'candidateCount':len(candidates),'directoryInvalidCoordinatesWithheld':23,'expectedExtractCount':49,
        'completedExtractCount':sum(r['complete'] for r in extracts.values()),'stateToExtract':EXTRACTS,
        'stateCounts':state_counts,'extracts':[{k:v for k,v in r.items() if k!='rows'} for r in extracts.values()],
        'rows':list(rows.values()),'limitations':LIMITATIONS,
        'dcMappingEvidence':'https://download.geofabrik.de/north-america/us/district-of-columbia.poly (verified DC extent); DC uses its dedicated extract'}
    stem='national-geometry-'+os.environ['GITHUB_RUN_ID']; write('reports/'+stem+'.json',result)
    lines=['# National mapped-geometry survey','',LIMITATIONS,'',f"Complete: **{complete}**. {len(candidates)} directory candidates accounted for; {result['completedExtractCount']}/49 extracts completed. 23 invalid-coordinate listing entries remain separately withheld.",'',
        f"Counts: `{json.dumps(result['counts'],sort_keys=True)}`",'',
        f"Run: https://github.com/CraneCourse/crane-course-data/actions/runs/{os.environ['GITHUB_RUN_ID']}",
        f"Pinned directory run: {INVENTORY_RUN}, commit `{INVENTORY_SHA}`.",'',
        'DC uses the [official dedicated extract extent](https://download.geofabrik.de/north-america/us/district-of-columbia.poly). AK/HI are outside this survey geography. Relation-only greens are not fabricated as ways. Parser errors and source timestamps/hashes remain in JSON evidence.','',
        '| State | Directory | Geometry candidates | Withheld | Outside geography/extent | Incomplete |','|---|---:|---:|---:|---:|---:|']
    for s,counts in state_counts.items(): lines.append('| '+' | '.join(map(str,[s,sum(counts.values()),counts.get('geometry-candidate',0),counts.get('geometry-withheld',0),counts.get('outside-geography',0)+counts.get('outside-extract',0),counts.get('incomplete',0)]))+' |')
    Path('reports/'+stem+'.md').write_text('\n'.join(lines)+'\n')
    Path('reports/LATEST-NATIONAL.md').write_text(f'[Latest national geometry survey]({stem}.md)\n')
    print(result['counts'],flush=True)
    # The report is deliberately produced before this failure assertion.
    assert complete, 'Incomplete geometry survey; retained per-state evidence and every candidate'


def main():
    cloud_only()
    command=sys.argv[1]
    if command=='verify': verify_run()
    elif command=='prepare': prepare()
    elif command=='report': report()
    else: survey(command)
if __name__=='__main__': main()
