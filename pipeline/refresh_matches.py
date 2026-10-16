from pathlib import Path
from pipeline.common import cloud_only, read, write
from pipeline.full_match import match

def main():
    cloud_only()
    for p in Path('work/validation').glob('*.json'):
        if p.name.startswith('control-'): continue
        row=read(p); c=row['candidate']['center']
        row['python']=match(row['scorecard']['holes'],row['ways'],(c['lon'],c['lat']))
        write(p,row)
if __name__=='__main__': main()
