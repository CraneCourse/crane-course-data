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

## Cloud survey and validation workflow

`national.yml` takes the inventory run ID and its exact source commit as required
dispatch inputs (`inventory_run`, `inventory_sha`) and verifies that run's workflow,
branch, event, exact source commit and completion before reuse. Candidate and withheld
counts are read from that inventory report and cross-checked against its candidates
file; all 50 states and DC must reach a terminal page. (The October 2026 survey used
inventory run `38981104633`, 16,776 candidates.) A cloud canary
run (`38982553669`, source `231ad9b`) passed 61 tests, then stopped before state
extraction because the official Maryland extent does **not** establish DC coverage.
The corrected allowlist uses Geofabrik's dedicated
[District of Columbia extract](https://download.geofabrik.de/north-america/us/district-of-columbia.html),
whose official polygon is fetched, hashed and checked on the runner. DC directory
centers outside that extent remain unresolved. They do not invalidate other states.
There are 49 extracts for the contiguous 48 states and DC. AK/HI remain outside
current app geography/imagery limits.

The screen reuses filtered state extracts with at most three state jobs running.
A conservative way/boundary bounding-box index only reduces search work; the
existing exact distance, explicit identity and numbered-layout selection still
apply. Nine- and eighteen-hole layouts require their entire unique numbered set.
Enough way greens is preliminary evidence only. Relation greens, parser errors,
source dates and every candidate/state outcome are retained. Failed jobs count as
incomplete, never as absent geometry. Geometry candidates are **not** supported
courses. The screen does not change centers, matching thresholds or publication.

`national-validation.yml` separately takes the exact successful geometry run ID and
source SHA. One sequential client acquires public scorecards; four deterministic
matching shards run at most two at a time, then standard macOS Actions runs the
actual Swift kernel, existing Python quality checks, course/index schemas and Watch
encoding gates. All 38 published identities are replayed from pinned successful
validation run `38981324295` as separately labelled frozen controls. New layout
identities are deduplicated by actual matched path/green IDs, with published IDs
preferred. Fresh geometry results and frozen controls are counted separately.
Candidate export files are retained as reviewable artifacts; neither workflow
publishes or changes the existing 38-course catalog.

[OpenGolfAPI's anonymous limit](https://courses.opengolfapi.org/pricing) is 1,000
requests/day/IP. Acquisition therefore uses a shared ceiling of 800 calls per run
and UTC day across its resume chain (leaving headroom for the prior 115-request
inventory), 1.5-second pacing, bounded network timeouts and
no automatic retries. Remaining/reset headers and HTTP 429 stop new requests; a
resume waits for the source reset rather than rotating runner IPs to bypass it.
Successful saved cards can be reused with their original source dates and pinned
provenance. Optional `previous_run` resumes the same geometry survey without a new
code push. A later dispatch must name the latest recorded validation run for that
geometry survey; omitting the resume cannot reset its request ledger. A new geometry
survey's first validation may set `cards_from_run` instead (see Re-surveying). Budget and acquisition failures stay incomplete, with every candidate
accounted for. A complete geometry survey may precede complete scorecard validation.

The official [bulk schema](https://github.com/opengolfapi/data/blob/main/SCHEMA.md)
was inspected as a possible efficient source. Its April 2026 public release carries
per-hole pars/handicaps and explicitly excludes per-tee yardages, which these quality
gates require. Total course yardage cannot substitute for hole/tee yardage. No bulk
adapter, invented yardages or weakened gate was introduced.

### Re-surveying

Every hand-off between these workflows is a GitHub artifact with limited retention,
so dispatch them in order without long gaps:

1. **Inventory national course directory** (`catalog.yml`, no inputs). Its
   `national-directory-inventory` artifact expires after **7 days**; it also commits
   `reports/catalog-inventory-<run>.json` to `main`.
2. **Survey national mapped geometry** (`national.yml`) with `inventory_run` = that run
   ID and `inventory_sha` = its head SHA, within those 7 days. Its
   `national-geometry-evidence` artifact expires after **7 days**.
3. **Validate national geometry candidates** (`national-validation.yml`) with
   `geometry_run`/`geometry_sha` = the survey run, `previous_run` empty, and
   `cards_from_run` = the newest recorded validation run of the earlier survey, within
   the survey's 7 days and before that run's 30-day `national-acquisition` and
   `national-frozen-carry` artifacts expire. The nightly schedule then resumes the new
   run as usual; later manual dispatches use `previous_run` only.

`cards_from_run` is accepted only for a geometry survey with no recorded validation run,
only without `previous_run`, and only when it names the newest recorded validation run
of a different survey on the same frozen baseline. Its artifact must match its committed
acquisition summary. A saved card is reused only for a current geometry candidate with
the same course ID whose card would otherwise be fetched; reviewed official cards and
frozen controls still take their existing paths. Reused cards keep their recorded source
and date and are labelled `carriedFromRun` (`carried-from-earlier-survey` in reports,
`carriedCards` in counts). Cards for IDs that are no longer geometry candidates are
ignored. Geometry is matched fresh, with the same gates. Every other candidate is
requested through the ledger exactly as before. While the original frozen-baseline
artifacts of run `38981324295` are gone (since October 2026), a new survey needs
`cards_from_run`: with neither `previous_run` nor `cards_from_run`, validation stops
before reserving any requests because the frozen 38-course payloads are missing.

## Request ledger, border accounting and report labels (Claude, October 17, 2026)

Corrections from `docs/national-survey-independent-review.md` in the private app repo:

- **Durable request budget.** Every OpenGolfAPI caller (inventory, scorecards, controls,
  layout details, national validation) now reserves requests in a repository-wide daily
  ledger, `ledger/opengolfapi/<UTC day>.json`, before making any. A reservation is
  committed to `main` first, keyed by workflow/run/attempt, and never refunded, so a
  failed run still counts. All workflows share 500 requests per UTC day: the documented
  limit is 1,000, but live responses report `X-RateLimit-Limit: 500`. A caller
  without a reservation for the current day refuses to request (fail closed).
  429, any error carrying Retry-After (e.g. 503), `X-RateLimit-Remaining: 0` and the later
  of Retry-After/reset stop all requests and block new reservations until the reset passes,
  however many days away it is. A stop is committed to the ledger at once; other active
  jobs check the ledger every 25 requests and stop too. Processes in one job (scorecards,
  then controls) resume the same recorded usage, so a grant is never spent twice. There
  are no retries around a limit and no IP rotation.
- **Daily resume chain.** Each 00:20 UTC run resumes the latest manual or scheduled
  validation run; runs from other workflows, branches or repositories are refused.
- **Weekly build paused.** `build.yml` runs on manual dispatch only. Its old Monday
  schedule rebuilt only the frozen catalog and would share the daily budget with national
  validation. Re-enable it only with catalog-aware revalidation that covers newly
  approved courses and the three published par conflicts.
- **Build refresh behavior.** A `build.yml` run reserves only the cards its
  daily cache lacks, tries a card at most twice on network errors and never after 429.
  If the ledger or source limit stops it, the build fails visibly. It never
  publishes empty or stale-as-new scorecards. Published files stay as they are until a
  later run succeeds.
- **Preserved acquisition evidence.** Validation commits
  `reports/national-acquisition-<run>.json` (counts, remaining IDs, reset time) right after
  scorecard acquisition, before matching can fail. A later dispatch must resume the latest
  run found in either acquisition summaries or validation reports.
- **Border accounting.** Before acquisition, `pipeline/national_border.py` fetches each
  extract's official Geofabrik `.poly`, records its hash, and tests a conservative envelope
  around every directory center's 3.5 km search area. If the envelope is not fully inside
  the assigned extract, the entry is `border-unresolved`; if the center is outside it, the
  entry is `outside-extract`. Neither is absent mapping. Border-unresolved geometry
  candidates are not sent through acceptance and spend no requests. Every directory ID
  in the survey (16,776 in October 2026) keeps an entry.
- **Report labels.** Validation reports lead with full-gate passes on newly surveyed
  geometry. They count layouts and distinct course boundaries, and state each scorecard's
  provenance and dates (reused cards keep their original dates). Frozen controls, controls
  that fail on current geometry, the candidate-export file count and preliminary
  directory-entry totals (aliases included) are reported separately and labelled.
- **Multi-day resumes.** At 500 requests per UTC day, scorecards for the 5,957 geometry
  candidates need several days, longer than the seven-day retention of the survey and
  frozen-baseline artifacts. Each validation run therefore re-uploads both as 30-day
  carry artifacts, and a resume falls back to the previous run's carry copy once an
  original expires. Identity checks (`geometry_inputs`, `baseline_ids`) still run on every use.
- **Daily resume.** `national-validation.yml` also runs at 00:20 UTC. A `resolve` job
  continues the latest recorded run (from acquisition summaries or reports) with up to 500
  requests. It does nothing when that run completed validation, when its recorded source
  reset is still in the future, or when that run did not succeed, so a failure waits for
  a person instead of repeating daily. Manual dispatch inputs work as before.

