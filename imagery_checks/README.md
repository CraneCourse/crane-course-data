# Imagery/download research (separate from national scorecard work)

Pinned input: national validation run 38987496757, source 91ff2944dc3f22d1a7161ca5987b97022063f919.
Only its 316 current-geometry full-gate passes are tested, not three older frozen controls.
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

One sequential client, 1.5 seconds between HTTP requests, no retries, at most 632 USDA
requests. HTTP 403/429/503 or five consecutive failures stop further requests, leaving
untested courses incomplete. No OpenGolfAPI requests, scorecard ledger writes, app
source uploads, course publishing, or local course processing. Full images are transient;
only reports and bounded attributed diagnostic previews are retained for seven days.

The workflow runs on this isolated research branch. It has read-only repository
permissions and writes no commits, so Claude's main-branch validation can continue.
Tests and live processing run only in GitHub Actions. Reports deliberately distinguish
smoke passes, visual-review warnings, and incomplete transfers. They do not certify
on-course accuracy or ongoing image availability.
