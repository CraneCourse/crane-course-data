import pytest
from pipeline.catalog_inventory import accept_page, candidate, STATES


def test_pagination_repetition_is_an_error_not_completion():
    with pytest.raises(ValueError,match='Repeated IDs'):
        accept_page({'courses':[{'id':'x'}]}, {'x'})
    assert accept_page({'courses':[]},{'x'})==[]


def test_duplicate_page_is_refused():
    with pytest.raises(ValueError,match='duplicate'):
        accept_page({'courses':[{'id':'x'},{'id':'x'}]},set())


def test_candidates_keep_only_public_directory_fields():
    row={'id':'24925661-c5ad-4a89-87f2-b97a8f77fe83','course_name':'Course','state':'NY','latitude':42.0,'longitude':-77.0,'email':'not-retained','phone':'not-retained'}
    assert set(candidate(row,'NY'))=={'id','name','city','state','center'}
    with pytest.raises(ValueError,match='state'):
        candidate(row,'CA')
    row['latitude']=True
    with pytest.raises(ValueError,match='coordinates'):
        candidate(row,'NY')


def test_national_inventory_includes_unsupported_geographies_explicitly():
    assert len(STATES)==51
    assert {'AK','HI','DC'} <= set(STATES)
