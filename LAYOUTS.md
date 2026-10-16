# Neighboring layouts and scorecard corrections

[Latest results](reports/LATEST-LAYOUT-RESOLUTION.md) · [Source audit](reports/LATEST-LAYOUT.md)

All extraction, matching, tests and Swift validation run in GitHub Actions. The app
and the approved App Store submission are unchanged. These are data-ready courses;
real-device downloads, aerial imagery, field accuracy and app integration remain
separate checks.

## Validated result

[Run 38978798804](https://github.com/CraneCourse/crane-course-data/actions/runs/38978798804)
passed all validation and deployed **38 distinct courses**, up from 32 with all
previous IDs retained. All 45 tests passed, along with actual Swift/Python parity,
Watch-size, schema and the existing real-course controls. Inputs were the frozen
147-course cloud audit; this was not a fresh national extraction.

The six additions are Scottsdale Silverado Golf Club, Pebble Beach Golf Links,
PGA West Private Jack Nicklaus Private Course, Pinehurst No. 2, Texas Rangers Golf
Club and Torrey Pines South. See the linked report for full per-course results.

Whistling Straits' scorecard is corrected but the course remains withheld: hole 6's
existing vertex-average center is outside its green. Pinehurst No. 5 remains
withheld because hole 5's mapped 165.2-yard path conflicts with its longest listed
tee of 428 yards. Neither failure is bypassed to increase coverage.

## What changed

- Match a directory entry to a uniquely named OSM boundary before matching holes.
  Nested duplicate boundaries can collapse only when their normalized names agree
  and exactly one boundary geometrically contains the others. Different possible
  layouts remain unresolved; a scorecard fit never chooses between them.
- Prefer hole paths entirely inside that boundary. Keep the existing 100 m boundary
  allowance only when it contains exactly one complete numbered layout. This removes
  neighboring duplicate holes without cutting paths or guessing their numbering.
- Decode the explicit `hole - #course` references used at Pinehurst. A matching
  numbered subcourse boundary, or a same-name parent with a complete set of explicit
  course references, is required. Original references and source IDs remain in the
  validation report. Plain unassigned paths cannot complete a parent subcourse.
- Withhold a newly resolved course if any mapped par conflicts with its scorecard.
- Correct two known provider scorecard mismatches using reviewed numeric facts from
  the clubs' official scorecards: Whistling Straits (Straits) and Texas Rangers.
  Complete tee rows must match printed totals. Source content hashes, review dates,
  source links and a six-month review expiry are required. A changed/unavailable or
  expired source stops publication for review; it never falls back to the known
  incorrect card. No scorecard artwork is republished.

## Validation and ongoing refreshes

The correction workflow requires all 147 audited inputs. Every candidate must pass
complete 9/18-hole matching, independent Swift/Python output parity, polygon and
front/center/back checks, common tees, Watch payload size, JSON schema, identity and
layout deduplication. The 32 original accepted layouts must remain accepted. Only a
successful full validation can deploy the complete dataset to Pages.

The recurring build uses the same boundary and scorecard correction code on fresh
extracts. A publication floor prevents older revalidation workflows or later source
regressions from silently removing previously validated layouts. Regressions leave
the current site in place and require investigation.

Real regression fixtures preserve source failures and expected path/green matches.
The first research run, 38977685678, was superseded because its artifact directory
was incorrect and no layout inputs were processed. The corrected workflow explicitly
rejects missing input sets; the earlier unchanged count is not a coverage result.

## What remains unresolved

The course-detail API supplies the same par sequences and no additional tee yardages
for the 115 failed entries; it does not repair their scorecards automatically. Other
conflicting cards/routings, ambiguous subcourses, missing numbered holes, missing
or shared greens and invalid geometry stay excluded. No inferred numbering, invented
tee lengths or changed green-center calculation is used.

Sources: © OpenStreetMap contributors (ODbL 1.0), Geofabrik, OpenGolfAPI, and the
individual official scorecard links in `inputs/reviewed-scorecards.json`.
