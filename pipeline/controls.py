"""Fetch only public, reproducible Lakeside and Kiawah API control inputs on the runner."""
import json, time, hashlib, urllib.request
from pipeline.common import cloud_only, read, write, sample, now

def main():
    cloud_only()
    candidates = [next(c for c in sample() if c['id']=='24925661-c5ad-4a89-87f2-b97a8f77fe83'), read('inputs/kiawah-control.json')]
    for c in candidates:
        values = {}
        for suffix in ('holes', 'features'):
            url=(f'https://api.opengolfapi.org/api/v1/features?course={c["id"]}' if suffix=='features' else f'https://api.opengolfapi.org/v1/courses/{c["id"]}/holes')
            for attempt in range(4):
                time.sleep(1.3 + (10 * attempt if attempt else 0))
                try:
                    with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent':'Crane-Course-Data/1.0'}), timeout=45) as response:
                        body=response.read(15_000_001)
                    if len(body)>15_000_000: raise ValueError('Control response too large')
                    value=json.loads(body)
                    if not isinstance(value.get(suffix), list): raise ValueError('Invalid control response')
                    break
                except Exception:
                    if attempt==3: raise
            values[suffix]=value
            if suffix=='holes' and c['id']==candidates[1]['id']:
                write(f'work/scorecards/{c["id"]}.json', {'response':value,'url':url,'sha256':hashlib.sha256(body).hexdigest(),'fetchedAt':now()})
        write(f'work/controls/control-{c["id"]}.json', {'candidate':c,'scorecard':values['holes'],'providerFeatures':values['features']})
if __name__=='__main__': main()
