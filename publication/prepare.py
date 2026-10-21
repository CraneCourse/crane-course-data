"""Publish only the reviewed snapshot. No survey, scorecard or geometry processing."""
from pathlib import Path, PurePosixPath
import argparse, hashlib, json, shutil, time, urllib.request

BASE = 'https://cranecourse.github.io/crane-course-data/'
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
    assert sum(p.stat().st_size for p in paths.values()) < 250_000_000
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

def fetch(name):
    safe_path(name)
    url = BASE + name
    req = urllib.request.Request(url, headers={'Cache-Control':'no-cache','User-Agent':'CraneCourse-approved-publication/1.0'})
    with urllib.request.urlopen(req, timeout=40) as response:
        assert response.status == 200 and response.url == url
        data = response.read(16_000_001)
        assert len(data) <= 16_000_000
        return data

def check_live(before, approved):
    current = digest(fetch('app-index.json'))
    candidates = [m['files'] for m in (before, approved)
                  if current == m['files']['app-index.json']]
    assert candidates, 'Live catalog changed; review before deploying'
    # A static-page cleanup can keep the same catalog hash. Require every file
    # to match one complete approved snapshot, rather than accepting a mixture.
    for name in sorted(set().union(*(set(c) for c in candidates))):
        if name == '.nojekyll': continue  # GitHub Pages does not serve dotfiles.
        actual = current if name == 'app-index.json' else digest(fetch(name))
        candidates = [c for c in candidates if c.get(name) == actual]
        assert candidates, f'Live file changed or mixed deployment: {name}'
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
    assert (approved['appCourses'],approved['legacyCourses'],approved['courseFiles']) == (1055,38,1063)
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
