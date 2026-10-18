# Imagery/download research (separate from national scorecard work)

Pinned input: national validation run 39085031615, source 672c8df11aa0b0b3e163800ae8696505e8030e16.
Compare with run 38987496757, source 91ff2944dc3f22d1a7161ca5987b97022063f919.
Only the 287 new full-gate passes (603 minus the original 316) are tested.
Both successful run identities/events and artifact report provenance are checked.
Fail closed on missing earlier passes, duplicate IDs or unexpected counts.
Frozen controls are never added to the selection.
The candidate manifest SHA-256 is checked before using each exported course.

This independent cloud harness follows the app's documented request shape: USDA NAIP
EPSG:4326, 2048×2048 JPEG, course path/feature extent plus 0.001 degrees, aspect adjustment
disabled, and one private uncached cookie session shared by each export/image pair.
It retains the *returned* extent, checks all course coordinates against it, decodes the
image, rejects completely uniform thumbnails and records suspicious dark/white patches.
These pixel heuristics are warnings, not proof of missing data. A dark water feature or
shadow can be legitimate. Pillow is not CoreGraphics; native decoder parity is not claimed.

Downloaded JSON/JPEG files are saved and reopened without a network client; hash, bounds
and image checks repeat. This does not test the app's CourseCache, UI, airplane mode,
Watch connectivity or elevation downloads. Those need separate integration/device tests.
The future public course-file HTTP endpoint is not deployed; artifact retrieval/hash
validation does not establish that future app endpoint's behavior.

One sequential client, 1.5 seconds between HTTP requests, no retries, at most 574 USDA
requests. HTTP 403/429/503 or five consecutive failures stop further requests, leaving
untested courses incomplete. No OpenGolfAPI requests, scorecard ledger writes, app
source uploads, course publishing, or local course processing. Full images are transient;
only reports and bounded attributed diagnostic previews are retained for seven days.

The workflow runs on this isolated research branch. It has read-only repository
permissions and writes no commits, so Claude's main-branch validation can continue.
Tests and live processing run only in GitHub Actions. Reports deliberately distinguish
smoke passes, visual-review warnings, and incomplete transfers. They do not certify
on-course accuracy or ongoing image availability.


October 18 continuation: retain the 28 actually attempted results from run
39132428260 (23 downloads, five blank Colorado images), then attempt only the 259
untested IDs. Verify the prior run's exact identity/source and artifact row membership.
Preserve prior provenance; cumulative ceiling stays 574 USDA requests across both
runs. A valid JPEG that is uniformly blank is an `imagery-rejected` course, not a
network failure: do not let five blank courses masquerade as a service outage.
Transport failures still trip the five-consecutive-failure circuit; HTTP
403/429/503 still stop immediately. No blank course is retried or promoted.
