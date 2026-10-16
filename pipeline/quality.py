import re, unicodedata, math
from shapely.geometry import shape, Point, Polygon, LineString
from shapely.ops import transform
from pyproj import Transformer
from pipeline.match import distance, number

STOP = {'the','at','of','golf','course','club','country','resort','no','tpc','private'}
def name_tokens(name):
    text=unicodedata.normalize('NFKD', name).encode('ascii','ignore').decode().lower()
    return set(re.findall(r'[a-z]+|[0-9]+',text))-STOP

def identity_error(candidate, link):
    selected=next((a for a in link.get('candidates',[]) if a['id']==link.get('selectedID')),None)
    if selected is None: return 'No unique OSM course boundary'
    a=name_tokens(candidate['name']); b=name_tokens(selected['name'])
    if not a or not b or not (a<=b or b<=a):
        return 'Course and boundary names need identity review; no automatic alias assumed'
    # Never ignore conflicting course-number qualifiers.
    if {x for x in a if x.isdigit()} != {x for x in b if x.isdigit()}:
        return 'Course-number qualifier differs from boundary name'
    return None

def check(payload, holes, watch_bytes):
    if payload.get("sourceResolutionError"): return payload["sourceResolutionError"]
    error=identity_error(payload['candidate'],payload['association'])
    if error: return error
    if payload['boundary'] is None: return 'No course boundary'
    if watch_bytes + 1024 >= 55_000: return 'Watch payload exceeds 55 KB with 1 KB round-metadata reserve'
    c=payload['candidate']['center']; origin=(c['lon'],c['lat'])
    project=Transformer.from_crs('EPSG:4326',f'+proj=aeqd +lat_0={c["lat"]} +lon_0={c["lon"]} +datum=WGS84 +units=m',always_xy=True).transform
    area=shape(payload['boundary']).buffer(100)
    ways={w['id']:w for w in payload['ways']}
    paths=[w for w in payload['ways'] if w['tags'].get('golf')=='hole' and number(w) is not None]
    if len(paths)!=len(holes): return 'Extra numbered hole lines within course boundary'
    for w in payload['ways']:
        if w['tags'].get('golf')!='hole':
            p=w['points']
            if len(p)<4 or p[0]!=p[-1] or not Polygon(p).is_valid or Polygon(p).area<=0:
                return f'Invalid or open {w["tags"].get("golf")} polygon {w["id"]}'
    for h,m in zip(holes,payload['python']['matchedHoles']):
        polygon=transform(project,Polygon(ways[m['greenID']]['points']))
        for key in ('front','center','back'):
            p=h[key]
            # Micrometre tolerance accommodates projected floating-point roundoff only.
            if not polygon.buffer(0.000001).covers(transform(project,Point(p['lon'],p['lat']))):
                return f'Hole {h["number"]}: {key} is outside its green'
        pts=[h[k] for k in ('tee','front','center','back')]+h['path']
        for f in h['features']: pts+=f['points']+sum(f.get('interiorRings',[]),[])
        for p in pts:
            xy=(p['lon'],p['lat'])
            if not (24<=xy[1]<=50 and -125<=xy[0]<=-66) or distance(xy,origin)>=3500:
                return f'Hole {h["number"]}: point outside geographic limits'
            if not area.covers(transform(project,Point(xy))):
                return f'Hole {h["number"]}: point outside buffered course boundary'
    return None

def equivalent(a,b):
    if isinstance(a,dict) and isinstance(b,dict):
        # Swift omits nil optional rings; neither side may hide actual rings.
        a={k:v for k,v in a.items() if not (k=='interiorRings' and v is None)}
        b={k:v for k,v in b.items() if not (k=='interiorRings' and v is None)}
        return a.keys()==b.keys() and all(equivalent(a[k],b[k]) for k in a)
    if isinstance(a,list) and isinstance(b,list):
        return len(a)==len(b) and all(equivalent(x,y) for x,y in zip(a,b))
    if isinstance(a,(int,float)) and isinstance(b,(int,float)):
        return math.isclose(a,b,rel_tol=0,abs_tol=1e-10)
    return a==b
