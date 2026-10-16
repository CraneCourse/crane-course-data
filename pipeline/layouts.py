"""Separate mapped courses by explicit boundary identity, never by guessed routing."""
from copy import deepcopy
import re
from shapely.geometry import shape, LineString
from shapely.ops import transform
from pyproj import Transformer
from pipeline.quality import identity_error, name_tokens
from pipeline.match import number
from pipeline.full_match import match, geometry


def complete_numbers(ways, count):
    numbers=[number(w) for w in ways if w['tags'].get('golf')=='hole' and number(w) is not None]
    return count in (9,18) and sorted(numbers)==list(range(1,count+1))


def explicit_course_number(candidate):
    values=re.findall(r'(?:\bno\.?\s*|#)([0-9]+)\b',candidate['name'],re.I)
    return int(values[0]) if len(values)==1 else None


def normalize_refs(ways, course_number, explicit_only=False):
    selected=[]; changes=[]
    for raw in ways:
        w=deepcopy(raw)
        if w['tags'].get('golf')=='hole':
            ref=w['tags'].get('ref','')
            parsed=re.fullmatch(r'([0-9]+)\s*-\s*#([0-9]+)',ref)
            if parsed and course_number is not None:
                if int(parsed[2])!=course_number: continue
                w['tags']['ref']=str(int(parsed[1]))
                changes.append({'id':w['id'],'sourceRef':ref,'number':int(parsed[1]),'courseNumber':course_number})
            elif explicit_only: continue
        selected.append(w)
    return selected,changes


def select_layout(candidate, ways, boundaries, count, required_boundary=None):
    """Select only explicit boundary/numbering evidence, without a scorecard guess.

    Both geometry survey and scorecard validation call this identical selector.
    Coordinates, margins, identity rules and centers are unchanged.
    """
    p={'candidate':candidate, 'ways':ways}
    compatible=[]; course_number=explicit_course_number(p['candidate'])
    for b in boundaries:
        b=deepcopy(b)
        link={'selectedID':b['id'],'method':'unique-named-boundary','confidence':'mapped', 'candidates':[b]}
        if identity_error(p['candidate'],link) is None and (required_boundary is None or b['id']==required_boundary):
            compatible.append(b)
        elif course_number is not None and required_boundary is None:
            # A named parent plus complete explicit '#N' refs can identify a
            # subcourse. The physical boundary remains the original parent.
            a=name_tokens(p['candidate']['name'])-{str(course_number)}; bt=name_tokens(b['name'])
            if a and a==bt:
                b['parentBoundaryID']=b['id']; b['parentBoundaryName']=b['name']
                b['id']=b['id']+'#course-'+str(course_number)
                b['name']=b['name']+' No. '+str(course_number)
                compatible.append(b)
    # Coincident/nested duplicate OSM boundaries with the same name may be
    # collapsed only by geometric containment, never scorecard fit.
    if len(compatible)>1 and len({frozenset(name_tokens(b['name'])) for b in compatible})==1:
        outer=[b for b in compatible if all(b['id']==a['id'] or shape(b['geometry']).covers(shape(a['geometry'])) for a in compatible)]
        if len(outer)==1: compatible=outer
    if len(compatible)!=1:
        return None, 'No single identity-compatible course boundary'
    b=compatible[0]; area=shape(b['geometry'])
    if not area.is_valid or area.is_empty: return None,'Invalid course boundary'
    c=p['candidate']['center']
    project=Transformer.from_crs('EPSG:4326',f'+proj=aeqd +lat_0={c["lat"]} +lon_0={c["lon"]} +datum=WGS84 +units=m',always_xy=True).transform
    wide=[w for w in p['ways'] if area.buffer(100).covers(transform(project,LineString(w['points'])))]
    wide,changes=normalize_refs(wide,course_number,'parentBoundaryID' in b)
    strict=[w for w in wide if w['tags'].get('golf')!='hole' or area.covers(transform(project,LineString(w['points'])))]
    if complete_numbers(strict,count): selected=strict; margin=0
    elif complete_numbers(wide,count): selected=wide; margin=100
    else: return None, 'Boundary does not contain one complete uniquely numbered layout'
    # Never choose a different complete layout just because its scorecard fits.
    if complete_numbers(strict,count) and complete_numbers(wide,count):
        assert {w['id'] for w in strict if w['tags'].get('golf')=='hole' and number(w) is not None} == {w['id'] for w in wide if w['tags'].get('golf')=='hole' and number(w) is not None}
    return {'boundary':b, 'ways':selected, 'changes':changes, 'margin':margin},None


def separate(payload, boundaries, cards=None, required_boundary=None):
    """Validate the selected explicit layout against a real complete scorecard."""
    p=deepcopy(payload)
    cards=cards if cards is not None else p['scorecard']['holes']
    selection,error=select_layout(p['candidate'],p['ways'],boundaries,len(cards),required_boundary)
    if error: return None,error
    b=selection['boundary']; selected=selection['ways']; changes=selection['changes']; margin=selection['margin']
    c=p['candidate']['center']
    # New source resolutions must not silently pair conflicting par values.
    by_number={number(w):w for w in selected if w['tags'].get('golf')=='hole' and number(w) is not None}
    for card in cards:
        tagged=by_number.get(card['number'],{}).get('tags',{}).get('par')
        if tagged is not None and tagged!=str(card['par']):
            return None,f"Hole {card['number']}: OSM par {tagged} conflicts with scorecard par {card['par']}"
    p['layoutResolution']={'boundaryID':b.get('parentBoundaryID',b['id']), 'compoundRefs':changes,
                           'sourceParAgreement':True,'holeNumbering':'explicit-osm-tags'}
    p.update(ways=selected,geometry=geometry(selected),scorecard={'holes':cards},boundary=b['geometry'],
             association={'selectedID':b['id'],'method':'unique-named-boundary','confidence':'mapped',
                          'pathMarginMeters':margin,'candidates':[{k:v for k,v in b.items() if k not in ('geometry','paths')} ]})
    p['python']=match(cards,selected,(c['lon'],c['lat']))
    return p,None


def nearby_boundaries(candidate, areas):
    from shapely.geometry import Point, mapping
    c=candidate['center']; origin=(c['lon'],c['lat'])
    project=Transformer.from_crs('EPSG:4326',f'+proj=aeqd +lat_0={c["lat"]} +lon_0={c["lon"]} +datum=WGS84 +units=m',always_xy=True).transform
    result=[]
    for a in areas:
        b=a['geometry'].bounds
        if not (b[0]-.02<=origin[0]<=b[2]+.02 and b[1]-.02<=origin[1]<=b[3]+.02): continue
        area=transform(project,a['geometry']); d=area.distance(Point(0,0))
        if d<=500:
            result.append({'id':a['id'],'name':a['name'],'distanceMeters':round(d,1),
                           'areaMeters2':round(area.area),'geometry':mapping(area)})
    return result
