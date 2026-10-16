from datetime import datetime, timezone, timedelta
from urllib.error import HTTPError
from email.message import Message
import pytest
from pipeline.national_validation import Client, prepare_payload, full_gate
from pipeline.common import read


def test_shared_budget_does_not_reset_by_candidate_or_state():
    client=Client(2,pause=lambda _:None)
    client.consume(); client.consume()
    with pytest.raises(RuntimeError,match='budget'): client.consume()
    day=datetime.now(timezone.utc).date().isoformat()
    resumed=Client(800,pause=lambda _:None,usage={day:800})
    with pytest.raises(RuntimeError,match='UTC-day'): resumed.consume()
    assert resumed.requests==0
    with pytest.raises(AssertionError): Client(801)


def test_429_stops_every_new_request_and_preserves_reset(monkeypatch):
    calls=[]; headers=Message(); headers['Retry-After']='3600'
    def fail(req,timeout):
        calls.append(req.full_url)
        raise HTTPError(req.full_url,429,'Rate limited',headers,None)
    monkeypatch.setattr('urllib.request.urlopen',fail)
    client=Client(800,pause=lambda _:None)
    result=client.card('24925661-c5ad-4a89-87f2-b97a8f77fe83')
    assert 'HTTP 429' in result['error'] and len(calls)==1
    assert datetime.fromisoformat(client.resume_after)>datetime.now(timezone.utc)+timedelta(minutes=59)
    with pytest.raises(RuntimeError,match='rate limit'): client.card('2f8238b3-85d2-4d54-b1af-779dec846381')
    assert len(calls)==1 and client.requests==1
    epoch_reset=(datetime.now(timezone.utc)+timedelta(minutes=30)).timestamp()
    client.rate_stop({'X-RateLimit-Reset':str(epoch_reset),'Retry-After':'7200'})
    assert datetime.fromisoformat(client.resume_after)>datetime.now(timezone.utc)+timedelta(minutes=119)
    iso_reset=(datetime.now(timezone.utc)+timedelta(hours=3)).isoformat()
    client.rate_stop({'X-RateLimit-Reset':iso_reset,'Retry-After':'3600'})
    assert datetime.fromisoformat(client.resume_after)==datetime.fromisoformat(iso_reset)


def test_real_known_failures_remain_withheld_after_actual_card_adapter():
    from pathlib import Path
    from pipeline.reviewed_cards import facts
    reviewed=read('inputs/reviewed-scorecards.json')
    for path in Path('tests/fixtures/layouts-withheld').glob('*.json'):
        f=read(path); p=f['input']; cards=facts(reviewed[p['candidate']['id']])
        result,error=prepare_payload(p,{'response':{'holes':cards},'url':'https://example.org/pinned-public-card'})
        assert error==f['expectedReason']


def test_actual_output_parity_and_watch_gates_remain_mandatory():
    p=read('tests/fixtures/extract-24925661-c5ad-4a89-87f2-b97a8f77fe83.json')
    ref={'pass':True,'holes':p['python']['holes'],'watchBytes':54000}
    doc,error=full_gate(p,ref,'2026-10-16T23:00:00+00:00','test')
    assert doc is None and 'Watch' in error
    with pytest.raises(AssertionError,match='decision mismatch'):
        full_gate(p,{'pass':False},'2026-10-16T23:00:00+00:00','test')
    changed=dict(ref,holes=[dict(p['python']['holes'][0],par=6)]+p['python']['holes'][1:],watchBytes=100)
    with pytest.raises(AssertionError,match='full Hole mismatch'):
        full_gate(p,changed,'2026-10-16T23:00:00+00:00','test')
