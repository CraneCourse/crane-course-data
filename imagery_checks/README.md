# Isolated imagery/download research

October 19 batch: completed national run 39187270851 minus 39085031615;
898 current passes minus 603 earlier passes = exactly 295 new IDs.

Cloud only. Uses existing artifacts and sequential USDA requests (at most 590),
with 1.5-second pacing and no automatic retries. No OpenGolfAPI requests, ledger
writes, national-workflow changes, deployment or course publication. Retains
returned extents, strict image checks, clipping checks, saved-file reopening and
rate/transport stop conditions. Uniform imagery is rejected without stopping
unrelated courses. Pixel warnings remain held for review.

Push trigger is limited to this existing research branch. Read-only job permissions
and a separate concurrency group prevent writes or cancellations of the national
survey. Native app checks run separately only for clean imagery candidates.
