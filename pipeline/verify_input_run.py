import json, os, urllib.request
from pipeline.common import cloud_only, write, sample, state_for

def main():
    cloud_only()
    run=os.environ['INPUT_RUN']; repo=os.environ['GITHUB_REPOSITORY']
    assert run.isascii() and run.isdigit()
    def get(path):
        request=urllib.request.Request('https://api.github.com/repos/'+repo+'/actions/runs/'+run+path, headers={'Authorization':'Bearer '+os.environ['GITHUB_TOKEN'], 'Accept':'application/vnd.github+json', 'User-Agent':'Crane-Course-Data/1.0'})
        with urllib.request.urlopen(request,timeout=30) as response: return json.load(response)
    meta=get('')
    assert meta['head_repository']['full_name']==repo and meta['head_branch']=='main'
    assert meta['event'] in ('workflow_dispatch','schedule') and meta['status']=='completed'
    assert meta['path']=='.github/workflows/build.yml'
    jobs=get('/jobs?per_page=100')['jobs']
    expected={'prepare'}|{f'states ({s})' for s in ({state_for(c) for c in sample()}-{None})|{'new-york','south-carolina'}}
    assert all(any(j['name']==name and j['conclusion']=='success' for j in jobs) for name in expected)
    write('work/input-run.json',{k:meta[k] for k in ('id','head_sha','created_at','html_url','path')})
    print('Reusing completed public extraction from trusted main run',run)
if __name__=='__main__': main()
