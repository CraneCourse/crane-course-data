"""Fetch only public, reproducible Lakeside and Kiawah API control inputs on the runner.

Requests go through the repository-wide ledger: two attempts for network errors, none
after HTTP 429 or an exhausted reservation.
"""
import json, time, hashlib, urllib.request, urllib.error
from pipeline.common import cloud_only, read, write, sample, now
from pipeline.api_ledger import Budget

def main():
    cloud_only()
    budget = Budget()
    candidates = [next(c for c in sample() if c['id']=='24925661-c5ad-4a89-87f2-b97a8f77fe83'), read('inputs/kiawah-control.json')]
    for c in candidates:
        values = {}
        for suffix in ('holes', 'features'):
            url=(f'https://api.opengolfapi.org/api/v1/features?course={c["id"]}' if suffix=='features' else f'https://api.opengolfapi.org/v1/courses/{c["id"]}/holes')
            for attempt in range(2):
                budget.consume()
                if attempt: time.sleep(10)
                try:
                    with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent':'Crane-Course-Data/1.0'}), timeout=45) as response:
                        budget.observe(response.headers)
                        body=response.read(15_000_001)
                    if len(body)>15_000_000: raise ValueError('Control response too large')
                    value=json.loads(body)
                    if not isinstance(value.get(suffix), list): raise ValueError('Invalid control response')
                    break
                except urllib.error.HTTPError as exc:
                    budget.http_error(exc); raise
                except Exception:
                    if attempt==1: raise
            values[suffix]=value
            if suffix=='holes' and c['id']==candidates[1]['id']:
                write(f'work/scorecards/{c["id"]}.json', {'response':value,'url':url,'sha256':hashlib.sha256(body).hexdigest(),'fetchedAt':now()})
        write(f'work/controls/control-{c["id"]}.json', {'candidate':c,'scorecard':values['holes'],'providerFeatures':values['features']})
if __name__=='__main__': main()
