import json
import os
from pipeline.common import cloud_only, sample, state_for, write

def main():
    cloud_only()
    states = sorted({state_for(c) for c in sample()} - {None})
    # SC remains in the pilot even if a future sample lacks it.
    states = sorted(set(states) | {'new-york', 'south-carolina'})
    write('work/selection.json', {'states': states, 'sampleCount': len(sample())})
    with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
        output.write('states=' + json.dumps(states) + '\n')
    print(f'{len(sample())} public directory candidates; {len(states)} state extracts. No app/player inputs.')

if __name__ == '__main__':
    main()
