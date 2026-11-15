import io
import json
from pathlib import Path
import pytest
from PIL import Image
from imagery_checks.check import returned_bounds, inspect_image, reopen_bundle, digest, checked_url, Client, HOST


def doc():
    p={'lat':40.5,'lon':-75.5}
    return {'holes':[{'number':1,'path':[p,p],'features':[],'tee':p,'front':p,'center':p,'back':p}]}


def test_extent_keeps_returned_bounds_and_rejects_clipping_or_wrong_crs():
    e={'xmin':-75.55,'ymin':40.45,'xmax':-75.45,'ymax':40.55,'spatialReference':{'wkid':4326}}
    assert returned_bounds({'extent':e},doc())==[-75.55,40.45,-75.45,40.55]
    with pytest.raises(ValueError,match='clips'): returned_bounds({'extent':dict(e,xmin=-75.49)},doc())
    with pytest.raises(ValueError,match='4326'): returned_bounds({'extent':dict(e,spatialReference={'wkid':3857})},doc())
    with pytest.raises(ValueError): returned_bounds({'extent':dict(e,xmin=float('nan'))},doc())


def image_bytes(blank=False):
    im=Image.new('RGB',(2048,2048),(30,90,40))
    if not blank:
        from PIL import ImageDraw
        ImageDraw.Draw(im).rectangle((0,0,400,2047),fill=(90,60,40))
    out=io.BytesIO(); im.save(out,format='JPEG'); return out.getvalue()


def test_uniform_image_rejected_and_partial_black_flagged_not_certified():
    b=[-75.55,40.45,-75.45,40.55]; meta={'width':2048,'height':2048}
    with pytest.raises(ValueError,match='Uniform'): inspect_image(image_bytes(True),doc(),b,meta)
    im=Image.open(io.BytesIO(image_bytes())); from PIL import ImageDraw
    ImageDraw.Draw(im).rectangle((0,0,1024,2047),fill='black')
    out=io.BytesIO(); im.save(out,format='JPEG')
    _,r=inspect_image(out.getvalue(),doc(),b,meta)
    assert r['darkFraction']>.4 and r['warnings']
    with pytest.raises((ValueError,OSError)): inspect_image(b'not jpeg',doc(),b,meta)


def test_saved_bundle_reopens_without_network_and_detects_corruption(tmp_path,monkeypatch):
    def blocked(*a,**k): raise AssertionError('Network forbidden')
    monkeypatch.setattr('urllib.request.urlopen',blocked)
    raw=image_bytes(); bundle={'course':doc(),'returnedBounds':[-75.55,40.45,-75.45,40.55],'exportDimensions':{'width':2048,'height':2048}}
    (tmp_path/'course.json').write_text(json.dumps(bundle)); (tmp_path/'aerial.jpg').write_bytes(raw)
    assert reopen_bundle(tmp_path,digest(raw))==bundle
    (tmp_path/'aerial.jpg').write_bytes(b'corrupt')
    with pytest.raises(AssertionError): reopen_bundle(tmp_path,digest(raw))


def test_urls_fail_closed_and_rate_limit_stops_following_requests():
    from urllib.error import HTTPError
    from email.message import Message
    for url in ('http://'+HOST+'/a','https://other.example/a','https://user@'+HOST+'/a'):
        with pytest.raises(ValueError): checked_url(url)
    class Blocked:
        def open(self,*a,**k): raise HTTPError('https://'+HOST,429,'limited',Message(),None)
    c=Client(pause=lambda _:None)
    with pytest.raises(HTTPError): c.get(Blocked(),'https://'+HOST+'/a',100)
    with pytest.raises(RuntimeError): c.get(Blocked(),'https://'+HOST+'/a',100)
    assert c.requests==1


def batch():
    """Synthetic baseline/current reports with the exact pinned sizes and the reviewed set of lost passes."""
    from imagery_checks.check import RUN, SHA, BASE_RUN, BASE_SHA, ALLOWED_LOST, CURRENT_PASSES, BASE_PASSES, EXPECTED_NEW
    kept=[f'kept-{i}' for i in range(BASE_PASSES-len(ALLOWED_LOST))]
    new=[f'new-{i}' for i in range(EXPECTED_NEW)]
    assert len(kept)+len(new)==CURRENT_PASSES
    def report(ids, run, sha):
        return {'runID':run,'sourceCommit':sha,'freshPassedIDs':ids,'errors':[],
                'rows':[{'candidate':{'id':i},'status':'full-data-gates-passed'} for i in ids]
                    +[{'candidate':{'id':'frozen-control'},'status':'data-withheld'}]}
    return (report(kept+new,RUN,SHA), report(kept+sorted(ALLOWED_LOST),BASE_RUN,BASE_SHA), kept, new, ALLOWED_LOST)


def test_delta_excludes_old_passes_and_frozen_controls_and_accounts_for_every_lost_pass():
    from imagery_checks.check import new_passes
    current,baseline,kept,new,lost=batch()
    assert new_passes(current,baseline)==set(new)
    # a duplicate row is rejected
    dup=dict(current); dup['rows']=current['rows']+[current['rows'][0]]
    with pytest.raises(AssertionError): new_passes(dup,baseline)
    # an earlier pass that disappears without being on the reviewed list stops the batch
    gone=dict(current); gone['freshPassedIDs']=current['freshPassedIDs'][1:]
    gone['rows']=[r for r in current['rows'] if r['candidate']['id']!=kept[0]]
    with pytest.raises(AssertionError): new_passes(gone,baseline)
    # a reviewed lost pass that unexpectedly passes again also stops the batch (the set must match exactly)
    back=sorted(lost)[0]
    again=dict(current); again['freshPassedIDs']=current['freshPassedIDs'][:-1]+[back]
    again['rows']=[r for r in current['rows'] if r['candidate']['id']!=new[-1]]+[{'candidate':{'id':back},'status':'full-data-gates-passed'}]
    with pytest.raises(AssertionError): new_passes(again,baseline)
    # count-preserving swaps: the totals (3235 / 3153 / 101) still hold, only the lost SET differs
    first=sorted(lost)[0]
    swapped_current=dict(current); ids=[kept[1]]*0+kept[1:]+[first]+new   # kept[0] vanishes, a reviewed lost pass returns
    swapped_current['freshPassedIDs']=ids
    swapped_current['rows']=[{'candidate':{'id':i},'status':'full-data-gates-passed'} for i in ids]
    with pytest.raises(AssertionError,match='unexpectedly'): new_passes(swapped_current,baseline)
    other=dict(baseline); ids=kept+sorted(lost)[1:]+['some-other-course']
    other['freshPassedIDs']=ids; other['rows']=[{'candidate':{'id':i},'status':'full-data-gates-passed'} for i in ids]
    with pytest.raises(AssertionError,match='unexpectedly'): new_passes(current,other)
    # wrong source commit
    wrong=dict(current); wrong['sourceCommit']='wrong-sha'
    with pytest.raises(AssertionError): new_passes(wrong,baseline)


def test_blank_image_is_a_specific_content_failure_not_a_transport_failure():
    from imagery_checks.check import UniformImageError
    with pytest.raises(UniformImageError):
        inspect_image(image_bytes(True),doc(),[-75.55,40.45,-75.45,40.55],{'width':2048,'height':2048})


def test_new_successful_source_must_have_no_execution_errors():
    from imagery_checks.check import new_passes
    current,baseline,*_=batch()
    current['errors']=[{'id':'not-accepted','reason':'unreviewed execution error'}]
    with pytest.raises(AssertionError,match='execution errors'): new_passes(current,baseline)


def test_successful_baseline_must_have_no_execution_errors():
    from imagery_checks.check import new_passes
    current,baseline,*_=batch()
    baseline['errors']=[{'id':'not-accepted','reason':'unreviewed execution error'}]
    with pytest.raises(AssertionError,match='Baseline source'): new_passes(current,baseline)
