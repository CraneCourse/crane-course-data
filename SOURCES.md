# Public sources and terms record

Reviewed 2026-10-16. This records the public sources used and operational limits; it
is not permission to use unrelated sources or to assume a service has an SLA.

- **OpenStreetMap:** https://www.openstreetmap.org/copyright — ODbL 1.0; attribution
  and applicable share-alike obligations. Full license:
  https://opendatacommons.org/licenses/odbl/1-0/ . Course coordinates and numbering
  are volunteer contributions, not authoritative surveyed positions.
- **Geofabrik:** https://download.geofabrik.de/north-america/us.html — free OSM state
  PBF extracts. Store the exact file SHA-256, response headers, download time and PBF
  replication timestamp. Public extracts omit OSM user names, user IDs and changeset
  IDs. The pipeline additionally only retains geometry and golf/ref/name/par tags.
- **OpenGolfAPI:** https://www.opengolfapi.org/attribution/ and
  https://www.opengolfapi.org/docs/ — free, keyless directory and scorecards, with
  ODbL attribution. Scorecards are `/v1/courses/{id}/holes`. Current service repository:
  https://github.com/opengolfapi/api . Its documented free tier includes a 1,000/day
  request limit per IP. Runners change IP, so every workflow reserves requests in one
  repository-wide daily ledger (500/day, the limit the service's own headers report, committed before any request, never refunded)
  and spends them through one paced client: at most two attempts per request for
  network errors, none after HTTP 429. A 429, `X-RateLimit-Remaining: 0` or Retry-After
  stops all requests and blocks new reservations until the later reset. A 429 or service
  outage is an operational failure, never an empty scorecard. No promise of availability.
- **GitHub Actions:** https://docs.github.com/en/billing/concepts/product-billing/github-actions
  — standard hosted runners are free for public repositories, subject to platform
  limits. This does not mean unlimited concurrency/storage or free larger runners.
- **Future only:** USDA NAIP and USGS 3DEP public data; not downloaded in phase one.
  Source imagery extents and attribution must be preserved if later used.

`inputs/sample.json` and `inputs/baseline.json` contain only public directory facts
and course-validation outcomes from the October 16 audit. They contain no player
locations, round scores, app source, private account credentials, or device identifiers.
The baseline is retained verbatim for comparison; its `date` uses Apple's epoch and is
not the acquisition timestamp of the new Geofabrik/scorecard measurements.

Data and source-derived reports are offered under ODbL 1.0. © OpenStreetMap contributors.
Directory and scorecards provided by OpenGolfAPI; extracts provided by Geofabrik.
