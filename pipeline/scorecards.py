"""One sequential, paced API client. Errors never become empty scorecards."""
import hashlib
import time
import urllib.request
import urllib.error
from pathlib import Path
from pipeline.common import cloud_only, sample, state_for, read, write, now

BASE = 'https://api.opengolfapi.org/v1/courses/'

def main():
    cloud_only()
    for candidate in sample():
        if not state_for(candidate):
            continue
        cid = candidate['id']
        import uuid
        uuid.UUID(cid)
        target = Path('work/scorecards') / (cid + '.json')
        if target.exists() and 'response' in read(target):
            continue
        url = BASE + cid + '/holes'
        result = {'url': url, 'fetchedAt': now()}
        for attempt in range(4):
            time.sleep(1.3)
            try:
                req = urllib.request.Request(url, headers={'User-Agent': 'Crane-Course-Data/1.0', 'Accept': 'application/json'})
                with urllib.request.urlopen(req, timeout=45) as response:
                    body = response.read(5_000_001)
                    if len(body) > 5_000_000:
                        raise ValueError('Response exceeds safety limit')
                    import json
                    data = json.loads(body)
                    if not isinstance(data, dict) or not isinstance(data.get('holes'), list):
                        raise ValueError('Missing holes array')
                    result.update(response=data, sha256=hashlib.sha256(body).hexdigest(), attempts=attempt + 1)
                    break
            except (urllib.error.URLError, ValueError, TimeoutError) as exc:
                result['error'] = str(exc)
                if attempt < 3:
                    retry = getattr(exc, 'headers', {}).get('Retry-After', '') if getattr(exc, 'headers', None) else ''
                    time.sleep(min(120, max(10 * 2 ** attempt, int(retry) if retry.isdigit() else 0)))
        if 'response' in result:
            result.pop('error', None)
        write(target, result)
        print(cid, 'received' if 'response' in result else 'SERVICE ERROR', flush=True)

if __name__ == '__main__':
    main()
