"""Public Ozaukee near-tie: compare the Python feature metric to real Core Location."""
import json
import math
import subprocess
import sys
from pathlib import Path

import pytest
from pipeline.match import center, distance


def test_ozaukee_feature_distance_matches_core_location(tmp_path):
    if sys.platform != 'darwin':
        pytest.skip('Core Location reference requires a macOS runner')
    p = json.loads(Path('tests/fixtures/parity/ozaukee-feature.json').read_text())
    c = center(p['feature'])
    pairs = [[c, v] for path in p['paths'] for v in path['points']]
    source = tmp_path / 'main.swift'
    source.write_text('''import Foundation
import CoreLocation
let input = try JSONDecoder().decode([[[Double]]].self, from: Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[1])))
let result = input.map { pair in
    CLLocation(latitude: pair[0][1], longitude: pair[0][0]).distance(from: CLLocation(latitude: pair[1][1], longitude: pair[1][0]))
}
print(String(data: try JSONEncoder().encode(result), encoding: .utf8)!)
''')
    exe = tmp_path / 'distance'
    subprocess.run(['swiftc', str(source), '-o', str(exe)], check=True)
    inputs = tmp_path / 'pairs.json'; inputs.write_text(json.dumps(pairs))
    swift = iter(json.loads(subprocess.check_output([str(exe), str(inputs)], text=True)))
    results = []
    for path in p['paths']:
        py = min(distance(c, v) for v in path['points'])
        sw = min(next(swift) for _ in path['points'])
        def sphere(v):
            lat1,lat2 = math.radians(c[1]),math.radians(v[1])
            a = math.sin((lat2-lat1)/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(math.radians(v[0]-c[0])/2)**2
            return 6371000 * 2 * math.atan2(math.sqrt(a),math.sqrt(1-a))
        results.append({'hole':int(path['tags']['ref']), 'pythonMeters':py,'swiftMeters':sw,'sphereMeters':min(sphere(v) for v in path['points'])})
    print('OZAUKEE DISTANCES',json.dumps(sorted(results,key=lambda r:r['swiftMeters'])[:3]),flush=True)
    assert min(results,key=lambda r:r['pythonMeters'])['hole'] == min(results,key=lambda r:r['swiftMeters'])['hole'] == p['expectedSwiftHole']
