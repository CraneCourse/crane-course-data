"""Frozen real boundary/scorecard failures replayed after cloud Swift validation."""
from pathlib import Path
from datetime import date
from pipeline.common import read
from pipeline.layouts import separate
from pipeline.reviewed_cards import facts
from pipeline.quality import check


def test_real_resolutions_keep_every_verified_path_and_green():
    reviewed=read('inputs/reviewed-scorecards.json')
    for file in Path('tests/fixtures/layouts').glob('*.json'):
        fixture=read(file); p=fixture['input']; cid=p['candidate']['id']
        cards=facts(reviewed[cid],date(2026,10,16)) if cid in reviewed else None
        resolved,error=separate(p,p['boundaries'],cards)
        assert error is None and resolved['python']['pass'], file.name
        assert check(resolved,resolved['python']['holes'],0) is None,file.name
        # JSON stores coordinate tuples as arrays; compare the serialized form.
        import json
        assert json.loads(json.dumps(resolved['python']['matchedHoles']))==fixture['expectedMatches'],file.name


def test_repaired_scorecards_do_not_bypass_remaining_geometry_failures():
    reviewed=read('inputs/reviewed-scorecards.json')
    for file in Path('tests/fixtures/layouts-withheld').glob('*.json'):
        fixture=read(file); p=fixture['input']; cid=p['candidate']['id']
        resolved,error=separate(p,p['boundaries'],facts(reviewed[cid],date(2026,10,16)))
        if resolved is not None:
            error=check(resolved,resolved['python']['holes'],0) if resolved['python']['pass'] else resolved['python']['reason']
        assert error==fixture['expectedReason'],file.name
