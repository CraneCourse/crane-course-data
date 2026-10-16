"""Public sample selection and reproducible I/O; no player data inputs."""
import json
import os
from pathlib import Path
from datetime import datetime, timezone

# Explicit allowlist: never interpolate directory data into a download URL or shell.
STATE_NAMES = dict(item.split(':', 1) for item in '''AL:alabama AZ:arizona AR:arkansas CA:california CO:colorado CT:connecticut DE:delaware FL:florida GA:georgia ID:idaho IL:illinois IN:indiana IA:iowa KS:kansas KY:kentucky LA:louisiana ME:maine MD:maryland MA:massachusetts MI:michigan MN:minnesota MS:mississippi MO:missouri MT:montana NE:nebraska NV:nevada NH:new-hampshire NJ:new-jersey NM:new-mexico NY:new-york NC:north-carolina ND:north-dakota OH:ohio OK:oklahoma OR:oregon PA:pennsylvania RI:rhode-island SC:south-carolina SD:south-dakota TN:tennessee TX:texas UT:utah VT:vermont VA:virginia WA:washington WV:west-virginia WI:wisconsin WY:wyoming'''.split())

def cloud_only():
    if os.environ.get('GITHUB_ACTIONS') != 'true':
        raise SystemExit('Course processing is restricted to GitHub Actions. Do not run on a personal computer.')

def read(path):
    return json.loads(Path(path).read_text())

def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + '\n')

def now():
    return datetime.now(timezone.utc).isoformat()

def state_for(candidate):
    p = candidate['center']
    if not (24 <= p['lat'] <= 50 and -125 <= p['lon'] <= -66):
        return None
    raw = candidate.get('state', '').strip()
    if raw.upper() in STATE_NAMES:
        return STATE_NAMES[raw.upper()]
    name = raw.lower().replace(' ', '-')
    return name if name in STATE_NAMES.values() else None

def sample():
    return [entry['candidate'] for entry in read('inputs/sample.json')]
