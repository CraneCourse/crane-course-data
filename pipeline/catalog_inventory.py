"""Cloud-only national directory inventory. No geometry processing or publication."""
from collections import Counter
from pathlib import Path
import hashlib
import json
import math
import os
import time
import urllib.error
import urllib.request
import uuid
from pipeline.common import cloud_only, STATE_NAMES, now, write

STATES = sorted(set(STATE_NAMES) | {'AK', 'HI', 'DC'})
LIMIT = 500
MAX_REQUESTS = 120  # Shared serial client; do not evade source daily limits.


def candidate(row, requested_state):
    cid = str(row['id']); uuid.UUID(cid)
    name = row.get('course_name') or row.get('name')
    if not isinstance(name, str) or not name.strip():
        raise ValueError('Missing course name')
    # Live state listings use lat/lng; the published OpenAPI schema lists
    # latitude/longitude. Accept either explicit pair, never swapped coordinates.
    lat, lon = row.get('lat', row.get('latitude')), row.get('lng', row.get('longitude'))
    if ('lat' in row and 'latitude' in row and row['lat'] != row['latitude']) or ('lng' in row and 'longitude' in row and row['lng'] != row['longitude']):
        raise ValueError('Conflicting coordinate fields')
    if any(isinstance(v, bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in (lat,lon)):
        raise ValueError('Missing or nonfinite coordinates')
    if not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise ValueError('Coordinates outside WGS84')
    state = row.get('state') or requested_state
    if state.strip().upper() != requested_state:
        raise ValueError('Directory state differs from requested state')
    return {'id':cid, 'name':name.strip(), 'city':row.get('city') or '', 'state':requested_state,
            'center':{'lat':lat,'lon':lon}}


def accept_page(data, seen):
    rows = data.get('courses') if isinstance(data,dict) else None
    if not isinstance(rows,list):
        raise ValueError('Missing courses array')
    ids = [r.get('id') for r in rows if isinstance(r,dict)]
    if len(ids)!=len(rows) or any(not isinstance(cid,str) for cid in ids) or len(set(ids))!=len(ids):
        raise ValueError('Invalid or duplicate IDs within page')
    if set(ids) & seen:
        raise ValueError('Repeated IDs across pages; pagination may be ignored or unstable')
    return rows


def main():
    from pipeline.api_ledger import Budget
    cloud_only(); budget = Budget()  # Repository-wide ledger; fails closed without a reservation.
    requests = 0; stop = False; all_candidates = {}; state_reports=[]; evidence=[]
    for state in STATES:
        seen=set(); offset=0; errors=[]; metadata_totals=[]; page_count=0; complete=False
        while not stop:
            if requests >= MAX_REQUESTS:
                errors.append('Shared run request budget exhausted'); stop=True; break
            url=f'https://api.opengolfapi.org/api/v1/courses/state/{state}?limit={LIMIT}&offset={offset}'
            try: budget.consume()
            except RuntimeError as exc:
                errors.append(str(exc)); stop=True; break
            requests+=1
            try:
                req=urllib.request.Request(url,headers={'User-Agent':'Crane-Course-Data/1.0','Accept':'application/json'})
                with urllib.request.urlopen(req,timeout=45) as response:
                    body=response.read(10_000_001)
                    if len(body)>10_000_000: raise ValueError('Page exceeds response size limit')
                    rate={k:v for k,v in response.headers.items() if 'ratelimit' in k.lower() or k.lower()=='retry-after'}
                    budget.observe(response.headers)
                data=json.loads(body); rows=accept_page(data,seen)
                evidence.append({'url':url,'fetchedAt':now(),'sha256':hashlib.sha256(body).hexdigest(),'returned':len(rows),'rateHeaders':rate})
                page_count+=1
                if isinstance(data.get('total'),int): metadata_totals.append(data['total'])
                if not rows:
                    complete=True; break
                for row in rows:
                    seen.add(row['id'])
                    try:
                        c=candidate(row,state)
                        if c['id'] in all_candidates: raise ValueError('ID occurs in another state')
                        all_candidates[c['id']]=c
                    except (ValueError,TypeError,KeyError) as exc:
                        errors.append({'id':row.get('id'),'reason':str(exc)})
                if page_count == 1 and not any(c['state']==state for c in all_candidates.values()):
                    errors.append('First page has no parseable candidates; stop acquisition for schema review')
                    stop=True; break
                offset+=len(rows)  # Honor server clamping; require an empty terminal page.
            except urllib.error.HTTPError as exc:
                errors.append(f'HTTP {exc.code}; no automatic retries in inventory')
                budget.http_error(exc)
                if exc.code==429: stop=True
                break
            except (urllib.error.URLError,TimeoutError,ValueError) as exc:
                errors.append(str(exc)); break
        if stop and not errors: errors.append('Not requested after shared budget/rate stop')
        state_reports.append({'state':state,'terminalEmptyPage':complete,'returnedIDs':len(seen),'pages':page_count,
                              'reportedTotals':sorted(set(metadata_totals)),'errors':errors})
        print(state,len(seen),'complete' if complete else 'incomplete',flush=True)
    counts=Counter(c['state'] for c in all_candidates.values())
    report={'runID':os.environ['GITHUB_RUN_ID'],'sourceCommit':os.environ['GITHUB_SHA'],'builtAt':now(),
            'requestCount':requests,'requestBudget':MAX_REQUESTS,'validUniqueDirectoryEntries':len(all_candidates),
            'completeStatePaginationCount':sum(s['terminalEmptyPage'] for s in state_reports),
            'stateCount':len(STATES),'states':state_reports,'sources':evidence,
            'limitations':'Directory inventory only, not supported-course counts or a geometry survey. Name/coordinate validation does not establish identity, complete scorecards, mapping, imagery availability or field accuracy. AK/HI are counted separately; current app geography/imagery gates exclude them. No player data.',
            'countsByState':dict(sorted(counts.items()))}
    stem='catalog-inventory-'+os.environ['GITHUB_RUN_ID']
    write('reports/'+stem+'.json',report)
    write('work/catalog/candidates.json',sorted(all_candidates.values(),key=lambda c:(c['state'],c['id'])))
    lines=['# U.S. directory inventory','',report['limitations'],'',
        f"{len(all_candidates)} valid unique directory entries; {report['completeStatePaginationCount']}/{len(STATES)} state/DC listings reached an empty terminal page; {requests} requests.",
        '', 'A terminal empty page verifies pagination ended; directory completeness and accuracy still depend on the provider. Errors are retained in the JSON report. No course is added to the published dataset by this run.', '',
        '| State | Returned IDs | Valid entries | Pagination | Errors |','|---|---:|---:|---|---:|']
    for s in state_reports:
        lines.append(f"| {s['state']} | {s['returnedIDs']} | {counts[s['state']]} | {'ended' if s['terminalEmptyPage'] else 'incomplete'} | {len(s['errors'])} |")
    Path('reports/'+stem+'.md').write_text('\n'.join(lines)+'\n')
    Path('reports/LATEST-CATALOG.md').write_text(f'[Latest directory inventory]({stem}.md)\n')

if __name__=='__main__': main()
