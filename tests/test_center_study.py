import pytest
from shapely.geometry import Polygon, Point
from pipeline.center_study import area_center, center_mode, comparison
from pipeline.match import center
import pipeline.match as matching
import pipeline.full_match as full


def green(points):
    return {'id':'way/1', 'tags':{'golf':'green'}, 'points':points}


def test_area_center_ignores_uneven_vertex_density():
    w = green([(-77,42),(-76.9999,42),(-76.9998,42),(-76.999,42),(-76.999,42.001),(-77,42.001),(-77,42)])
    x,y = area_center(w)
    assert x == pytest.approx(-76.9995, abs=1e-8)
    assert y == pytest.approx(42.0005, abs=1e-8)
    assert abs(y-center(w)[1]) > .0001
    assert Polygon(w['points']).covers(Point(x,y))


def test_concave_centroid_outside_is_not_silently_repaired():
    w=green([(0,0),(.003,0),(.003,.003),(.002,.003),(.002,.001),(.001,.001),(.001,.003),(0,.003),(0,0)])
    with pytest.raises(ValueError, match='outside'):
        area_center(w)


@pytest.mark.parametrize('points', [[(0,0),(1,0),(1,1)], [(0,0),(1,1),(1,0),(0,1),(0,0)]])
def test_invalid_or_open_green_refused(points):
    with pytest.raises(ValueError):
        area_center(green(points))


def test_non_green_centers_and_production_functions_remain_unchanged():
    w=green([(-77,42),(-76.999,42),(-76.999,42.001),(-77,42)])
    w['tags']['golf']='tee'
    assert area_center(w)==center(w)
    before=matching.center
    with center_mode(True):
        assert matching.center is area_center and full.center is area_center
    assert matching.center is before and full.center is before


def test_changed_green_is_reported_not_described_as_center_shift():
    a={'matches':[{'number':1,'pathID':'way/1','greenID':'way/2','center':(-77,42)}]}
    b={'matches':[{'number':1,'pathID':'way/1','greenID':'way/3','center':(-77,42)}]}
    result=comparison(a,b)
    assert len(result['changedAssignments'])==1
    assert result['centerShifts']==[]
