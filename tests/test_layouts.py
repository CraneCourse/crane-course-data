from copy import deepcopy
from datetime import date
import pytest
from shapely.geometry import box, mapping
from pipeline.layouts import separate
from pipeline.reviewed_cards import facts
from pipeline.common import read


def sample():
    ways=[{'id':f'way/{i}','tags':{'golf':'hole','ref':str(i)},'points':[[-100,40+i*.00001],[-99.9998,40+i*.00001]]} for i in range(1,10)]
    cards=[{'number':i,'par':3,'yardages':{'White':100}} for i in range(1,10)]
    p={'candidate':{'name':'Example South Course','center':{'lat':40,'lon':-100}},'ways':ways,'scorecard':{'holes':cards}}
    b={'id':'way/200','name':'Example South Course','geometry':mapping(box(-10,-10,50,50))}
    return p,b


def test_boundary_excludes_neighbor_duplicate_without_renumbering():
    p,b=sample()
    p['ways'].append({'id':'way/100','tags':{'golf':'hole','ref':'1'},'points':[[-99.9992,40],[-99.9991,40]]})
    out,error=separate(p,[b])
    assert error is None and out['association']['pathMarginMeters']==0
    assert {w['id'] for w in out['ways']}=={f'way/{i}' for i in range(1,10)}
    assert len(p['ways'])==10


def test_duplicate_inside_boundary_is_not_guessed():
    p,b=sample(); p['ways'].append(dict(deepcopy(p['ways'][0]),id='way/100'))
    assert separate(p,[b])[0] is None


def test_multiple_matching_boundaries_are_not_chosen_by_scorecard_fit():
    p,b=sample()
    assert separate(p,[b,dict(b,id='way/201')])[0] is None


def test_missing_edge_path_can_use_existing_buffer_only_if_complete():
    p,b=sample(); p['ways'][-1]['points']=[[-99.9992,40],[-99.9991,40]]
    out,error=separate(p,[b])
    assert error is None and out['association']['pathMarginMeters']==100


def test_neighbor_name_cannot_substitute():
    p,b=sample(); b['name']='Example North Course'
    assert separate(p,[b])[0] is None


def test_wrong_hole_count_cannot_be_trimmed():
    p,b=sample(); p['ways'].append(dict(deepcopy(p['ways'][0]),id='way/100',tags={'golf':'hole','ref':'10'}))
    assert separate(p,[b])[0] is None


def test_reviewed_cards_are_complete_and_match_printed_totals():
    for r in read('inputs/reviewed-scorecards.json').values():
        assert len(facts(r,date(2026,10,16)))==18
        with pytest.raises(ValueError): facts(r,date(2027,4,17))
        broken=deepcopy(r); next(iter(broken['tees'].values()))[0]+=1
        with pytest.raises(ValueError): facts(broken,date(2026,10,16))


def test_explicit_compound_ref_preserves_course_membership():
    from pipeline.layouts import normalize_refs
    ways=[{'id':'way/1','tags':{'golf':'hole','ref':'1 - #2'},'points':[]},
          {'id':'way/2','tags':{'golf':'hole','ref':'1 - #4'},'points':[]}]
    selected,evidence=normalize_refs(ways,2)
    assert len(selected)==1 and selected[0]['tags']['ref']=='1'
    assert evidence[0]['sourceRef']=='1 - #2'
    assert ways[0]['tags']['ref']=='1 - #2'


def test_compound_ref_without_explicit_course_number_stays_unmodified():
    from pipeline.layouts import normalize_refs
    ways=[{'id':'way/1','tags':{'golf':'hole','ref':'1 - #2'},'points':[]}]
    assert normalize_refs(ways,None)==(ways,[])


def test_par_disagreement_blocks_new_resolution():
    p,b=sample(); p['ways'][0]['tags']['par']='4'
    result,error=separate(p,[b])
    assert result is None and 'conflicts' in error


def test_parent_subcourse_requires_all_explicit_numbers():
    p,b=sample(); p['candidate']['name']='Example No. 5'; b['name']='Example'
    for w in p['ways']: w['tags']['ref']+=' - #5'
    result,error=separate(p,[b])
    assert error is None and len(result['layoutResolution']['compoundRefs'])==9
    p['ways'][0]['tags']['ref']='1'
    assert separate(p,[b])[0] is None


def test_new_alias_cannot_replace_existing_published_course_id():
    from pipeline.export import export_order
    ids=['a-new-alias','z-already-published']
    assert sorted(ids,key=lambda cid:export_order(cid,{}, {'z-already-published'}))[0]=='z-already-published'
