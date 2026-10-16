# Phase 3: conservative hole-numbering research

Run **Actions → Research missing hole numbering → Run workflow**. This manually
triggered workflow reuses verified state-extraction artifacts from phase two. It
does all processing and tests on a standard public GitHub runner, using only the
built-in token. It neither fetches player data nor changes the app or Pages dataset.

Two experiments are reported for every otherwise eligible numbering failure:

- **Mapped paths:** assign missing numbers to existing, directed OSM paths. Retain
  existing numeric refs and par tags; corroborate unnumbered starts with a mapped
  tee within 25 m. Use the same green matching and vertex-average center as phase
  two. Never overwrite duplicate/conflicting numbers or interpret compound refs.
- **Tee/green hypotheses:** retain known numbered paths, then investigate pairings
  of mapped tee and green polygons for other holes. Tee color is unknown, so every
  scorecard tee yardage is considered. These endpoint pairs do not reconstruct
  doglegs and are never converted into invented/exported hole paths.

Both searches require a one-to-one assignment of starts and greens and a closed
sequence with transfers of at most 300 m. Yardage tolerances are the larger of
20 yards or 15%/20%/25%/25% for par 3/4/5/6. Mapped paths compare to the longest
scorecard tee; endpoint hypotheses compare to any tee. Par 3 distances are at most
275 yards. These are deliberately fixed research assumptions, not authoritative
golf standards. Different physical tee platforms count as separate assignments.
Missing clubhouse/entrance evidence is explicitly reported; it is not invented.

The search tests **125% of the normal tolerance/transfer limits** to expose nearby
alternatives. It accepts a hypothesis only if the full relaxed search finds exactly
one assignment and that assignment also passes the strict checks. Two solutions
mean ambiguity. At most 20,000 nodes or 12 seconds per search; reaching either
budget always withholds uniqueness, even if one solution has already been found.
No greedy or beam search is allowed to turn omitted alternatives into certainty.

For all 32 phase-two published courses, blind controls mask hole 1, the final hole,
and then all numbers. The solver receives no saved matching answers or encoded
geometry. The original path/green IDs check exact recovery afterwards. Correct,
unresolved and incorrect recoveries are counted separately. Any incorrect unique
recovery fails the workflow. Real success and ambiguity fixtures are saved and
replayed by tests before the report is committed.

**A unique heuristic solution does not establish real-world numbering.** Public
OSM refs in masked controls provide ground truth for controls only. New courses
need independent numbering evidence before promotion. All findings are reports;
the 32-course published index remains unchanged, and the course schema continues
to allow only `numbering: "osm-mapped"`. A later confirmed inferred export must
explicitly use `numbering: "inferred"` and pass every existing geometry, schema,
Swift and Watch gate. This workflow cannot deploy to Pages.

Sources and reports: © OpenStreetMap contributors, Geofabrik, OpenGolfAPI; ODbL 1.0.
