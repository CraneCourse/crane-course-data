from copy import deepcopy
import pytest
from pipeline.quality import identity_error, equivalent
from pipeline.export import validate_document
from jsonschema import ValidationError

def link(name): return {'selectedID':'way/1','candidates':[{'id':'way/1','name':name}]}
@pytest.mark.parametrize('a,b', [('Lakeside Country Club','Lakeside Country Club'),('The Ridge At Castle Pines Colorado','The Ridge at Castle Pines'),('Pinehurst Resort Country Club No 7','Pinehurst Course No.7')])
def test_name_normalization(a,b): assert identity_error({'name':a},link(b)) is None
@pytest.mark.parametrize('a,b',[('Fairview Lakeside Country Club','Legacy Golf Course'),('Pinehurst No 7','Pinehurst No 4'),('Pinehurst No 7','Pinehurst'),('Castlewoods','')])
def test_identity_not_guessed(a,b): assert identity_error({'name':a},link(b))
def test_full_parity_detects_feature_or_yardage_change():
    assert equivalent({'points':[{'lat':42.,'lon':-77.}]},{'points':[{'lat':42.,'lon':-77.}]})
    assert not equivalent({'teeYardages':{'White':300}},{'teeYardages':{'White':301}})
    assert not equivalent({'features':[{'kind':'tee'}]},{'features':[]})
def test_index_rejects_missing_fields_and_unsafe_paths():
    with pytest.raises(ValidationError): validate_document({'courses':[]},'schemas/index.schema.json')
def test_real_fixture_replay():
    from pathlib import Path
    from pipeline.common import read
    from pipeline.full_match import match
    for file in Path('tests/fixtures').glob('extract-*.json'):
        p=read(file); c=p['candidate']['center']
        result=match(p['scorecard']['holes'],p['ways'],(c['lon'],c['lat']))
        assert result['pass']==p['python']['pass']
        if result['pass']: assert equivalent(result['holes'],p['python']['holes'])

def quality_fixture():
    from test_measurement import fixture
    from pipeline.full_match import match
    from shapely.geometry import box, mapping
    cards,ways,origin=fixture()
    for i,w in enumerate(ways): w['id']='way/'+str(i+1)
    result=match(cards,ways,origin)
    payload={'candidate':{'name':'Test Club','center':{'lon':origin[0],'lat':origin[1]}},
             'association':link('Test Club'),'boundary':mapping(box(-2000,-2000,2000,2000)),
             'ways':ways,'python':result}
    return payload,result['holes']

def test_publication_rejects_open_green_and_outside_center():
    from pipeline.quality import check
    p,holes=quality_fixture()
    assert check(p,holes,12000) is None
    p['ways'][1]['points'].pop()
    assert 'open' in check(p,holes,12000)
    p,holes=quality_fixture()
    holes[0]['center']['lat']+=.001
    assert 'outside its green' in check(p,holes,12000)

def test_publication_budget_and_extra_line():
    from pipeline.quality import check
    p,holes=quality_fixture()
    assert 'Watch' in check(p,holes,54000)
    p['ways'].append(dict(p['ways'][0],id='way/999',tags={'golf':'hole'}))
    assert 'Extra' in check(p,holes,12000)

def test_export_schema_matches_complete_holes_and_rejects_unknown_fields():
    p,holes=quality_fixture()
    doc={'candidate':{'id':'00000000-0000-0000-0000-000000000000','name':'Test Club','city':'','state':'NY','center':p['candidate']['center']},
         'holes':holes,'builtAt':'2026-10-16T00:00:00Z','dataVersion':'test','numbering':'osm-mapped','sources':['OSM','Geofabrik','OpenGolfAPI']}
    validate_document(doc,'schemas/course.schema.json')
    doc['holes'][0]['pin']=doc['holes'][0]['center']
    with pytest.raises(ValidationError): validate_document(doc,'schemas/course.schema.json')
