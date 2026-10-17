"""One sequential, paced API client. Errors never become empty scorecards.

Requests go through the repository-wide ledger (pipeline/api_ledger.py): at most two
attempts per card for network errors, none after HTTP 429 or an exhausted reservation.
A stopped acquisition fails the job instead of continuing.
"""
import hashlib
import time
import urllib.request
import urllib.error
from pathlib import Path
from pipeline.common import cloud_only, sample, state_for, read, write, now
from pipeline.api_ledger import Budget

BASE = 'https://api.opengolfapi.org/v1/courses/'

def main():
    cloud_only()
    budget = Budget()
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
        for attempt in range(2):
            budget.consume()  # Raises when stopped or exhausted; never retries around a limit.
            try:
                req = urllib.request.Request(url, headers={'User-Agent': 'Crane-Course-Data/1.0', 'Accept': 'application/json'})
                with urllib.request.urlopen(req, timeout=45) as response:
                    budget.observe(response.headers)
                    body = response.read(5_000_001)
                    if len(body) > 5_000_000:
                        raise ValueError('Response exceeds safety limit')
                    import json
                    data = json.loads(body)
                    if not isinstance(data, dict) or not isinstance(data.get('holes'), list):
                        raise ValueError('Missing holes array')
                    result.update(response=data, sha256=hashlib.sha256(body).hexdigest(), attempts=attempt + 1)
                    break
            except urllib.error.HTTPError as exc:
                result['error'] = f'HTTP {exc.code}; no retries'
                budget.http_error(exc)
                break
            except (urllib.error.URLError, ValueError, TimeoutError) as exc:
                result['error'] = str(exc)
                if attempt == 0:
                    time.sleep(10)
        if 'response' in result:
            result.pop('error', None)
        write(target, result)
        print(cid, 'received' if 'response' in result else 'SERVICE ERROR', flush=True)

if __name__ == '__main__':
    main()
