"""Public Ozaukee near-tie: compare the Python feature metric to real Core Location."""
import json
import subprocess
import sys
from pathlib import Path

import pytest
from pipeline.match import center, distance
from pipeline.national_validation import full_gate, feature_partition_differs
from copy import deepcopy


def test_ozaukee_distance_disagreement_is_not_forced_to_match(tmp_path):
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
        results.append({'hole':int(path['tags']['ref']), 'pythonMeters':py,'swiftMeters':sw})
    print('OZAUKEE DISTANCES',json.dumps(sorted(results,key=lambda r:r['swiftMeters'])[:3]),flush=True)
    assert min(results,key=lambda r:r['pythonMeters'])['hole'] == p['originalPythonHole']
    assert min(results,key=lambda r:r['swiftMeters'])['hole'] == p['expectedSwiftHole']


def partition_fixture():
    from test_export import quality_fixture
    payload, holes = quality_fixture()
    reference = {'pass': True, 'holes': deepcopy(holes), 'watchBytes': 0}
    moved = reference['holes'][0]['features'].pop()
    reference['holes'][1]['features'].append(moved)
    return payload, reference


def test_optional_feature_repartition_rejects_course():
    payload, reference = partition_fixture()
    assert feature_partition_differs(payload['python']['holes'], reference['holes'])
    doc, error = full_gate(payload, reference, '2026-10-24T00:00:00Z', 'test')
    assert doc is None
    assert error == 'Swift/Python feature assignment differs; course withheld'


@pytest.mark.parametrize('kind', ['par', 'front', 'missing-feature', 'changed-feature', 'extra-feature'])
def test_other_parity_errors_remain_fatal(kind):
    payload, reference = partition_fixture()
    if kind == 'par':
        reference['holes'][0]['par'] += 1
    elif kind == 'front':
        reference['holes'][0]['front']['lat'] += .0001
    elif kind == 'missing-feature':
        reference['holes'][1]['features'].pop()
    elif kind == 'changed-feature':
        reference['holes'][1]['features'][-1]['kind'] = 'water'
    else:
        reference['holes'][1]['features'].append(deepcopy(reference['holes'][1]['features'][-1]))
    with pytest.raises(AssertionError, match='full Hole mismatch'):
        full_gate(payload, reference, '2026-10-24T00:00:00Z', 'test')


def test_decision_mismatch_remains_fatal():
    payload, reference = partition_fixture()
    reference['pass'] = False
    with pytest.raises(AssertionError, match='decision mismatch'):
        full_gate(payload, reference, '2026-10-24T00:00:00Z', 'test')
