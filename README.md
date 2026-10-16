# Crane Course public course data

This repository measures and validates free public golf-course data for complete course downloads.
Phase two publishes only courses passing the full data gates; app integration is separate.

[Phase-two pipeline](PHASE2.md) · [Latest build report](reports/LATEST-BUILD.md) · [Published dataset](https://cranecourse.github.io/crane-course-data/)
The Crane Course app source remains private and is not included here.

[Latest report](reports/LATEST.md) · [Sources and terms](SOURCES.md) · [Measurement workflow](.github/workflows/measure.yml)

## Phase-one measurement (retained)

The fixed input sample contains 207 public directory candidates from a previous coverage
sweep: 17 passed the app's geometry checks and 190 did not. It was a selected sample,
not a randomized or representative national survey. Search strings identify public
courses and regions; they are not collected from app users.

A manually triggered GitHub Actions workflow:

1. Tests the diagnostic code on a standard hosted runner.
2. Fetches OpenGolfAPI scorecards sequentially, at most one request every 1.3 seconds,
   with bounded retries and a daily response cache.
3. Downloads Geofabrik extracts for every recognized contiguous-US state represented
   in the sample, plus New York and South Carolina. Three state jobs run at once.
4. Filters golf features and course areas with osmium, preserving referenced nodes.
   Raw PBF files are transient; small filtered extracts are cached daily.
5. Counts mapped features and diagnoses the current matching rules. Unique spatial
   course-area association uses containment or a single boundary within 500 m;
   overlapping candidates remain unresolved. Features must be covered by the selected
   boundary plus 100 m. An unassociated 3.5 km inventory is labelled separately.
6. Commits an attributed, per-course report, source checksums and acquisition times.

All processing and tests run in GitHub Actions. Entry points refuse local execution.
There are no app telemetry inputs, player locations, rounds, credentials, paid services,
Overpass queries, external vendors, in the measurement workflow. The phase-two build runs weekly. Standard public runners are
used; no larger runners or paid storage are enabled. Only the report job has write
permission through the default short-lived GITHUB_TOKEN.

Run **Actions → Measure public course data → Run workflow**. Jobs may take several
minutes per state. Failed downloads or missing jobs are explicitly reported, not
silently counted as absent mapping. Reports include the exact workflow commit and run.

## Phase-one limits (historical)

The phase-one matcher is a Python diagnostic, not a parity-certified Swift port.
A provisional pass is only potential for phase two. Boundary identity, Swift parity,
front/back containment, Watch size, imagery and complete export validation remain.
Phase two now verifies Lakeside/Kiawah real fixtures; see PHASE2.md and the build report.

An inference inventory count means only enough green/tee polygons exist. It does **not**
prove which hole they belong to. Even a unique solution under a heuristic model does not
prove real-world numbering. No inferred course will be published on that basis alone.
Green-center shifts are measured separately; matching still uses the app's vertex mean.
No change to green centers, app downloads or the App Store submission is made here.

## Licensing

Pipeline code: [MIT](LICENSE). Data, sample, source-derived reports and future course
files: [ODbL 1.0](LICENSE-DATA), © OpenStreetMap contributors, with OpenGolfAPI and
Geofabrik attribution. No imagery is downloaded or redistributed in this phase.
ODbL does not mandate GitHub specifically; public distribution here supports the
share-alike obligations and free standard Actions use.
