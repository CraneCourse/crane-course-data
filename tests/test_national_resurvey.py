"""Re-surveying: inventory pin as dispatch input, and new-survey card reuse.

Synthetic fixtures only; no network. Same-geometry resume rules are proven unchanged.
"""
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path
import io
import json
import shutil
import pytest
import pipeline.national as national
import pipeline.national_validation as nv
from pipeline.api_ledger import Budget
from pipeline.common import read, write, STATE_NAMES

REPO = Path(__file__).resolve().parents[1]
SHA = 'a' * 40
OTHER_SHA = 'b' * 40
REPOSITORY = 'CraneCourse/crane-course-data'


# ---------- A. inventory pin and derived counts ----------

def inventory_fixture(run='123', sha=SHA, invalid=2, other=1):
    states = sorted(set(STATE_NAMES) | {'AK', 'HI', 'DC'})
    candidates = [{'id': f'00000000-0000-4000-8000-{i:012d}', 'name': f'Course {i}', 'city': '', 'state': s,
                   'center': {'lat': 40.0, 'lon': -100.0}} for i, s in enumerate(states)]
    errors = [{'id': f'bad-{i}', 'reason': 'Missing or nonfinite coordinates'} for i in range(invalid)]
    errors += [{'id': f'other-{i}', 'reason': 'Missing course name'} for i in range(other)]
    state_reports = [{'state': s, 'terminalEmptyPage': True, 'returnedIDs': 1, 'pages': 2, 'reportedTotals': [1], 'errors': []} for s in states]
    state_reports[0]['errors'] = errors; state_reports[0]['returnedIDs'] += len(errors)
    report = {'runID': run, 'sourceCommit': sha, 'validUniqueDirectoryEntries': len(candidates),
              'completeStatePaginationCount': 51, 'stateCount': 51, 'states': state_reports,
              'countsByState': dict(sorted(Counter(c['state'] for c in candidates).items()))}
    return report, candidates


def test_counts_are_derived_from_the_inventory_report():
    report, candidates = inventory_fixture(invalid=2, other=1)
    numbers = national.inventory_numbers(report, candidates, '123', SHA)
    assert numbers == {'candidateCount': 51, 'directoryEntriesWithheld': 3, 'directoryInvalidCoordinatesWithheld': 2}


def test_committed_october_inventory_still_yields_the_former_constants():
    report = read(REPO / 'reports/catalog-inventory-38981104633.json')
    numbers = national.inventory_numbers(report, [None] * 16776, '38981104633', 'dec9d728641ce1ec1a80d575e64b11a1e598a525')
    assert numbers == {'candidateCount': 16776, 'directoryEntriesWithheld': 23, 'directoryInvalidCoordinatesWithheld': 23}


@pytest.mark.parametrize('mutate', [
    lambda r, c: c.pop(),                                                   # candidates file differs from report
    lambda r, c: r.update(completeStatePaginationCount=50),                 # pagination incomplete
    lambda r, c: r['states'][3].update(terminalEmptyPage=False),
    lambda r, c: r['states'].pop(),                                         # missing state listing
    lambda r, c: r['states'][1].update(returnedIDs=2),                      # unaccounted returned ID
    lambda r, c: r.update(sourceCommit=OTHER_SHA),                          # report from another commit
    lambda r, c: r.update(runID='999'),                                     # report from another run
])
def test_inconsistent_inventory_report_is_rejected(mutate):
    report, candidates = inventory_fixture()
    mutate(report, candidates)
    with pytest.raises(AssertionError):
        national.inventory_numbers(report, candidates, '123', SHA)


def test_inventory_reads_the_verified_run_and_keeps_every_candidate_check(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    report, candidates = inventory_fixture(run='777')
    write('work/catalog/candidates.json', candidates)
    write('reports/catalog-inventory-777.json', report)
    write('work/national/input-run.json', {'id': 777, 'head_sha': SHA, 'path': '.github/workflows/catalog.yml'})
    got, numbers = national.inventory(with_numbers=True)
    assert got == candidates and numbers['directoryInvalidCoordinatesWithheld'] == 2
    write('work/catalog/candidates.json', candidates + [dict(candidates[0])])   # duplicate ID, counts adjusted
    report['validUniqueDirectoryEntries'] += 1; report['states'][0]['returnedIDs'] += 1
    write('reports/catalog-inventory-777.json', report)
    with pytest.raises(AssertionError):
        national.inventory()
    write('work/national/input-run.json', {'id': 777, 'head_sha': OTHER_SHA, 'path': '.github/workflows/catalog.yml'})
    with pytest.raises(AssertionError):
        national.inventory()


def meta(**kw):
    m = {'id': 123, 'head_repository': {'full_name': REPOSITORY}, 'head_branch': 'main', 'event': 'workflow_dispatch',
         'status': 'completed', 'conclusion': 'success', 'head_sha': SHA, 'path': '.github/workflows/catalog.yml'}
    m.update(kw); return m


def test_inventory_run_metadata_checks_match_the_former_pin():
    national.check_inventory_meta(meta(), REPOSITORY, '123', SHA)
    for bad in (meta(id=124), meta(head_branch='dev'), meta(head_repository={'full_name': 'someone/fork'}),
                meta(event='schedule'), meta(status='in_progress'), meta(conclusion='failure'),
                meta(head_sha=OTHER_SHA), meta(path='.github/workflows/national.yml')):
        with pytest.raises(AssertionError):
            national.check_inventory_meta(bad, REPOSITORY, '123', SHA)


def test_inventory_inputs_are_validated_before_use():
    assert national.inventory_pin({'INVENTORY_RUN': ' 123 ', 'INVENTORY_SHA': SHA}) == ('123', SHA)
    for env in ({'INVENTORY_RUN': '', 'INVENTORY_SHA': SHA}, {'INVENTORY_RUN': '12 3', 'INVENTORY_SHA': SHA},
                {'INVENTORY_RUN': '1;ls', 'INVENTORY_SHA': SHA}, {'INVENTORY_RUN': '123', 'INVENTORY_SHA': 'abc'},
                {'INVENTORY_RUN': '123', 'INVENTORY_SHA': SHA.upper()}, {'INVENTORY_RUN': '123'}):
        with pytest.raises(AssertionError):
            national.inventory_pin(env)


def test_geometry_survey_size_comes_from_its_recorded_inventory(tmp_path):
    real = read(REPO / 'reports/national-geometry-38983094457.json')
    assert national.expected_candidate_count(real, REPO / 'reports') == 16776 == real['candidateCount']
    write(tmp_path / 'catalog-inventory-5.json', {'runID': '5', 'sourceCommit': SHA, 'completeStatePaginationCount': 51, 'validUniqueDirectoryEntries': 9})
    assert national.expected_candidate_count({'inputRun': {'id': 5, 'head_sha': SHA}}, tmp_path) == 9
    with pytest.raises(AssertionError):
        national.expected_candidate_count({'inputRun': {'id': 5, 'head_sha': OTHER_SHA}}, tmp_path)


# ---------- B. validation: verify() input rules ----------

G_OLD = {'id': 100, 'head_sha': 'g' * 40}
G_NEW = {'id': 300, 'head_sha': 'h' * 40}


def run_meta(run, path, sha='x' * 40, event='workflow_dispatch'):
    return {'id': int(run), 'head_repository': {'full_name': REPOSITORY}, 'head_branch': 'main', 'event': event,
            'status': 'completed', 'conclusion': 'success', 'path': path, 'head_sha': sha,
            'created_at': '2026-11-15T00:00:00Z', 'html_url': f'https://example.invalid/{run}'}


@pytest.fixture
def verify_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runs = {str(G_OLD['id']): run_meta(G_OLD['id'], '.github/workflows/national.yml', G_OLD['head_sha']),
            str(G_NEW['id']): run_meta(G_NEW['id'], '.github/workflows/national.yml', G_NEW['head_sha']),
            nv.BASELINE_RUN: run_meta(nv.BASELINE_RUN, '.github/workflows/resolve.yml', nv.BASELINE_SHA),
            '200': run_meta(200, '.github/workflows/national-validation.yml', event='schedule'),
            '250': run_meta(250, '.github/workflows/national-validation.yml', event='schedule')}
    def urlopen(req, timeout):
        return io.BytesIO(json.dumps(runs[req.full_url.rsplit('/', 1)[1]]).encode())
    monkeypatch.setattr('urllib.request.urlopen', urlopen)
    monkeypatch.setenv('GITHUB_REPOSITORY', REPOSITORY); monkeypatch.setenv('GITHUB_TOKEN', 't')
    baseline = {k: runs[nv.BASELINE_RUN][k] for k in ('id', 'head_sha', 'created_at', 'html_url', 'path')}
    write('reports/national-acquisition-200.json', {'runID': '200', 'inputs': {'geometry': G_OLD, 'baseline': baseline}})
    write('reports/national-validation-250.json', {'runID': '250', 'inputs': {'geometry': G_OLD, 'baseline': baseline}})
    write('reports/national-acquisition-250.json', {'runID': '250', 'inputs': {'geometry': G_OLD, 'baseline': baseline}})
    def go(geometry, previous='', cards_from=''):
        monkeypatch.setenv('GEOMETRY_RUN', str(geometry['id'])); monkeypatch.setenv('GEOMETRY_SHA', geometry['head_sha'])
        monkeypatch.setenv('PREVIOUS_RUN', previous); monkeypatch.setenv('CARDS_FROM_RUN', cards_from)
        nv.verify(); return read(nv.ROOT / 'inputs.json')
    return go


def test_same_geometry_resume_rules_are_unchanged(verify_env):
    assert verify_env(G_OLD, previous='250')['previous']['id'] == 250
    for previous in ('', '200'):              # must resume the latest recorded run of this survey
        with pytest.raises(AssertionError, match='Resume the latest recorded run 250'):
            verify_env(G_OLD, previous=previous)
    with pytest.raises(AssertionError):       # cards_from_run never accompanies a resume
        verify_env(G_OLD, previous='250', cards_from='250')
    with pytest.raises(AssertionError):       # nor replaces previous_run for a survey with a recorded run
        verify_env(G_OLD, cards_from='250')


def test_new_survey_may_start_from_the_newest_saved_cards(verify_env):
    inputs = verify_env(G_NEW, cards_from='250')
    assert inputs['cardsFrom']['id'] == 250 and 'previous' not in inputs
    assert verify_env(G_NEW).get('cardsFrom') is None          # still allowed: acquire everything as today
    with pytest.raises(AssertionError, match='latest recorded validation run 250'):
        verify_env(G_NEW, cards_from='200')                    # stale card set
    with pytest.raises(AssertionError):
        verify_env(G_NEW, cards_from='12a')                    # malformed run ID


def test_cards_from_rejects_same_survey_and_other_baseline(tmp_path):
    baseline = {'id': int(nv.BASELINE_RUN)}
    write(tmp_path / 'national-acquisition-250.json', {'runID': '250', 'inputs': {'geometry': G_OLD, 'baseline': baseline}})
    nv.check_cards_from('250', '', None, '300', baseline, tmp_path)
    with pytest.raises(AssertionError, match='Same geometry'):
        nv.check_cards_from('250', '', None, '100', baseline, tmp_path)
    with pytest.raises(AssertionError, match='different frozen baseline'):
        nv.check_cards_from('250', '', None, '300', {'id': 1}, tmp_path)
    with pytest.raises(AssertionError):
        nv.check_cards_from('250', '250', None, '300', baseline, tmp_path)
    with pytest.raises(AssertionError):
        nv.check_cards_from('250', '', '400', '300', baseline, tmp_path)


def test_manual_dispatch_passes_cards_from_and_schedule_does_not(tmp_path, monkeypatch):
    out = tmp_path / 'out'; out.write_text('')
    monkeypatch.setenv('GITHUB_OUTPUT', str(out)); monkeypatch.setenv('GITHUB_EVENT_NAME', 'workflow_dispatch')
    for k, v in {'INPUT_GEOMETRY_RUN': '300', 'INPUT_GEOMETRY_SHA': 'h' * 40, 'INPUT_PREVIOUS_RUN': '', 'INPUT_REQUEST_BUDGET': '', 'INPUT_CARDS_FROM_RUN': '250'}.items():
        monkeypatch.setenv(k, v)
    nv.resolve()
    assert out.read_text().splitlines() == ['proceed=true', 'geometry_run=300', 'geometry_sha=' + 'h' * 40, 'previous_run=', 'request_budget=500', 'cards_from_run=250']


def test_nightly_resumes_a_survey_that_started_from_carried_cards(tmp_path):
    at = datetime(2026, 11, 20, 0, 20, tzinfo=timezone.utc)
    write(tmp_path / 'national-validation-250.json', {'runID': '250', 'inputs': {'geometry': G_OLD}, 'complete': True})
    write(tmp_path / 'national-acquisition-400.json', {'runID': '400', 'inputs': {'geometry': G_NEW, 'cardsFrom': {'id': 250}}, 'resumeAfterUTC': None})
    values, reason = nv.scheduled_inputs(tmp_path, run_conclusion=lambda run: 'success', at=at)
    assert values == {'proceed': 'true', 'geometry_run': '300', 'geometry_sha': 'h' * 40, 'previous_run': '400', 'request_budget': '500'}, reason


# ---------- B. validation: acquire() with carried cards ----------

FLOOR = sorted(read(REPO / f'reports/layout-resolution-{nv.BASELINE_RUN}.json')['validatedCourseIDs'])
A, B, C, D, R, O = (f'00000000-0000-4000-8000-00000000000{i}' for i in range(1, 7))
F = FLOOR[0]
OLD_DATE = '2026-10-20T01:02:03+00:00'


def card(cid, **extra):
    return dict({'url': f'https://api.opengolfapi.org/v1/courses/{cid}/holes', 'fetchedAt': OLD_DATE, 'attempts': 1, 'sha256': 'f' * 64},
                **extra, response={'holes': [{'number': 1, 'par': 4}]})


def source(record):
    return {k: v for k, v in record.items() if k != 'response'}


@pytest.fixture
def world(tmp_path, monkeypatch):
    """Geometry 300 with candidates A,B,C,D,R,F; an earlier survey's saved cards for A,C,R,F,O (+ D incomplete)."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'reports').mkdir()
    shutil.copy(REPO / f'reports/layout-resolution-{nv.BASELINE_RUN}.json', tmp_path / 'reports')
    ids = [A, B, C, D, R, F]
    cands = {cid: {'id': cid, 'name': cid, 'state': 'NY', 'center': {'lat': 42.0, 'lon': -75.0}} for cid in ids + [O]}
    rows = [{'candidate': cands[cid], 'status': 'geometry-candidate'} for cid in ids]
    rows.append({'candidate': dict(cands[O], id='00000000-0000-4000-8000-0000000000ff'), 'status': 'geometry-withheld'})
    write('reports/catalog-inventory-77.json', {'runID': '77', 'sourceCommit': SHA, 'completeStatePaginationCount': 51, 'validUniqueDirectoryEntries': len(rows)})
    write('reports/national-geometry-300.json', {'complete': True, 'sourceCommit': G_NEW['head_sha'], 'candidateCount': len(rows), 'rows': rows,
                                                 'inputRun': {'id': 77, 'head_sha': SHA}})
    for cid in ids: write(f'work/national/candidates/{cid}.json', {'candidate': cands[cid]})
    for cid in FLOOR:
        write(f'work/frozen/work/validation/{cid}.json', {'scorecardSource': {'url': 'frozen', 'fetchedAt': '2026-10-16T00:00:00+00:00'}, 'scorecard': {'holes': []}})
    write(nv.ROOT / 'border.json', {'rows': {cid: ('border-unresolved' if cid == C else 'inside') for cid in ids}})
    write('inputs/reviewed-scorecards.json', {R: {'url': 'https://official.example/r'}})
    baseline = {'id': int(nv.BASELINE_RUN), 'head_sha': nv.BASELINE_SHA}
    current = {'geometry': G_NEW, 'baseline': baseline, 'cardsFrom': {'id': 250}}
    write(nv.ROOT / 'inputs.json', current)
    prior_inputs = {'geometry': G_OLD, 'baseline': baseline, 'previous': {'id': 200}}
    saved = {cid: card(cid) for cid in (A, C, R, O)}
    saved[F] = card(F, reusedFromRun=nv.BASELINE_RUN)
    prior_rows = [{'candidate': cands[cid], 'status': 'scorecard-acquired', 'source': source(saved[cid])} for cid in saved]
    prior_rows.append({'candidate': cands[D], 'status': 'incomplete', 'reason': 'budget', 'source': {}})
    src = Path('work/cards-from')
    write(src / 'inputs.json', prior_inputs)
    write(src / 'acquisition.json', {'rows': prior_rows, 'resumeAfterUTC': None})
    for cid, record in saved.items(): write(src / 'scorecards' / f'{cid}.json', record)
    write('reports/national-acquisition-250.json', {'runID': '250', 'inputs': prior_inputs, 'counts': dict(Counter(r['status'] for r in prior_rows))})
    fetched = []
    def fake_card(self, cid):
        self.consume(); fetched.append(cid); return card(cid, fetchedAt='2026-11-16T00:00:00+00:00')
    monkeypatch.setattr(nv.Client, 'card', fake_card)
    monkeypatch.setattr(nv, 'Budget', lambda: Budget({'day': datetime.now(timezone.utc).date().isoformat(), 'granted': 10, 'key': 'test'},
                                                     pause=lambda _: None, usage_path=None))
    monkeypatch.setattr(nv, 'fetch', lambda record: {'url': record['url'], 'fetchedAt': '2026-11-16T00:00:00+00:00', 'type': 'reviewed-official-scorecard'})
    monkeypatch.setattr(nv, 'facts', lambda record: [{'number': 1, 'par': 4}])
    monkeypatch.setenv('GEOMETRY_RUN', '300'); monkeypatch.setenv('GEOMETRY_SHA', G_NEW['head_sha'])
    monkeypatch.setenv('PREVIOUS_RUN', ''); monkeypatch.setenv('CARDS_FROM_RUN', '250'); monkeypatch.setenv('REQUEST_BUDGET', '500')
    return {'fetched': fetched, 'src': src, 'saved': saved, 'prior_rows': prior_rows}


def test_new_survey_reuses_saved_cards_and_acquires_the_rest_through_the_budget(world):
    assert nv.need() == 3                           # B (no card), D (prior incomplete), R (reviewed path)
    nv.acquire()
    a = read(nv.ROOT / 'acquisition.json')
    status = {r['candidate']['id']: r['status'] for r in a['rows']}
    assert status == {A: 'scorecard-acquired', B: 'scorecard-acquired', C: 'border-unresolved', D: 'scorecard-acquired',
                      R: 'scorecard-acquired', F: 'scorecard-acquired'} and a['complete']
    assert sorted(world['fetched']) == sorted([B, D])                    # only cards nobody saved
    assert a['requests'] == 3                                            # B, D and the reviewed re-fetch of R
    assert a['carriedCards'] == 1 and a['cachedCards'] == 1 and a['resumedCards'] == 0
    carried = read(nv.ROOT / 'scorecards' / f'{A}.json')
    assert carried['fetchedAt'] == OLD_DATE and carried['carriedFromRun'] == '250'   # original date, labeled
    assert carried['response'] == world['saved'][A]['response']
    assert read(nv.ROOT / 'scorecards' / f'{R}.json')['type'] == 'reviewed-official-scorecard'
    assert read(nv.ROOT / 'scorecards' / f'{F}.json')['reusedFromRun'] == nv.BASELINE_RUN
    assert not (nv.ROOT / 'scorecards' / f'{O}.json').exists()           # orphan card ignored
    assert not (nv.ROOT / 'scorecards' / f'{C}.json').exists()           # border-unresolved spends and carries nothing
    provenance = nv.scorecard_provenance({A, B, D, R, F}, a)
    assert provenance['carried-from-earlier-survey'] == {'count': 1, 'earliestFetchedAt': OLD_DATE, 'latestFetchedAt': OLD_DATE}
    assert provenance['fetched-or-resumed']['count'] == 2
    assert provenance['reviewed-official-card']['count'] == 1 and provenance['reused-published-card']['count'] == 1


@pytest.mark.parametrize('tamper', ['provenance', 'no-holes', 'reset', 'summary', 'counts', 'same-geometry', 'baseline'])
def test_carried_cards_fail_closed(world, tamper):
    src = world['src']
    if tamper == 'provenance': write(src / 'scorecards' / f'{A}.json', card(A, fetchedAt='2026-11-01T00:00:00+00:00'))
    if tamper == 'no-holes': write(src / 'scorecards' / f'{A}.json', source(card(A)))
    if tamper == 'reset':
        a = read(src / 'acquisition.json'); a['resumeAfterUTC'] = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        write(src / 'acquisition.json', a)
    if tamper == 'summary':
        s = read('reports/national-acquisition-250.json'); s['inputs']['previous'] = {'id': 1}; write('reports/national-acquisition-250.json', s)
    if tamper == 'counts':
        s = read('reports/national-acquisition-250.json'); s['counts']['scorecard-acquired'] += 1; write('reports/national-acquisition-250.json', s)
    if tamper == 'same-geometry':
        for path in (src / 'inputs.json',):
            i = read(path); i['geometry'] = G_NEW; write(path, i)
    if tamper == 'baseline':
        i = read(src / 'inputs.json'); i['baseline'] = {'id': 1}; write(src / 'inputs.json', i)
    with pytest.raises(AssertionError):
        nv.need()
    with pytest.raises(AssertionError):
        nv.acquire()
    assert world['fetched'] == []


def make_same_geometry_resume(world, monkeypatch):
    """Convert the world into today's same-geometry resume from run 260 of geometry 300."""
    prior = Path('work/previous')
    inputs = read(nv.ROOT / 'inputs.json'); inputs.pop('cardsFrom'); inputs['previous'] = {'id': 260}
    write(nv.ROOT / 'inputs.json', inputs)
    write(prior / 'inputs.json', {'geometry': G_NEW, 'baseline': inputs['baseline']})
    rows = []
    for cid in (A, B, C, D, R, F):
        cand = read(f'work/national/candidates/{cid}.json')['candidate']
        if cid in (A, R):
            write(prior / 'scorecards' / f'{cid}.json', card(cid)); rows.append({'candidate': cand, 'status': 'scorecard-acquired', 'source': source(card(cid))})
        else:
            rows.append({'candidate': cand, 'status': 'incomplete', 'reason': 'budget'})
    write(prior / 'acquisition.json', {'rows': rows, 'resumeAfterUTC': None})
    monkeypatch.setenv('PREVIOUS_RUN', '260'); monkeypatch.setenv('CARDS_FROM_RUN', '')
    return prior


def test_same_geometry_resume_acquire_is_unchanged(world, monkeypatch):
    make_same_geometry_resume(world, monkeypatch)
    assert nv.need() == 2                           # B and D; F frozen; A and R resumed
    nv.acquire()
    a = read(nv.ROOT / 'acquisition.json')
    assert a['resumedCards'] == 2 and a['cachedCards'] == 1 and a['carriedCards'] == 0 and a['complete']
    assert sorted(world['fetched']) == sorted([B, D])
    assert 'carriedFromRun' not in read(nv.ROOT / 'scorecards' / f'{A}.json')


@pytest.mark.parametrize('tamper', ['other-geometry', 'other-baseline', 'missing-row', 'extra-row', 'reset'])
def test_same_geometry_resume_rejections_are_unchanged(world, monkeypatch, tamper):
    prior = make_same_geometry_resume(world, monkeypatch)
    i = read(prior / 'inputs.json'); a = read(prior / 'acquisition.json')
    if tamper == 'other-geometry': i['geometry'] = G_OLD
    if tamper == 'other-baseline': i['baseline'] = {'id': 1}
    if tamper == 'missing-row': a['rows'].pop()
    if tamper == 'extra-row': a['rows'].append({'candidate': {'id': O}, 'status': 'incomplete'})
    if tamper == 'reset': a['resumeAfterUTC'] = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    write(prior / 'inputs.json', i); write(prior / 'acquisition.json', a)
    with pytest.raises(AssertionError):
        nv.acquire()
    assert world['fetched'] == []
