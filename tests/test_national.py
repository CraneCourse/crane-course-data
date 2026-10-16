"""Cloud regression checks: indexing is only a superset optimization."""
from copy import deepcopy
from pathlib import Path
import pytest
from pyproj import Transformer
from shapely.geometry import box, shape
from shapely.ops import transform
from pipeline.common import read
from pipeline.national import SpatialIndex, screen, aggregate, EXTRACTS
from pipeline.layouts import nearby_boundaries, select_layout
from pipeline.match import valid_way
from test_layouts import sample


def test_index_matches_bruteforce_at_geography_edges_and_long_ways():
    from pipeline.match import GEOD
    for lat in (24,40,50):
        c={'name':'Example','center':{'lat':lat,'lon':-100}}
        ways=[]
        for n,d in enumerate((0,1,3499.99,3500.01,5000,20000)):
            for az in range(0,360,15):
                lon2,lat2,_=GEOD.fwd(-100,lat,az,d)
                ways.append({'id':f'way/{n}-{az}','tags':{'golf':'hole'},'points':[[-100,lat],[lon2,lat2]]})
        for i,d in enumerate((1,3000,3501,6000,30000)):
            p1=GEOD.fwd(-100,lat,90,d)[:2]; p2=GEOD.fwd(-100,lat,95,d+1)[:2]
            ways.append({'id':f'way/off-{i}','tags':{'golf':'hole'},'points':[p1,p2]})
        ways.append({'id':'way/long-crossing','tags':{'golf':'hole'},'points':[[-101,lat],[-99,lat]]})
        areas=[{'id':'area/1','name':'Example','geometry':box(-100.01,lat-.01,-99.99,lat+.01)},
               {'id':'area/2','name':'Far','geometry':box(-90,lat,-89,lat+1)}]
        selected,selected_areas=SpatialIndex(ways,areas).query(c)
        assert selected==[w for w in ways if valid_way(w,(-100,lat))]
        assert nearby_boundaries(c,selected_areas)==nearby_boundaries(c,areas)


def fixture_index(p,b):
    c=p['candidate']['center']; inv=Transformer.from_crs(f'+proj=aeqd +lat_0={c["lat"]} +lon_0={c["lon"]} +datum=WGS84 +units=m','EPSG:4326',always_xy=True).transform
    return SpatialIndex(p['ways'],[dict(b,geometry=transform(inv,shape(b['geometry'])))])


def test_screen_requires_unique_full_numbering_and_greens_without_guessing_card():
    p,b=sample()
    for i in range(9):
        x=-99.9998; y=40+(i+1)*.00001
        p['ways'].append({'id':f'way/green-{i}','tags':{'golf':'green'},'points':[[x,y],[x+.000001,y],[x+.000001,y+.000001],[x,y]]})
    row,payload=screen(p['candidate'],fixture_index(p,b))
    assert row['status']=='geometry-candidate' and row['holeCount']==9
    assert 'scorecard' not in payload
    p['ways'].append(dict(deepcopy(p['ways'][0]),id='way/duplicate'))
    assert screen(p['candidate'],fixture_index(p,b))[0]['status']=='geometry-withheld'


def test_real_explicit_layout_selection_keeps_existing_matching_input():
    for file in Path('tests/fixtures/layouts').glob('*.json'):
        p=read(file)['input']
        out,error=select_layout(p['candidate'],p['ways'],p['boundaries'],len(p['scorecard']['holes']))
        assert error is None
        assert sorted(w['id'] for w in out['ways'] if w['tags'].get('golf')=='hole')==sorted(h['pathID'] for h in read(file)['expectedMatches'])


def test_incomplete_state_is_not_absent_geometry_and_all_ids_accounted():
    cs=[{'id':'a','state':'AL'},{'id':'b','state':'DC'},{'id':'c','state':'AK'},{'id':'d','state':'HI'}]
    rows,extracts,counts=aggregate(cs,[{'extract':'alabama','complete':False,'rows':[],'errors':['download failure']}])
    assert len(rows)==4
    assert rows['a']['status']==rows['b']['status']=='incomplete'
    assert rows['c']['status']==rows['d']['status']=='outside-geography'
    assert EXTRACTS['DC']=='district-of-columbia' and EXTRACTS['MD']=='maryland'
    assert len(set(EXTRACTS.values()))==49
    with pytest.raises(AssertionError):
        aggregate(cs,[{'extract':'alabama','complete':True,'rows':[],'errors':[]}])
    with pytest.raises(AssertionError):
        aggregate(cs,[{'extract':'alabama','complete':False,'rows':[{'candidate':cs[1]}]}])
