from copy import deepcopy
from pathlib import Path
import pytest

from pipeline.common import read
from pipeline.infer import Policy, eligible, solve, infer
from pipeline.infer_sweep import masked


def option(name, green, anchor=None, yards=150):
    return {'kind':'mapped-path', 'pathID':name, 'startID':name, 'greenID':green,
            'anchor':anchor, 'par':3, 'start':(-77,42), 'end':(-77,42.001), 'yards':yards}


def cards(n=3):
    return [{'number':i+1,'par':3,'yardages':{'White':150}} for i in range(n)]


def test_two_complete_layouts_are_never_tiebroken():
    result=solve(cards(),[option('a','g1'),option('b','g2'),option('c','g3')])
    assert result['status']=='ambiguous'
    assert result['solutionsFound']==2
    assert result['solutionCountIsLowerBound']


def test_green_cannot_be_shared_and_search_limit_cannot_mean_unique():
    opts=[option('a','same',1),option('b','same',2),option('c','g3',3)]
    assert solve(cards(),opts)['status']=='no-assignment'
    opts[1]['greenID']='g2'
    assert solve(cards(),opts,Policy(max_nodes=1))['status']=='search-limit'


def test_near_alternative_in_relaxed_band_withholds():
    opts=[option('a','g1',1),option('b','g2',2),option('c','g3',3),option('d','g4',3,yards=177)]
    assert eligible(opts[-1],cards()[2]) is False
    assert eligible(opts[-1],cards()[2],1.25) is True
    assert solve(cards(),opts)['status']=='ambiguous'


def test_existing_numbers_and_par_are_hard_evidence():
    o=option('a','g1',anchor=2)
    assert not eligible(o,cards()[0])
    o['anchor']=None; o['reservedNumbers']=[1]
    assert not eligible(o,cards()[0])
    o['reservedNumbers']=[]; o['par']=4
    assert not eligible(o,cards()[0])


def test_no_greedy_choice_can_hide_a_second_assignment():
    # Tight scorecard fit must not hide a slightly longer valid alternative.
    opts=[option('a','g1',1),option('b','g2',2),option('c','g3',3),option('d','g4',3,155)]
    assert solve(cards(),opts)['status']=='ambiguous'


def test_wrap_transition_is_checked():
    opts=[option('a','g1',1),option('b','g2',2),option('c','g3',3)]
    opts[2]['end']=(-77,42.02)
    assert solve(cards(),opts)['status']=='no-assignment'


def test_real_mask_hides_both_number_sources_and_expected_answers():
    p=read('tests/fixtures/extract-24925661-c5ad-4a89-87f2-b97a8f77fe83.json')
    m=masked(p,[1])
    assert 'python' not in m and 'geometry' not in m
    from pipeline.match import number
    assert not any(number(w)==1 for w in m['ways'] if w['tags'].get('golf')=='hole')
    assert p['python']['pass']  # Original fixture unchanged.


def test_real_duplicate_number_and_uninterpreted_ref_rejected():
    p=read('tests/fixtures/extract-24925661-c5ad-4a89-87f2-b97a8f77fe83.json')
    paths=[w for w in p['ways'] if w['tags'].get('golf')=='hole' and w['tags'].get('ref')]
    p['ways'].append(deepcopy(paths[0]))
    assert infer(p)['status']=='ineligible'
    p['ways'].pop()
    paths[0]['tags']={'golf':'hole','ref':'1 - #2'}
    assert 'Non-integer' in infer(p)['reason']


def test_real_correct_and_ambiguous_fixtures():
    root=Path('tests/fixtures/inference')
    if not (root/'manifest.json').exists():
        pytest.skip('First cloud sweep will save real inference fixtures; rerun after sweep')
    manifest=read(root/'manifest.json')
    assert set(manifest)=={'correct','ambiguous'}
    for name,expect in manifest.items():
        result=infer(read(root/(name+'.json')),expect['mode'])
        assert result['status']==expect['status']
        assert result['publicationEligible'] is False
        if name=='correct':
            assert result['exampleAssignments'][0]==expect['assignment']

