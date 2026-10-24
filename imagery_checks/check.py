"""Cloud-only NAIP transfer/coverage smoke checks. No scorecard requests or app code."""
import hashlib
import io
import json
import math
import os
import shutil
from pathlib import Path
import time
import urllib.error
import urllib.parse
import urllib.request
import http.cookiejar
from collections import Counter
from PIL import Image, ImageDraw
from jsonschema import Draft202012Validator, FormatChecker

RUN = '40063763126'
SHA = 'a4d71b4bc7d9eace954f2fcec0d0cdf721c75aa3'
BASE_RUN = '39903637565'
BASE_SHA = 'c75f9b6f51a709f45a3a7ce70b8c7526c8a8431b'
EXPECTED_NEW = 252
REPLAY_RUN = '40098946399'
REPLAY_SHA = '94df77a5acb48bee806423be080ab392071d7663'
OZAUKEE = 'bc3df947-b18d-4684-9316-a9f0181fff19'
HOST = 'apps.geo.fpac.usda.gov'
ENDPOINT = f'https://{HOST}/geo-imagery/rest/services/naip/conus_naip/ImageServer/exportImage'
OUT = Path('work/imagery-checks')
LIMIT = 15_000_000
CAVEAT = ('Research smoke test of the download protocol, not a native iPhone/Watch test, '
          'a field test, visual alignment certification or long-term availability guarantee. '
          'Pixel warnings need review; dark pixels can be legitimate shadows/water. '
          'Only current-geometry full-data passes are tested. No OpenGolfAPI calls or publication.')


def read(p): return json.loads(Path(p).read_text())
def write(p, d):
    p = Path(p); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d, indent=2, sort_keys=True, allow_nan=False)+'\n')
def digest(b): return hashlib.sha256(b).hexdigest()
def stamp():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def verify_run():
    repo = os.environ['GITHUB_REPOSITORY']
    assert repo == 'CraneCourse/crane-course-data'
    evidence=[]
    for run, sha, event, outcome in ((RUN, SHA, 'schedule', 'failure'), (BASE_RUN, BASE_SHA, 'schedule', 'success')):
        req = urllib.request.Request(f'https://api.github.com/repos/{repo}/actions/runs/{run}', headers={
            'Authorization': 'Bearer '+os.environ['GITHUB_TOKEN'], 'Accept': 'application/vnd.github+json'})
        with urllib.request.urlopen(req, timeout=30) as r: d=json.load(r)
        assert d['head_repository']['full_name']==repo and d['head_branch']=='main'
        assert d['status']=='completed' and d['conclusion']==outcome and d['event']==event
        assert str(d['id'])==run and d['head_sha']==sha and d['path']=='.github/workflows/national-validation.yml'
        evidence.append({k:d[k] for k in ('id','head_sha','html_url','created_at','path')})
    req = urllib.request.Request(f'https://api.github.com/repos/{repo}/actions/runs/{REPLAY_RUN}', headers={
        'Authorization': 'Bearer '+os.environ['GITHUB_TOKEN'], 'Accept': 'application/vnd.github+json'})
    with urllib.request.urlopen(req, timeout=30) as r: replay=json.load(r)
    assert replay['head_repository']['full_name']==repo and replay['head_branch']=='codex/ozaukee-parity-fix'
    assert str(replay['id'])==REPLAY_RUN and replay['head_sha']==REPLAY_SHA
    assert replay['path']=='.github/workflows/tests.yml' and replay['event']=='workflow_dispatch'
    assert replay['status']=='completed' and replay['conclusion']=='success'
    write(OUT/'input-run.json', {'current':evidence[0], 'baseline':evidence[1],
          'recoveryReplay':{k:replay[k] for k in ('id','head_sha','html_url','created_at','path')}})

def passed_ids(report, run, sha):
    assert report['sourceCommit']==sha and str(report['runID'])==run
    rows=[r['candidate']['id'] for r in report['rows'] if r['status']=='full-data-gates-passed']
    ids=set(rows)
    assert len(ids)==len(rows) and ids==set(report['freshPassedIDs'])
    return ids


def new_passes(current, baseline):
    ids=passed_ids(current,RUN,SHA)
    old=passed_ids(baseline,BASE_RUN,BASE_SHA)
    assert len(ids)==2244 and len(old)==1992
    assert old<=ids, 'Earlier passes disappeared; inspect before running downloads'
    selected=ids-old
    assert len(selected)==EXPECTED_NEW
    return selected


def verify_recovered_passes(report, replay, selected):
    """The failed source is usable only with this exact independently verified replay.

    No source error is ignored or admitted. The replay used unchanged full gates,
    retained all prior passes, and withheld Ozaukee; this task tests only those passes.
    """
    assert report['errors']==[{'id':OZAUKEE,'reason':'AssertionError: Swift/Python full Hole mismatch'}]
    assert replay['run']==RUN and replay['sourceCommit']==SHA
    assert replay['originalFreshPasses']==2244 and replay['originalFreshPassesPreserved'] is True
    assert replay['executionErrors']==[] and replay['requests']==0 and replay['published'] is False
    assert replay['ozaukee']=={'id':OZAUKEE,'pass':False,
                            'reason':'Swift/Python feature assignment differs; course withheld'}
    rows=replay['results']; by_id={r['id']:r for r in rows}
    assert len(by_id)==len(rows)==replay['replayed']
    accepted=set(report['freshPassedIDs'])
    assert OZAUKEE not in accepted and OZAUKEE not in selected
    assert all(by_id[cid]['pass'] is True for cid in accepted)
    assert selected<=accepted


def inputs():
    report = read(f'reports/national-validation-{RUN}.json')
    baseline = read(f'baseline/reports/national-validation-{BASE_RUN}.json')
    ids = new_passes(report,baseline)
    verify_recovered_passes(report,read('recovery/results.json'),ids)
    write(OUT/'selected-ids.json', {'currentRun':RUN,'baselineRun':BASE_RUN,'count':len(ids),'ids':sorted(ids)})
    root=Path('work/national-validation/candidate-export')
    index=read(root/'index.json'); entries={e['id']:e for e in index['courses']}
    assert len(entries)==len(index['courses']) and ids<=entries.keys()
    validator=Draft202012Validator(read('schemas/course.schema.json'),format_checker=FormatChecker())
    docs=[]
    for cid in sorted(ids):
        e=entries[cid]; assert e['file']==f'courses/{cid}.json'
        raw=(root/e['file']).read_bytes(); assert digest(raw)==e['sha256']
        d=json.loads(raw); validator.validate(d); assert d['candidate']['id']==cid
        docs.append(d)
    return sorted(docs,key=lambda d:(d['candidate']['state'],d['candidate']['name'],d['candidate']['id']))


def points(doc, all_points=True):
    result=[]
    for h in doc['holes']:
        result+=h['path']
        if all_points: result += [h[k] for k in ('tee','front','center','back')]
        for f in h['features']:
            result += f['points']
            if all_points:
                for ring in f.get('interiorRings') or []: result+=ring
    return result


def request_bounds(doc):
    p=points(doc,False)
    return [min(x['lon'] for x in p)-.001,min(x['lat'] for x in p)-.001,
            max(x['lon'] for x in p)+.001,max(x['lat'] for x in p)+.001]


def checked_url(url):
    u=urllib.parse.urlsplit(url)
    if u.scheme!='https' or u.hostname!=HOST or u.username or u.password or u.port not in (None,443):
        raise ValueError('Imagery URL is not HTTPS on the expected USDA host')
    return url


class SameHostRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        checked_url(newurl)
        return super().redirect_request(req,fp,code,msg,headers,newurl)


class Client:
    def __init__(self, pause=time.sleep, maximum_requests=2*EXPECTED_NEW):
        self.pause=pause; self.requests=0; self.stop_reason=None; self.maximum_requests=maximum_requests
    def get(self, opener, url, maximum):
        if self.stop_reason: raise RuntimeError(self.stop_reason)
        assert self.requests<self.maximum_requests, 'Hard global request ceiling exceeded'
        self.pause(1.5); self.requests+=1
        req=urllib.request.Request(checked_url(url),headers={
            'User-Agent':'Crane-Course-Imagery-Research/1.0',
            'Cache-Control':'no-cache','Pragma':'no-cache'})
        try:
            with opener.open(req,timeout=60) as r:
                checked_url(r.url)
                if r.status!=200: raise ValueError(f'Unexpected HTTP {r.status}')
                body=r.read(maximum+1)
                if len(body)>maximum: raise ValueError('Response exceeds byte limit')
                expected=r.headers.get('Content-Length')
                if expected and int(expected)!=len(body): raise ValueError('Truncated transfer')
                return body,{'status':r.status,'contentType':r.headers.get('Content-Type'),'bytes':len(body)}
        except urllib.error.HTTPError as exc:
            if exc.code in (403,429,503): self.stop_reason=f'Service HTTP {exc.code}; sweep stopped without retries'
            raise


def returned_bounds(meta, doc):
    e=meta['extent']; b=[float(e[k]) for k in ('xmin','ymin','xmax','ymax')]
    if e.get('spatialReference',{}).get('wkid')!=4326: raise ValueError('Returned extent is not EPSG:4326')
    w,s,east,n=b
    if not all(math.isfinite(x) for x in b) or not (-180<=w<east<=180 and -90<=s<n<=90 and east-w<.2 and n-s<.2):
        raise ValueError('Invalid returned extent')
    if any(not (w<=p['lon']<=east and s<=p['lat']<=n) for p in points(doc)):
        raise ValueError('Returned extent clips course geometry')
    return b


def pixel(p,b,size):
    return ((p['lon']-b[0])/(b[2]-b[0])*(size[0]-1), (b[3]-p['lat'])/(b[3]-b[1])*(size[1]-1))


class UniformImageError(ValueError):
    pass


def inspect_image(raw, doc, bounds, metadata):
    if len(raw)>LIMIT: raise ValueError('Image exceeds app transfer limit')
    with Image.open(io.BytesIO(raw)) as im:
        if im.format!='JPEG' or im.size!=(2048,2048): raise ValueError('Unexpected image format/dimensions')
        if im.size!=(metadata['width'],metadata['height']): raise ValueError('Image dimensions disagree with export')
        im.load(); rgb=im.convert('RGB')
    sample=rgb.resize((64,64),Image.Resampling.BILINEAR)
    extrema=sample.getextrema()
    if all(lo==hi for lo,hi in extrema): raise UniformImageError('Uniform image: obvious no-data result')
    small=rgb.resize((256,256),Image.Resampling.BILINEAR)
    dark=sum(max(p)<=8 for p in small.getdata())/(256*256)
    white=sum(min(p)>=247 for p in small.getdata())/(256*256)
    suspect=[]
    for h in doc['holes']:
        for kind in ('tee','center'):
            x,y=pixel(h[kind],bounds,rgb.size); x=int(round(x)); y=int(round(y))
            crop=rgb.crop((max(0,x-4),max(0,y-4),min(2048,x+5),min(2048,y+5)))
            pp=list(crop.getdata())
            if not pp or sum(max(p)<=8 or min(p)>=247 for p in pp)/len(pp)>.95:
                suspect.append({'hole':h['number'],'point':kind})
    warnings=[]
    if dark>.02: warnings.append('More than 2% near-black pixels')
    if white>.05: warnings.append('More than 5% near-white pixels')
    if suspect: warnings.append('Nearly blank tee/green sample patches')
    return rgb,{'darkFraction':dark,'whiteFraction':white,'suspectPoints':suspect,'warnings':warnings,
                'decoder':'Pillow; not native CoreGraphics parity'}


def reopen_bundle(root, expected_hash):
    """This function uses only saved files. It has no HTTP client or network fallback."""
    bundle=read(root/'course.json'); raw=(root/'aerial.jpg').read_bytes()
    assert digest(raw)==expected_hash
    bounds=returned_bounds({'extent':dict(zip(('xmin','ymin','xmax','ymax'),bundle['returnedBounds']))|
                           {'spatialReference':{'wkid':4326}}},bundle['course'])
    inspect_image(raw,bundle['course'],bounds,bundle['exportDimensions'])
    return bundle


def thumbnail(rgb, doc, bounds, target):
    im=rgb.resize((600,600)); draw=ImageDraw.Draw(im)
    for h in doc['holes']:
        coords=[pixel(p,bounds,im.size) for p in h['path']]
        draw.line(coords,fill='cyan',width=2)
        x,y=pixel(h['center'],bounds,im.size)
        draw.ellipse((x-3,y-3,x+3,y+3),outline='yellow',width=2)
        draw.text((x+4,y),str(h['number']),fill='yellow',stroke_width=1,stroke_fill='black')
    target.parent.mkdir(parents=True,exist_ok=True); im.save(target)


def run():
    docs=inputs()
    rows=[]; client=Client(); consecutive=0; represented=set()
    for doc in docs:
        c=doc['candidate']; row={'candidate':c,'startedAt':stamp(),'status':'incomplete'}; start=time.monotonic()
        try:
            if client.stop_reason: raise RuntimeError(client.stop_reason)
            requested=request_bounds(doc); row['requestedBounds']=requested
            url=ENDPOINT+'?'+urllib.parse.urlencode({'bbox':','.join(map(str,requested)),
                'bboxSR':'4326','imageSR':'4326','size':'2048,2048','adjustAspectRatio':'false','format':'jpg','f':'json'})
            # A new cookie jar for each course; export + returned image share it.
            opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),SameHostRedirect())
            body,response=client.get(opener,url,1_000_000); meta=json.loads(body)
            if 'error' in meta: raise ValueError('USDA export error: '+str(meta['error'])[:400])
            bounds=returned_bounds(meta,doc); href=checked_url(meta['href'])
            row.update(returnedBounds=bounds,metadataSHA256=digest(body),metadataResponse=response)
            raw,image_response=client.get(opener,href,LIMIT)
            rgb,checks=inspect_image(raw,doc,bounds,meta)
            root=OUT/'temporary-bundle'; root.mkdir(parents=True,exist_ok=True)
            bundle={'course':doc,'returnedBounds':bounds,'exportDimensions':{'width':meta['width'],'height':meta['height']}}
            write(root/'course.json',bundle); (root/'aerial.jpg').write_bytes(raw)
            assert reopen_bundle(root,digest(raw))==bundle
            row.update(status='needs-visual-review' if checks['warnings'] else 'download-smoke-passed',
                       imageSHA256=digest(raw),imageResponse=image_response,checks=checks,fileRoundTripPassed=True)
            # Keep bounded diagnostic previews only. Full images stay transient.
            if c['state'] not in represented or checks['warnings']:
                if len(list((OUT/'previews').glob('*.jpg'))) < 90:
                    name=c['id']+'.jpg'; thumbnail(rgb,doc,bounds,OUT/'previews'/name); row['preview']='previews/'+name
                represented.add(c['state'])
            (root/'course.json').unlink(); (root/'aerial.jpg').unlink()
            consecutive=0
        except Exception as exc:
            row['error']=f'{type(exc).__name__}: {exc}'[:800]
            if isinstance(exc,UniformImageError):
                row['status']='imagery-rejected'; consecutive=0
            else:
                consecutive+=1
            if consecutive>=5 and not client.stop_reason: client.stop_reason='Five consecutive failures; inspect service/input before more requests'
        row['seconds']=round(time.monotonic()-start,3); rows.append(row)
        write(OUT/'progress.json',{'rows':rows,'requestCount':client.requests,'stopReason':client.stop_reason})
        print(c['state'],c['name'],row['status'],row['seconds'],flush=True)
    assert len(rows)==EXPECTED_NEW and {r['candidate']['id'] for r in rows}=={d['candidate']['id'] for d in docs}
    result={'builtAt':stamp(),'runID':os.environ['GITHUB_RUN_ID'],'sourceCommit':os.environ['GITHUB_SHA'],
            'inputRun':read(OUT/'input-run.json'),'candidateCount':len(rows),'counts':dict(Counter(r['status'] for r in rows)),
            'requestCount':client.requests,'requestsThisRun':client.requests,
            'stopReason':client.stop_reason,'scope':CAVEAT,'rows':rows}
    write(OUT/'report.json',result)
    lines=['# Validated-course imagery/download smoke test','',CAVEAT,'',
           f"{EXPECTED_NEW} new current-geometry passes accounted for. Results: `{json.dumps(result['counts'],sort_keys=True)}`.",
           f"USDA HTTP requests this run: {client.requests}; cumulative: {result['requestCount']}; OpenGolfAPI requests: **0**.",'',
           'Returned EPSG:4326 extents are retained and used for all point checks and preview overlays. '
           'Each export and its image use one private cookie session. There are no automatic retries. '
           'File round-trip means JSON/JPEG reopen in this cloud harness, not the app CourseCache or airplane-mode verification.','',
           '| State | Course | Outcome | Seconds | Detail |','|---|---|---|---:|---|']
    for r in rows:
        detail=r.get('error') or '; '.join(r.get('checks',{}).get('warnings',[]))
        lines.append('| '+' | '.join(str(x).replace('|','/').replace('\n',' ') for x in
            (r['candidate']['state'],r['candidate']['name'],r['status'],r['seconds'],detail))+' |')
    (OUT/'report.md').write_text('\n'.join(lines)+'\n')
    # Contact sheets are measurement previews with source attribution, never map replacements.
    previews=[r for r in rows if r.get('preview')]
    for page in range(0,len(previews),12):
        selected=previews[page:page+12]; sheet=Image.new('RGB',(1200,4*340+35),'white'); draw=ImageDraw.Draw(sheet)
        draw.text((8,8),'USDA NAIP / OSM overlays — research visual checks; returned image extents',fill='black')
        for i,r in enumerate(selected):
            x=(i%3)*400; y=(i//3)*340+35
            with Image.open(OUT/r['preview']) as im: sheet.paste(im.resize((320,300)),(x,y))
            draw.text((x,y+303),r['candidate']['state']+' '+r['candidate']['name'][:48],fill='black')
            draw.text((x,y+318),r['status'],fill='black')
        sheet.save(OUT/f'contact-sheet-{page//12+1}.jpg')
    print(result['counts'],flush=True)


if __name__=='__main__':
    assert os.environ.get('GITHUB_ACTIONS')=='true','Run imagery processing only in GitHub Actions'
    import sys
    if sys.argv[-1]=='verify': verify_run()
    else: run()
