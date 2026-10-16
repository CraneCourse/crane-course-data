import pytest
from shapely.geometry import Polygon
from pipeline.common import state_for, cloud_only
from pipeline.match import number, diagnose, counts, center, yardage_error
from pipeline.extract import association, GolfHandler


def fixture():
    cards = [{'number': i+1, 'par': 4, 'yardages': {'white': 360}} for i in range(9)]
    ways = []
    for i in range(9):
        x = -77 + i * .001
        y = 42
        green = [(x-.0001,y+.0029), (x+.0001,y+.0029), (x+.0001,y+.0031), (x-.0001,y+.0031), (x-.0001,y+.0029)]
        ways += [{'id': f'path/{i}', 'tags': {'golf':'hole','ref':str(i+1)}, 'points': [(x,y),(x,y+.003)]},
                 {'id': f'green/{i}', 'tags': {'golf':'green'}, 'points': green}]
    return cards, ways, (-76.996,42.0015)


def test_complete_and_partial_courses():
    cards, ways, origin = fixture()
    assert diagnose(cards, ways, origin)['pass']
    missing = [w for w in ways if w['id'] != 'path/8']
    assert diagnose(cards, missing, origin)['rule'] == 'numbering'
    assert not diagnose(cards, missing, origin)['pass']


def test_duplicate_line_is_rejected():
    cards, ways, origin = fixture()
    ways.append(dict(ways[0], id='another-path'))
    assert diagnose(cards, ways, origin)['rule'] == 'numbering'
    assert counts(ways)['duplicateNumbers'] == [1]


def test_green_ambiguity_rejected():
    cards, ways, origin = fixture()
    ways.append(dict(ways[1], id='another-green'))
    assert diagnose(cards, ways, origin)['rule'] == 'green'


def test_scorecard_distance_and_common_tee():
    cards, ways, origin = fixture()
    cards[0]['yardages'] = {'white': 900}
    assert diagnose(cards, ways, origin)['rule'] == 'length'
    cards[0]['yardages'] = {'blue': 360}
    assert diagnose(cards, ways, origin)['rule'] == 'scorecard'
    assert yardage_error(cards)


def test_raw_number_parser_does_not_invent_number_from_ref():
    assert number({'tags':{'ref':'1 - #2'}}) is None
    assert number({'tags':{'ref':'1 - #2', 'name':'Hole 7'}}) == 7
    assert number({'tags':{'ref':'18'}}) == 18


def test_centroid_ignores_closing_vertex():
    assert center({'points':[(0,0),(2,0),(2,2),(0,2),(0,0)]}) == (1,1)


def test_scope_is_explicit():
    assert state_for({'center':{'lat':42,'lon':-77},'state':'NY'}) == 'new-york'
    assert state_for({'center':{'lat':42,'lon':-77},'state':'New York'}) == 'new-york'
    assert state_for({'center':{'lat':22,'lon':-155},'state':'HI'}) is None
    assert state_for({'center':{'lat':44,'lon':-79},'state':'ON'}) is None


def test_cloud_only(monkeypatch):
    monkeypatch.delenv('GITHUB_ACTIONS', raising=False)
    with pytest.raises(SystemExit): cloud_only()


def test_overlapping_boundaries_not_arbitrarily_chosen():
    area = {'id':'way/1','name':'A','geometry':Polygon([(-77.01,41.99),(-76.99,41.99),(-76.99,42.01),(-77.01,42.01)])}
    boundary, _, link = association([area], (-77,42))
    assert boundary is not None and link['method'] == 'contains'
    boundary, _, link = association([area, dict(area,id='relation/2')],(-77,42))
    assert boundary is None and link['method'] == 'ambiguous'


def test_osmium_reference_locations_and_course_area(tmp_path):
    xml = '''<osm version="0.6">
    <node id="1" lat="42" lon="-77"/><node id="2" lat="42" lon="-76.99"/>
    <node id="3" lat="42.01" lon="-76.99"/><node id="4" lat="42.01" lon="-77"/>
    <way id="10"><nd ref="1"/><nd ref="2"/><nd ref="3"/><nd ref="4"/><nd ref="1"/><tag k="leisure" v="golf_course"/><tag k="name" v="Test"/></way>
    <way id="11"><nd ref="1"/><nd ref="3"/><tag k="golf" v="hole"/><tag k="ref" v="1"/></way>
    </osm>'''
    p = tmp_path / 'fixture.osm'
    p.write_text(xml)
    handler = GolfHandler()
    handler.apply_file(str(p), locations=True, idx='flex_mem')
    assert len(handler.areas) == 1
    assert handler.areas[0]['id'] == 'way/10'
    assert len(handler.ways) == 1
    assert handler.ways[0]['points'][0] == (-77,42)
    assert not handler.errors
