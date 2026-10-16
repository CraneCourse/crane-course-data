# Toward broad U.S. coverage

The launch target is broad U.S. coverage, not a regional pilot. Directory entries,
Python hypotheses, fully validated course files and field-tested downloads are
separate measures. The selected 207-course failure-heavy sample cannot estimate a
national acceptance rate.

## Next order of work

1. Inventory all 50 state listings and DC with a single paced client, explicit
   pagination checks and a shared request budget. Validate coordinate fields against
   the live response as well as the documented schema. Preserve source URLs and
   checksums; retain only public course identity/location fields. A listing is not
   evidence of a complete usable course.
2. Survey mapped geometry across the contiguous states in GitHub Actions. Start
   with explicit course identity, complete numbered layouts and green availability;
   fetch scorecards for plausible candidates rather than spending API requests on
   every unmapped directory entry. Reuse filtered state extracts and a bounded
   spatial index. Keep state failures distinct from missing mapping. DC requires an
   explicitly verified extract mapping; AK/HI require a separate imagery/geography
   design because the current app's limits exclude them.
3. Validate complete scorecards, polygon geometry, hole/green identity and all
   existing Swift/schema/Watch gates. Report accepted and excluded counts by state,
   plus reasons and source dates. Preserve existing course IDs. Source/routing
   mismatches stay excluded until independent evidence resolves them.
4. Integrate the validated index in a separate app change. Check imagery downloads,
   offline use and phone/Watch synchronization on real devices, plus a geographic
   sample of course downloads and independent layout checks. Keep existing saved
   rounds/downloads compatible.
5. Before paid launch, provide a public course-availability lookup and make the
   coverage limit clear. Decide readiness from geographic coverage and download
   reliability, not just a large total. Submit the integrated app build to Apple
   review; the current approved binary does not use this dataset.

## Current findings

The area-centroid study (`reports/center-study-38980632573.md`) tested all 147 saved
inputs. It recovered no additional layouts. All 38 published controls still passed
Python quality checks, with no changed path/green assignment. This is research only;
centers and production matching remain unchanged. Whistling Straits and the Golf
Club at Texas A&M still have a matched centroid outside a green.

The first blockers among 109 nonpublished entries were 63 incomplete/ambiguous
numbered layouts, 29 boundary identities, six card/routing conflicts, five missing
or ambiguous greens, two polygon/center failures and four other entries (including
aliases). These overlap later failures and are not national prevalence figures.

The official Pinehurst No. 5 card repairs the spurious provider `web` tee row.
Five complete tee rows agree between the official HTML and linked PDF; Gold is
withheld because the official sources disagree. The corrected course still fails
hole 18 green matching. Do not count a repaired scorecard as a supported course.

The initial national inventory (`38980424240`) completed pagination and counted
16,799 listing IDs, but its coordinate parser followed the OpenAPI names instead
of the live `lat`/`lng` fields, so zero usable candidates were produced. That run is
superseded for candidate selection. The corrected parser handles both explicit
schemas, rejects conflicts and stops acquisition if its first page cannot produce
any valid candidates. No guessed coordinates or source-schema fallback is allowed.

The corrected inventory ([run 38981104633](https://github.com/CraneCourse/crane-course-data/actions/runs/38981104633),
source `dec9d728641ce1ec1a80d575e64b11a1e598a525`) produced **16,776 valid unique
candidates** from 16,799 listing IDs. All 50 states and DC reached terminal pages
in 115 paced requests; 23 entries with missing coordinates were withheld.
See [the state report](reports/catalog-inventory-38981104633.md). These are directory
candidates, not supported courses. The candidate artifact expires after seven days;
a later cloud sweep must use it before expiry or refresh the inventory.

Final [validation and publication run 38981324295](https://github.com/CraneCourse/crane-course-data/actions/runs/38981324295)
passed all 57 tests, actual Swift parity and deployment. The catalog remains **38**;
there were no additions from this research pass. The original failure fixtures for
Pinehurst No. 5 and Whistling Straits now guard against publishing either while its
remaining geometry failure persists.

## Schedule uncertainty

A national measurement pass is the next estimation milestone. Broad launch readiness
cannot be dated from this selected sample. Mapping missing from free sources may
require substantial additional work, and better code cannot guarantee its recovery.
There is no promised national supported count or launch date. All processing and
validation stays on standard public GitHub Actions runners; no player data, paid
sources, vendor outreach or local course processing is used.
