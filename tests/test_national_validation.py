from datetime import datetime, timezone, timedelta
from urllib.error import HTTPError
from email.message import Message
import pytest
from pipeline.national_validation import Client, prepare_payload, full_gate
from pipeline.api_ledger import Budget
from pipeline.common import read


def budget(granted, day=None, clock=None):
    today=datetime.now(timezone.utc).date().isoformat()
    return Budget({'day':day or today,'granted':granted,'key':'test'},pause=lambda _:None,usage_path=None,
                  **({'clock':clock} if clock else {}))


def test_shared_budget_does_not_reset_by_candidate_or_state():
    client=Client(budget(2))
    client.consume(); client.consume()
    with pytest.raises(RuntimeError,match='exhausted'): client.consume()
    assert client.requests==2
    yesterday=(datetime.now(timezone.utc)-timedelta(days=1)).date().isoformat()
    with pytest.raises(RuntimeError,match='reserve again'): Client(budget(800,day=yesterday)).consume()


def test_429_stops_every_new_request_and_preserves_reset(monkeypatch):
    calls=[]; headers=Message(); headers['Retry-After']='3600'
    def fail(req,timeout):
        calls.append(req.full_url)
        raise HTTPError(req.full_url,429,'Rate limited',headers,None)
    monkeypatch.setattr('urllib.request.urlopen',fail)
    client=Client(budget(800))
    result=client.card('24925661-c5ad-4a89-87f2-b97a8f77fe83')
    assert 'HTTP 429' in result['error'] and len(calls)==1
    assert datetime.fromisoformat(client.resume_after)>datetime.now(timezone.utc)+timedelta(minutes=59)
    with pytest.raises(RuntimeError,match='rate limit'): client.card('2f8238b3-85d2-4d54-b1af-779dec846381')
    assert len(calls)==1 and client.requests==1
    epoch_reset=(datetime.now(timezone.utc)+timedelta(minutes=30)).timestamp()
    client.budget.rate_stop({'X-RateLimit-Reset':str(epoch_reset),'Retry-After':'7200'})
    assert datetime.fromisoformat(client.resume_after)>datetime.now(timezone.utc)+timedelta(minutes=119)
    iso_reset=(datetime.now(timezone.utc)+timedelta(hours=3)).isoformat()
    client.budget.rate_stop({'X-RateLimit-Reset':iso_reset,'Retry-After':'3600'})
    assert datetime.fromisoformat(client.resume_after)==datetime.fromisoformat(iso_reset)


def test_remaining_zero_header_stops_further_requests():
    client=Client(budget(800)); client.consume()
    client.budget.observe({'X-RateLimit-Remaining':'0','Retry-After':'60'})
    with pytest.raises(RuntimeError,match='rate limit'): client.consume()


def test_latest_prior_run_sees_failed_runs_through_acquisition_summaries(tmp_path):
    from pipeline.common import write
    from pipeline.national_validation import latest_prior_run
    geometry={'geometry':{'id':38983094457}}
    write(tmp_path/'national-validation-100.json',{'runID':'100','inputs':geometry})
    write(tmp_path/'national-acquisition-250.json',{'runID':'250','inputs':geometry})
    write(tmp_path/'national-acquisition-900.json',{'runID':'900','inputs':{'geometry':{'id':1}}})
    assert latest_prior_run('38983094457',tmp_path)=='250'
    assert latest_prior_run('5',tmp_path) is None


def test_report_leads_with_fresh_passes_and_labels_other_counts(monkeypatch):
    from pipeline.national_validation import report_lines
    monkeypatch.setenv('GITHUB_RUN_ID','1')
    result={'freshGeometryFullDataPassCount':12,'freshPassDistinctBoundaryCount':10,'existingPublishedFreshGeometryNotPassed':['a'],
        'counts':{'incomplete':3},'resumeAfterUTC':None,'borderUnresolvedCount':4,'frozenPublishedControlsPassCount':38,
        'combinedDistinctCandidateCount':49,'geometryCandidateDirectoryEntries':30,'geometryCandidateDistinctBoundaries':25,
        'freshPassScorecardProvenance':{'reused-published-card':{'count':2,'earliestFetchedAt':'2026-10-01T00:00:00','latestFetchedAt':'2026-10-02T00:00:00'}}}
    lines=report_lines(result,False,{'requests':5,'budget':9,'reservation':{'key':'k'}})
    head='\n'.join(lines[:6])
    assert 'Newly surveyed geometry passing every data gate: 12 layouts' in lines[2]
    assert 'reused-published-card: 2 (card dates 2026-10-01 to 2026-10-02)' in head
    text='\n'.join(lines)
    assert 'not a supported-course count' in text and 'aliases counted separately' in text and 'Field-verified courses added: **0**' in text
    assert text.index('12 layouts')<text.index('49 files')


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
