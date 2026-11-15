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

# The directory inventory run and its exact commit are workflow_dispatch inputs
# (INVENTORY_RUN / INVENTORY_SHA), checked against GitHub run metadata by `verify`
# and recorded in work/national/input-run.json for every later job.
STATE_LISTINGS=51  # 50 states + DC; structural, not a directory size.
COORDINATE_REASONS=('Missing or nonfinite coordinates','Coordinates outside WGS84','Conflicting coordinate fields')
EXTRACTS=dict(STATE_NAMES, DC='district-of-columbia')
ALL_STATES=set(EXTRACTS)|{'AK','HI'}
LIMITATIONS=('Geometry candidates are preliminary: explicit identity-compatible boundaries, unique 9/18 numbered hole paths and enough way greens only. '
              'No scorecard, full matching, polygon/center, actual Swift, schema, Watch, imagery-download or field verification is implied. '
              'Relation greens are recorded but never converted into invented ways. Unfinished states/candidates are incomplete, not absent mapping. '
              'AK/HI remain outside current app geography/imagery gates. Published catalog remains unchanged.')


def inventory_pin(environ=os.environ):
    run=environ.get('INVENTORY_RUN','').strip(); sha=environ.get('INVENTORY_SHA','').strip()
    assert run.isascii() and run.isdigit(), 'inventory_run must be a numeric GitHub Actions run ID'
    assert len(sha)==40 and all(ch in '0123456789abcdef' for ch in sha), 'inventory_sha must be a full lowercase commit SHA'
    return run,sha


def check_inventory_meta(meta, repo, run, sha):
    """The same checks the former hard-coded pin received, now against dispatch inputs."""
    assert str(meta['id'])==run, 'GitHub returned a different run'
    assert meta['head_repository']['full_name']==repo and meta['head_branch']=='main', 'Inventory run is from another repository or branch'
    assert meta['event']=='workflow_dispatch' and meta['status']=='completed' and meta['conclusion']=='success', 'Inventory run did not finish successfully by dispatch'
    assert meta['head_sha']==sha, 'Inventory run commit differs from inventory_sha'
    assert meta['path']=='.github/workflows/catalog.yml', 'Run is not the directory inventory workflow'


def verify_run():
    repo=os.environ['GITHUB_REPOSITORY']; run,sha=inventory_pin()
    request=urllib.request.Request(f'https://api.github.com/repos/{repo}/actions/runs/{run}',headers={
        'Authorization':'Bearer '+os.environ['GITHUB_TOKEN'],'Accept':'application/vnd.github+json','User-Agent':'Crane-Course-Data/1.0'})
    with urllib.request.urlopen(request,timeout=30) as response: meta=json.load(response)
    check_inventory_meta(meta,repo,run,sha)
    write('work/national/input-run.json',{k:meta[k] for k in ('id','head_sha','created_at','html_url','path')})


def input_run():
    """Verified inventory run (written by `verify`, carried in the national-selection artifact)."""
    meta=read('work/national/input-run.json')
    assert meta['path']=='.github/workflows/catalog.yml'
    return str(meta['id']),meta['head_sha']


def inventory_numbers(report, candidates, run, sha):
    """Counts come from the inventory report and must agree with its candidates file."""
    assert report['sourceCommit']==sha and str(report['runID'])==run
    assert report['stateCount']==len(report['states'])==STATE_LISTINGS
    assert report['completeStatePaginationCount']==STATE_LISTINGS
    assert all(s['terminalEmptyPage'] for s in report['states'])
    assert len(candidates)==report['validUniqueDirectoryEntries']
    withheld=[e for s in report['states'] for e in s['errors'] if isinstance(e,dict)]
    # Every returned row is either a valid entry or one per-ID error; nothing unaccounted.
    assert sum(s['returnedIDs'] for s in report['states'])==len(candidates)+len(withheld), 'Returned IDs differ from valid plus withheld entries'
    return {'candidateCount':len(candidates),'directoryEntriesWithheld':len(withheld),
            'directoryInvalidCoordinatesWithheld':sum(e.get('reason') in COORDINATE_REASONS for e in withheld)}


def expected_candidate_count(geometry_report, reports=Path('reports')):
    """Directory size a geometry survey must account for: its recorded inventory report."""
    run=str(geometry_report['inputRun']['id']); inventory_report=read(Path(reports)/f'catalog-inventory-{run}.json')
    assert str(inventory_report['runID'])==run and inventory_report['sourceCommit']==geometry_report['inputRun']['head_sha']
    assert inventory_report['completeStatePaginationCount']==STATE_LISTINGS
    return inventory_report['validUniqueDirectoryEntries']


def inventory(with_numbers=False):
    candidates=read('work/catalog/candidates.json'); run,sha=input_run()
    report=read(f'reports/catalog-inventory-{run}.json')
    numbers=inventory_numbers(report,candidates,run,sha)
    assert len({c['id'] for c in candidates})==len(candidates), 'Duplicate inventory IDs'
    for c in candidates:
        uuid.UUID(c['id'])
        assert isinstance(c['name'],str) and c['name'].strip()
        lat,lon=c['center']['lat'],c['center']['lon']
        assert all(isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v) for v in (lat,lon))
        assert -90<=lat<=90 and -180<=lon<=180
    assert set(c['state'] for c in candidates)==ALL_STATES
    assert dict(Counter(c['state'] for c in candidates))==report['countsByState']
    return (candidates,numbers) if with_numbers else candidates


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
    candidates=inventory(); run,sha=input_run()
    verify_dc_extent(candidates)
    extracts=sorted(set(EXTRACTS.values()))
    write('work/national/selection.json',{'extracts':extracts,'candidateCount':len(candidates),'stateToExtract':EXTRACTS,
        'inventoryRun':run,'inventoryCommit':sha})
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
    candidates,numbers=inventory(with_numbers=True); run,sha=input_run()
    rows,extracts,state_counts=aggregate(candidates,[read(p) for p in Path('work/national/states').glob('*.json')])
    complete=len(extracts)==49 and all(r['complete'] for r in extracts.values()) and not any(r['status']=='incomplete' for r in rows.values())
    result={'builtAt':now(),'runID':os.environ['GITHUB_RUN_ID'],'sourceCommit':os.environ['GITHUB_SHA'],
        'inputRun':read('work/national/input-run.json'),'dcExtentEvidence':read('work/national/dc-extent.json'),'complete':complete,'counts':dict(Counter(r['status'] for r in rows.values())),
        'candidateCount':len(candidates),'directoryInvalidCoordinatesWithheld':numbers['directoryInvalidCoordinatesWithheld'],
        'directoryEntriesWithheld':numbers['directoryEntriesWithheld'],'expectedExtractCount':49,
        'completedExtractCount':sum(r['complete'] for r in extracts.values()),'stateToExtract':EXTRACTS,
        'stateCounts':state_counts,'extracts':[{k:v for k,v in r.items() if k!='rows'} for r in extracts.values()],
        'rows':list(rows.values()),'limitations':LIMITATIONS,
        'dcMappingEvidence':'https://download.geofabrik.de/north-america/us/district-of-columbia.poly (verified DC extent); DC uses its dedicated extract'}
    stem='national-geometry-'+os.environ['GITHUB_RUN_ID']; write('reports/'+stem+'.json',result)
    lines=['# National mapped-geometry survey','',LIMITATIONS,'',f"Complete: **{complete}**. {len(candidates)} directory candidates accounted for; {result['completedExtractCount']}/49 extracts completed. {numbers['directoryInvalidCoordinatesWithheld']} invalid-coordinate listing entries remain separately withheld"
        +(f" ({numbers['directoryEntriesWithheld']} withheld listing entries in all)." if numbers['directoryEntriesWithheld']!=numbers['directoryInvalidCoordinatesWithheld'] else '.'),'',
        f"Counts: `{json.dumps(result['counts'],sort_keys=True)}`",'',
        f"Run: https://github.com/CraneCourse/crane-course-data/actions/runs/{os.environ['GITHUB_RUN_ID']}",
        f"Pinned directory run: {run}, commit `{sha}`.",'',
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
