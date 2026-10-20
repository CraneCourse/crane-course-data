# Approved app catalog publication

This directory retains the explicitly approved data snapshot. Nightly survey output
is never promoted automatically. Run `Publish approved app catalog` manually on
main only after approval and native consumer validation.

The October 20 catalog contains 817 app courses. The 38-entry legacy index remains
available, and all existing course IDs and content are preserved. Every course and
both indexes share a publication version/timestamp because existing consumers
require this. Repackaging changed only the course envelope version/timestamp; it
did not modify geometry, identity, hole numbering, scorecards or source notices.
The public historical report is retained for existing links; it is not the current
app catalog and does not certify newly published courses.

`app-index.json` plus `site/` form the deployment. `approved-files.json` pins every
file; the workflow also pins the manifest hash. `expected-live.json` records the
previous deployment and prevents overwriting an unexpected catalog. A repeat of
the same approved deployment is allowed. No expired artifact is needed.

The publisher makes no OpenGolfAPI requests and does not run the survey, modify its
request ledger, commit pipeline reports or change its schedule. Its existing
publication concurrency group is separate from national validation. Keep future
app approvals explicit, retain legacy compatibility, and update the pinned hash
only after validation. The retained snapshot is data under LICENSE-DATA; pipeline
code remains under the repository's existing code license.
