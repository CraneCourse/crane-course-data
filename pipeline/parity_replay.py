"""Read-only replay of saved national full gates; no acquisition, exports or deployment."""
import hashlib
import json
import os
from pathlib import Path
import sys
import urllib.request
from concurrent.futures import ProcessPoolExecutor
from pipeline.common import cloud_only, read, write
from pipeline.national_validation import full_gate

OZAUKEE = 'bc3df947-b18d-4684-9316-a9f0181fff19'


def verify(run_id):
    cloud_only()
    assert run_id.isascii() and run_id.isdigit()
    repo = os.environ['GITHUB_REPOSITORY']
    assert repo == 'CraneCourse/crane-course-data'
    def get(path, raw=False):
        req = urllib.request.Request(f'https://api.github.com/repos/{repo}/{path}', headers={
            'Authorization': 'Bearer ' + os.environ['GITHUB_TOKEN'],
            'Accept': 'application/vnd.github.raw+json' if raw else 'application/vnd.github+json'})
        with urllib.request.urlopen(req, timeout=45) as response:
            return response.read() if raw else json.load(response)
    run = get(f'actions/runs/{run_id}')
    assert run['head_repository']['full_name'] == repo and run['head_branch'] == 'main'
    assert run['path'] == '.github/workflows/national-validation.yml'
    assert run['event'] in ('schedule', 'workflow_dispatch') and run['status'] == 'completed'
    reference_hashes = {}
    for file in ('reference/AppKernel.swift', 'reference/main.swift'):
        original = get(f'contents/{file}?ref={run["head_sha"]}', raw=True)
        assert Path(file).read_bytes() == original, 'Replay reference differs from saved Swift execution'
        reference_hashes[file] = hashlib.sha256(original).hexdigest()
    write('work/parity-replay/source.json', {'run': run_id, 'sourceCommit': run['head_sha'], 'referenceHashes': reference_hashes})


def evaluate(task):
    path, ref = task
    p = read(path)
    try:
        doc, error = full_gate(p, ref, '2026-10-24T00:00:00Z', 'read-only-replay')
        return {'id': path.stem, 'pass': doc is not None and error is None, 'reason': error}
    except Exception as exc:
        return {'id': path.stem, 'executionError': f'{type(exc).__name__}: {exc}'}


def replay(run_id):
    cloud_only()
    source = read('work/parity-replay/source.json')
    assert source['run'] == run_id
    root = Path('work/saved-parity')
    original = read(root / 'reports' / f'national-validation-{run_id}.json')
    assert original['sourceCommit'] == source['sourceCommit']
    assert original['errors'] == [{'id': OZAUKEE, 'reason': 'AssertionError: Swift/Python full Hole mismatch'}]
    swift = read(root / 'work/swift.json')
    files = sorted((root / 'work/validation').glob('*.json'))
    assert len(files) == len(swift) and {p.stem for p in files} == set(swift)
    with ProcessPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(evaluate, ((p, swift[p.stem]) for p in files), chunksize=8))
    by_id = {r['id']: r for r in results}
    prior_passes = set(original['freshPassedIDs'])
    unchanged = all(by_id[cid]['pass'] for cid in prior_passes)
    oz = by_id[OZAUKEE]
    errors = [r for r in results if 'executionError' in r]
    summary = dict(source, replayed=len(results), originalFreshPasses=len(prior_passes),
                   originalFreshPassesPreserved=unchanged, ozaukee=oz, executionErrors=errors,
                   requests=0, published=False, results=results)
    write('work/parity-replay/results.json', summary)
    print(json.dumps({k:v for k,v in summary.items() if k != 'results'}), flush=True)
    assert unchanged and not errors
    assert not oz['pass'] and oz['reason'] == 'Swift/Python feature assignment differs; course withheld'
    # All 38 frozen published controls must still pass; negative controls remain rejected.
    baseline = root / 'work/national-validation/baseline'
    baseline_ids = {p.stem for p in baseline.glob('*.json') if not p.stem.startswith('control-')}
    assert len(baseline_ids) == 38
    assert all(by_id['baseline-' + cid]['pass'] for cid in baseline_ids)
    assert not by_id['control-24925661-c5ad-4a89-87f2-b97a8f77fe83']['pass']
    assert not by_id['control-2f8238b3-85d2-4d54-b1af-779dec846381']['pass']


if __name__ == '__main__':
    command, run_id = sys.argv[1:]
    if command == 'verify': verify(run_id)
    elif command == 'replay': replay(run_id)
    else: raise SystemExit('Unknown command')
