"""Publish only the reviewed snapshot. No survey, scorecard or geometry processing."""
from pathlib import Path, PurePosixPath
import argparse, hashlib, json, shutil, time, urllib.request, urllib.error, socket
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone

BASE = 'https://cranecourse.github.io/crane-course-data/'
# Bounded static packaging size; GitHub Pages allows 1 GB. Course-admission rules are separate.
MAX_SNAPSHOT_BYTES = 1_000_000_000
def digest(data): return hashlib.sha256(data).hexdigest()
def safe_path(value):
    p = PurePosixPath(value)
    assert value and not p.is_absolute() and '..' not in p.parts and str(p) == value
    return p

def verify_source(root, manifest):
    site = root / 'site'
    assert not site.is_symlink()
    paths = {}
    for p in site.rglob('*'):
        assert not p.is_symlink(), f'Symlink: {p}'
        if p.is_file(): paths[str(p.relative_to(site))] = p
    assert 'app-index.json' not in paths
    paths['app-index.json'] = root / 'app-index.json'
    assert set(paths) == set(manifest['files']), 'Unreviewed or missing publication file'
    for name, path in paths.items():
        safe_path(name)
        assert digest(path.read_bytes()) == manifest['files'][name], f'Hash mismatch: {name}'
    assert sum(p.stat().st_size for p in paths.values()) < MAX_SNAPSHOT_BYTES, 'Approved snapshot exceeds publication packaging limit'
    all_ids = set()
    for index_name, count_key in [('app-index.json', 'appCourses'), ('index.json', 'legacyCourses')]:
        index = json.loads(paths[index_name].read_bytes())
        assert index['dataVersion'] == manifest['catalogVersion'] and index['builtAt'] == manifest['builtAt']
        assert len(index['courses']) == manifest[count_key]
        assert len({e['id'] for e in index['courses']}) == len(index['courses'])
        assert len(index['sources']) >= 3
        if index_name == 'app-index.json': assert index['schemaVersion'] == 1
        for e in index['courses']:
            assert e['file'] == f"courses/{e['id']}.json"
            assert e['dataVersion'] == index['dataVersion'] and e['builtAt'] == index['builtAt']
            assert e['sha256'] == manifest['files'][e['file']]
            doc = json.loads(paths[e['file']].read_bytes())
            assert doc['candidate'] == {k:e[k] for k in ['id','name','city','state','center']}
            assert doc['builtAt'] == e['builtAt'] and doc['dataVersion'] == e['dataVersion']
            assert len(doc['holes']) == e['holes'] and len(doc['sources']) >= 3
            all_ids.add(e['id'])
    course_paths = {n for n in paths if n.startswith('courses/')}
    assert len(all_ids) == manifest['courseFiles']
    assert course_paths == {f'courses/{i}.json' for i in all_ids}
    return paths

# Only static Pages reads are retried. Survey/source acquisition is separate.
MAX_FETCH_ATTEMPTS = 4
MAX_TRANSIENT_RETRIES = 12  # Shared across one complete baseline verification.
MAX_RETRY_WAIT_SECONDS = 60
TRANSIENT_HTTP = {500, 502, 503, 504}

def retry_delay(value, attempt):
    delay = 2 ** attempt
    if value:
        try:
            requested = int(value)
        except ValueError:
            try:
                when = parsedate_to_datetime(value)
                if when.tzinfo is None: when = when.replace(tzinfo=timezone.utc)
                requested = (when - datetime.now(timezone.utc)).total_seconds()
            except (ValueError, TypeError, OverflowError):
                requested = 0
        delay = max(delay, requested)
    if delay > MAX_RETRY_WAIT_SECONDS:
        raise RuntimeError('Server Retry-After exceeds bounded publication wait; defer publication')
    return delay

def fetch(name, retry_budget=None):
    safe_path(name)
    url = BASE + name
    if retry_budget is None: retry_budget = {'remaining':MAX_TRANSIENT_RETRIES}
    for attempt in range(1, MAX_FETCH_ATTEMPTS + 1):
        req = urllib.request.Request(url, headers={'Cache-Control':'no-cache','User-Agent':'CraneCourse-approved-publication/1.0'})
        try:
            with urllib.request.urlopen(req, timeout=40) as response:
                assert response.status == 200 and response.url == url, f'Unexpected response: {url}'
                data = response.read(16_000_001)
                assert len(data) <= 16_000_000, f'Oversize response: {url}'
            if attempt > 1:
                print(json.dumps({'event':'fetch-recovered','url':url,'attempt':attempt}), flush=True)
            return data
        except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
            headers = getattr(error, 'headers', None) or {}
            code = getattr(error, 'code', None)
            reason = getattr(error, 'reason', error)
            transient = code in TRANSIENT_HTTP if code is not None else isinstance(reason, (TimeoutError, socket.timeout, ConnectionResetError, ConnectionAbortedError))
            retry_after = headers.get('Retry-After')
            diagnostic = {'event':'fetch-failed','url':url,'attempt':attempt,
                          'status':code,'error':str(error),'retryAfter':retry_after,
                          'server':headers.get('Server'),'requestID':headers.get('X-GitHub-Request-Id'),
                          'cache':headers.get('X-Cache'),'via':headers.get('Via')}
            print(json.dumps(diagnostic), flush=True)
            if isinstance(error, urllib.error.HTTPError): error.close()
            if not transient or attempt == MAX_FETCH_ATTEMPTS or retry_budget['remaining'] <= 0:
                raise RuntimeError(f'Cannot verify live file {url}; publication stopped') from error
            delay = retry_delay(retry_after, attempt)
            retry_budget['remaining'] -= 1
            print(json.dumps({'event':'fetch-retry','url':url,'waitSeconds':delay,
                              'remainingRetries':retry_budget['remaining']}), flush=True)
            time.sleep(delay)

def check_live(before, approved):
    retry_budget = {'remaining':MAX_TRANSIENT_RETRIES}
    def read(name): return fetch(name, retry_budget=retry_budget)
    current = digest(read('app-index.json'))
    candidates = [m['files'] for m in (before, approved)
                  if current == m['files']['app-index.json']]
    assert candidates, 'Live catalog changed; review before deploying'
    # A static-page cleanup can keep the same catalog hash. Require every file
    # to match one complete approved snapshot, rather than accepting a mixture.
    names = sorted(set().union(*(set(c) for c in candidates)))
    for ordinal, name in enumerate(names, 1):
        if name == '.nojekyll': continue  # GitHub Pages does not serve dotfiles.
        actual = current if name == 'app-index.json' else digest(read(name))
        candidates = [c for c in candidates if c.get(name) == actual]
        assert candidates, f'Live file changed or mixed deployment: {name}'
        if ordinal % 100 == 0 or ordinal == len(names):
            print(json.dumps({'event':'baseline-progress','checked':ordinal,'total':len(names),
                              'retriesUsed':MAX_TRANSIENT_RETRIES-retry_budget['remaining']}), flush=True)
        time.sleep(.1)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--check-live', action='store_true')
    parser.add_argument('--expected-manifest-sha256', required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    raw = (root/'approved-files.json').read_bytes()
    assert digest(raw) == args.expected_manifest_sha256, 'Publication approval manifest changed'
    approved = json.loads(raw)
    assert (approved['appCourses'],approved['legacyCourses'],approved['courseFiles']) == (2684,38,2692)
    paths = verify_source(root, approved)
    if args.check_live: check_live(json.loads((root/'expected-live.json').read_bytes()), approved)
    dist = root.parent/'dist'
    assert not dist.exists(), 'Refuse to merge with an old deployment directory'
    dist.mkdir()
    for name, source in paths.items():
        target=dist/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
    assert {str(p.relative_to(dist)):digest(p.read_bytes()) for p in dist.rglob('*') if p.is_file()} == approved['files']
    print(f"Prepared {approved['appCourses']} app courses and {approved['legacyCourses']} legacy entries; {len(paths)} verified files.")

if __name__ == '__main__': main()
