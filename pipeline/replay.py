import shutil, subprocess
from pathlib import Path
from pipeline.common import cloud_only, read
from pipeline.quality import equivalent

def main():
    cloud_only()
    fixtures=list(Path('tests/fixtures').glob('extract-*.json'))
    if not fixtures:
        print('First build: real controls are captured and validated in this run, then committed for replay.')
        return
    for p in fixtures:
        shutil.copyfile(p,Path('work/replay')/p.name.removeprefix('extract-'))
    for p in Path('tests/fixtures').glob('control-*.json'):
        shutil.copyfile(p,Path('work/replay')/p.name)
    subprocess.run(['/tmp/course-reference','work/replay','work/replay-results.json'],check=True)
    result=read('work/replay-results.json')
    from pipeline.export import validate_controls
    payloads={p.name.removeprefix('extract-')[:-5]:read(p) for p in fixtures}
    validate_controls(result,payloads)
    for cid,p in payloads.items():
        assert result[cid]['pass']==p['python']['pass']
        if result[cid]['pass']: assert equivalent(result[cid]['holes'],p['python']['holes'])
    print('Frozen Lakeside and Kiawah controls pass Swift replay.')
if __name__=='__main__': main()
