import argparse
import hashlib
import subprocess
import urllib.request
from pathlib import Path
from pipeline.common import cloud_only, STATE_NAMES, sample, state_for, read, write, now
from pipeline.extract import GolfHandler, measure

def acquire(state):
    cloud_only()
    assert state in STATE_NAMES.values()
    root = Path('work/extracts') / state
    root.mkdir(parents=True, exist_ok=True)
    filtered = root / 'golf.osm.pbf'
    metadata_path = root / 'source.json'
    url = f'https://download.geofabrik.de/north-america/us/{state}-latest.osm.pbf'
    if not filtered.exists() or not metadata_path.exists():
        raw = root / 'state.osm.pbf'
        headers = root / 'headers.txt'
        subprocess.run(['curl', '--fail', '--location', '--retry', '3', '--max-time', '1200', '--user-agent', 'Crane-Course-Data/1.0', '--dump-header', str(headers), '--output', str(raw), url], check=True)
        digest = hashlib.file_digest(raw.open('rb'), 'sha256').hexdigest()
        info = subprocess.check_output(['osmium', 'fileinfo', str(raw)], text=True)
        subprocess.run(['osmium', 'tags-filter', str(raw), 'nwr/golf', 'wr/leisure=golf_course', '-o', str(filtered), '--overwrite'], check=True)
        write(metadata_path, {'url': url, 'downloadedAt': now(), 'sha256': digest, 'fileInfo': info,
                             'httpHeaders': headers.read_text(), 'filter': ['nwr/golf', 'wr/leisure=golf_course']})
        raw.unlink()  # Only a transient downloaded public input; keep the small filtered cache.
    return root


def main():
    cloud_only()
    parser = argparse.ArgumentParser()
    parser.add_argument('state', choices=sorted(STATE_NAMES.values()))
    args = parser.parse_args()
    state = args.state
    root = acquire(state)
    filtered = root / 'golf.osm.pbf'
    metadata_path = root / 'source.json'
    handler = GolfHandler()
    handler.apply_file(str(filtered), locations=True, idx='flex_mem')
    rows = []
    for candidate in sample():
        if state_for(candidate) != state:
            continue
        path = Path('work/scorecards') / (candidate['id'] + '.json')
        scorecard = read(path) if path.exists() else {'error': 'Scorecard acquisition job did not produce a response'}
        row = measure(candidate, handler, scorecard)
        rows.append(row)
        print(candidate['name'], row['category'], row['ruleResult']['reason'], flush=True)
    write(f'work/results/{state}.json', {'state': state, 'source': read(metadata_path), 'parserErrors': handler.errors, 'rows': rows})

if __name__ == '__main__':
    main()
