"""Repository-wide OpenGolfAPI ledger: reservations count in full and gate every caller."""
from datetime import datetime, timezone, timedelta
from pathlib import Path
import re
import pytest
from pipeline.api_ledger import plan, Budget, DAILY_LIMIT, reset_after, unexpired_stop

AT = datetime(2026, 10, 17, 12, tzinfo=timezone.utc)


def test_reservations_share_one_daily_limit_and_are_never_refunded():
    entries, a = plan([], 'validation/1/1/cards', DAILY_LIMIT - 100, AT)
    # A failed run that used nothing still holds its reservation.
    entries[0]['used'] = 0
    entries, b = plan(entries, 'build/2/1/cards', 300, AT)
    assert (a, b) == (DAILY_LIMIT - 100, 100)
    entries, c = plan(entries, 'validation/3/1/cards', 50, AT)
    assert c == 0 and sum(e['granted'] for e in entries) == DAILY_LIMIT


def test_rerun_attempt_reserves_again_but_a_retried_step_does_not_double_book():
    entries, first = plan([], 'validation/1/1/cards', 100, AT)
    same, again = plan(entries, 'validation/1/1/cards', 100, AT)
    assert again == first and len(same) == 1
    rerun, granted = plan(entries, 'validation/1/2/cards', 100, AT)
    assert granted == 100 and len(rerun) == 2


def test_recorded_rate_stop_blocks_reservations_until_reset_including_across_midnight():
    stop = {'key': 'old', 'granted': 10, 'resumeAfterUTC': (AT + timedelta(hours=2)).isoformat()}
    _, blocked = plan([stop], 'next', 100, AT)
    assert blocked == 0
    _, carried = plan([], 'next', 100, AT, earlier_entries=[stop])
    assert carried == 0
    _, later = plan([stop], 'next', 100, AT + timedelta(hours=3))
    assert later == 100


def test_budget_fails_closed_without_reservation(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(RuntimeError, match='fail closed'):
        Budget()


def test_budget_is_limited_to_its_day_and_count_and_records_usage(tmp_path):
    usage = tmp_path / 'usage.json'
    b = Budget({'day': '2026-10-17', 'granted': 1, 'key': 'k'}, pause=lambda _: None, clock=lambda: AT, usage_path=usage)
    b.consume()
    assert '"used": 1' in usage.read_text()
    with pytest.raises(RuntimeError, match='exhausted'):
        b.consume()
    late = Budget({'day': '2026-10-17', 'granted': 5, 'key': 'k'}, pause=lambda _: None,
                  clock=lambda: AT + timedelta(days=1), usage_path=None)
    with pytest.raises(RuntimeError, match='reserve again'):
        late.consume()


R = {'day': '2026-10-17', 'granted': 3, 'key': 'k'}


def quiet(reservation=R, **kw):
    return Budget(reservation, pause=lambda _: None, clock=lambda: AT, **kw)


def test_processes_in_one_job_share_one_grant(tmp_path):
    usage = tmp_path / 'usage.json'
    quiet(usage_path=usage).consume()
    second = quiet(usage_path=usage)          # e.g. controls after scorecards
    assert second.requests == 1
    second.consume(); second.consume()
    with pytest.raises(RuntimeError, match='exhausted'):
        quiet(usage_path=usage).consume()
    other = quiet({**R, 'key': 'another-job'}, usage_path=usage)   # a different reservation starts fresh
    assert other.requests == 0


def test_stop_carries_to_the_next_process(tmp_path):
    usage = tmp_path / 'usage.json'
    quiet(usage_path=usage).rate_stop({'Retry-After': '3600'})
    later = quiet(usage_path=usage)
    assert later.resume_after == (AT + timedelta(hours=1)).isoformat()
    with pytest.raises(RuntimeError, match='rate limit'):
        later.consume()


class Err(Exception):
    def __init__(self, code, headers):
        self.code = code; self.headers = headers


def test_retry_after_on_any_error_stops_but_plain_errors_do_not():
    plain = quiet(usage_path=None); plain.http_error(Err(500, {}))
    assert plain.stopped is None
    busy = quiet(usage_path=None); busy.http_error(Err(503, {'Retry-After': '120'}))
    assert busy.stopped and busy.resume_after == (AT + timedelta(minutes=2)).isoformat()
    limited = quiet(usage_path=None); limited.http_error(Err(429, {}))
    assert limited.stopped


def test_a_stop_from_another_job_halts_active_holders():
    seen = []
    def shared(at):
        seen.append(at); return '2026-10-18T00:00:00+00:00' if len(seen) > 1 else None
    b = quiet({**R, 'granted': 100}, usage_path=None, shared_stop=shared)
    for _ in range(25):
        b.consume()
    with pytest.raises(RuntimeError, match='Another job'):
        b.consume()
    assert b.requests == 25 and b.resume_after == '2026-10-18T00:00:00+00:00'


def test_unexpired_stops_are_found_on_any_ledger_day():
    old = {'reservations': [{'key': 'a', 'resumeAfterUTC': '2026-10-25T00:00:00+00:00'}]}
    past = {'reservations': [{'key': 'b', 'resumeAfterUTC': '2026-10-17T00:00:00+00:00'}, {'key': 'c'}]}
    assert unexpired_stop([past, old], AT) == '2026-10-25T00:00:00+00:00'
    assert unexpired_stop([past], AT) is None
    entries = [e for d in (past, old) for e in d['reservations']]
    assert plan([], 'new', 10, AT, earlier_entries=entries)[1] == 0


def test_reset_uses_the_later_header_and_defaults_to_next_utc_midnight():
    later = reset_after({'Retry-After': '7200', 'X-RateLimit-Reset': str((AT + timedelta(minutes=30)).timestamp())}, AT)
    assert later == AT + timedelta(hours=2)
    assert reset_after({}, AT) == datetime(2026, 10, 18, tzinfo=timezone.utc)


def test_every_opengolfapi_caller_spends_through_the_ledger_budget():
    callers = [p for p in Path('pipeline').glob('*.py') if re.search(r"https://api\.opengolfapi\.org", p.read_text())]
    assert callers, 'Expected OpenGolfAPI callers'
    for path in callers:
        text = path.read_text()
        assert 'Budget(' in text and ('consume()' in text or 'self.budget.consume' in text), f'{path} bypasses the ledger'
        assert 'urlopen' in text
    for workflow in Path('.github/workflows').glob('*.yml'):
        text = workflow.read_text()
        modules = re.findall(r'python -m (pipeline\.\w+)', text)
        spends = [m for m in modules if Path(m.replace('.', '/') + '.py') in callers]
        if spends:
            assert 'pipeline.api_ledger reserve' in text and 'pipeline.api_ledger record' in text, f'{workflow} spends without reserving'
