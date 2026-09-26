# Rehydration is verified: counts, the mutation battery, and the open daemon decision

**Written 2026-09-26, Task 8 of the rehydration plan, closing out `spec/rehydration`;
amended the same day by the whole-branch review's fix wave.** As of the fix
wave's final commit (the one that wrote this sentence), the branch is
18 commits past `main` (`git log --oneline main..HEAD`, spec and plan
commits included): 27 files changed, 6329 insertions, 299 deletions.
**It is not merged** — integration is a decision for the requester
(`superpowers:finishing-a-development-branch`), not made here.
*Corrected 2026-09-26: the requester chose to merge; merged as `5cd0c79`, with
the residual the fix wave's re-review reproduced still open — `timing_resolved`
requires only a `TimingProvenance` row, so a failed or crashed per-system
`SystemTimebase` key on a force-freed session can later write a permanent
`no_recording`. Where this document says a session is never freed before its
timebase stages ran on real files, read it as overclaiming until that is closed.
Closed the same day by `fix/timing-resolved-every-system`: `timing_resolved`
now also requires a `SystemTimebase` row for every `core.AcquisitionSystem`,
and the wording holds.*

## The one-paragraph version

`wlpp rehydrate` restores a reclaimed session from its NAS artifact to the
exact path `Ingestion.session_dir` recorded, byte for byte, and `wlpp reclaim
--no-dry-run --confirm <session> --nas-root <mount>` now deletes the scratch
copy, behind a fresh proof against the NAS artifact that no force can skip.
Tasks 1–7 built this and were reviewed per-task; this task re-verified the
whole branch rather than trusting the per-task reviews to compose. Both
interpreters are green, and all 13 mutations the controller specified were
caught by the named test — no coverage gap found. A whole-branch review then
found one Critical and one Important defect that the per-task reviews had
not; the fix wave below closes both, with five more mutations, all caught.

## Both suites, verified fresh for this task

`__pycache__` cleared, `PYTHONDONTWRITEBYTECODE=1`, from the repo root, Docker
running for the MySQL testcontainer:

- **3.11** (`.venv/bin/python`, 3.11.15): `.venv/bin/python -m pytest -q` →
  **1467 passed, 11 skipped, 1 deselected, 1 xfailed** (303s).
  `.venv/bin/python -m pytest tests/schema/test_guardrails.py -q` →
  **10 passed**.
- **3.13**: `uv pip compile pyproject.toml --extra dev --python-version 3.13`
  resolved fresh against a `uv venv --python 3.13` (real CPython **3.13.9**,
  from `/opt/anaconda3/bin/python3` via `uv`'s own interpreter discovery —
  not substituted). `-e . --no-deps` installed this checkout into it. Full
  suite: **1466 passed, 13 skipped, 1 xfailed** (315s).

Both green; the same 1480 tests collected on each side. The split between
`skipped`/`deselected` differs by interpreter (11+1 on 3.11 against 13 on
3.13) the same way it has on every prior branch's dual-interpreter run
recorded in `docs/CHECKPOINT.md` — an env-gated real-recording check and the
pre-existing `slow`-marked Kilosort test resolve to `deselected` under one
interpreter's collection and `skipped` under the other. Not a new finding;
recorded here only so a reader does not mistake the differing counts for a
divergence between interpreters.

**After the whole-branch review's fix wave**, the same way: 3.11
`.venv/bin/python -m pytest -q` → **1471 passed, 11 skipped, 1 deselected, 1
xfailed** (335s); 3.13, in the same `venv_ci` Task 8 resolved (not
re-resolved for the fix wave) → **1470 passed, 13 skipped, 1 xfailed**
(303s). The four new tests on each side, nothing else moved; 1484 collected
on each.

## The mutation battery: 13 mutations, 13 caught, 0 survived

Per mutation: apply the literal source edit, clear `__pycache__`, run
`PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest <named test ids> -q`,
record the failure, `git checkout -- <file>`, re-run to confirm the pass
returns. The table below is the **controller's corrected** mutation table —
review changed code after the plan was written, so five rows differ from the
brief's own table: row 5 is retargeted (the brief's own mutation was
`target = staging / session_path.name` → `target = session_path`, caught by
`test_a_failure_part_way_through_leaves_nothing`; review's staging-mkdir fix
moved the vulnerable line, so the mutation that now exercises the same
neighborhood is `target.mkdir()`'s placement, caught by
`test_a_failure_creating_the_staged_session_leaves_no_leftover`); row 10 is
retargeted (the brief's own comparison was `str(Path(session_dir)) !=
recorded`; review changed the production comparison itself to
`Path(session_dir) != Path(recorded)`, so the mutation follows it — the same
test still catches it); and rows 11–13 are new, none of which the ten-row
brief listed at all. Rows 2 and 6 are unchanged from the brief.

| # | mutation | caught by | result |
|---|---|---|---|
| 1 | `scratch.free_session`: `if not proof.passed:` → `if False:` | `test_reclaim_refuses_a_nas_copy_that_changed_since_archiving` | CAUGHT |
| 2 | `reclaim.reclaim_conditions`: `artifact_present` gains `overridable=True` | `test_pins_condition_kinds_to_production`, `test_a_force_does_not_free_a_session_with_no_artifact` | CAUGHT (both) |
| 3 | `reclaim.reclaim_conditions`: `canonical_nwb_present` passes (`False`→`True`) | `test_canonical_nwb_present_fails_until_phase_3`, `test_report_names_the_missing_nwb_for_an_unforced_session` | CAUGHT (both) |
| 4 | `reclaim.blocking`: drop `and not (c.overridable and predicate.forced)` | `test_each_condition_blocks_on_its_own_unless_forced_and_overridable` | CAUGHT |
| 5 | `rehydrate.rehydrate_session`: move `target.mkdir()` back OUTSIDE the `try` | `test_a_failure_creating_the_staged_session_leaves_no_leftover` | CAUGHT |
| 6 | `rehydrate.rehydrate_session`: delete `refuse_leftovers(session_path)` | `test_rehydrate_refuses_a_leftover_staging_directory` | CAUGHT |
| 7 | `rehydrate._check`: delete the `if markers != expected:` block | `test_a_done_marker_that_disagrees_with_the_recorded_digests_fails` | CAUGHT |
| 8 | `proof.prove_artifact`: `exclude=frozenset({SENTINEL_NAME})` → `exclude=frozenset()` | `test_an_intact_artifact_is_proven_file_by_file` | CAUGHT |
| 9 | `verify.iter_reconstruct`: `range(0, array.shape[0], rows)` → `range(0, array.shape[0] - 1, rows)` | `test_a_stream_rebuilds_chunk_by_chunk_including_a_final_partial_chunk` | CAUGHT |
| 10 | `scratch.free_session`: `Path(session_dir) != Path(recorded)` → `False` | `test_reclaim_refuses_a_symlinked_spelling_of_the_session` | CAUGHT |
| 11 | `scratch.free_session`: `if unarchived:` → `if False:` | `test_reclaim_refuses_a_file_added_after_archiving`, `test_reclaim_refuses_a_file_changed_after_archiving` | CAUGHT (both) |
| 12 | `rehydrate.session_for_path`: delete the exact-match filter line | `test_rehydrate_refuses_a_differently_cased_spelling_of_the_path` | CAUGHT |
| 13 | `cli/main.py` `reclaim` dispatch: delete the `refuse_leftovers(session_dir)` try/except before the `is_dir()` guard | `test_an_interrupted_removal_is_named_by_the_next_reclaim` | CAUGHT |

No survivor. `git status` is clean after all 13 reverts — confirmed
separately, not merely asserted.

## The four rulings of spec §0

1. **A person frees scratch, for now.** Only `wlpp reclaim --no-dry-run
   --confirm <session>` deletes; the daemon never does — automatic
   reclamation is a later branch.
2. **Reclamation frees the whole session**, not only its bulk streams,
   because the canonical NWB carries eye, timing and event data near their
   native rate and everything else regenerates from the archive.
3. **Reclamation waits for the canonical NWB** — a condition the code did
   not have; this design adds `canonical_nwb_present`, failing until Phase 3,
   so real deletion is built and tested without being able to free a real
   session except by a recorded force.
4. **A force overrides judgement, never safety** — settling what the
   archival design's "a force that overrides" (§5.3) never specified.

## What review changed after the plan was written

Four fixes landed after the tasks that introduced the code they fix, which is
why the brief's own ten-row mutation table undercounted by three rows and
misdescribed two others:

- **The unarchived-file refusal** (`855d549`) — `free_session` now checks
  every scratch file against `archive.verify.stored_sizes` after the NAS
  proof passes, and refuses if a file was added or changed since archiving.
  The proof alone only ever compared the NAS copy against its own recorded
  values, never scratch against the NAS, so a file dropped onto scratch
  after archiving would have been deleted having never been archived at all.
  Same commit also moved `refuse_leftovers` ahead of the CLI's `is_dir()`
  guard, so an interrupted removal (mutation 13) is named on the next
  attempt instead of being reported as an ordinary "already reclaimed?".
- **The exact path match** (`7ca65f3`) — `session_for_path` filtered only on
  MySQL's own default collation (`utf8mb4_0900_ai_ci`), case- and
  accent-insensitive, so a differently spelled `--session` passed straight
  through and restored to the caller's spelling rather than the recorded
  one. Now filtered exactly in Python after the query (mutation 12).
- **The staging-mkdir fix** (`2d47d54`) — round 1's own fix for a vanished
  scratch root put `target.mkdir()` beside `staging.mkdir()`, both before the
  `try/finally` whose `finally` removes staging. A fault creating `target`
  then propagated before the `try` was entered, leaving an empty staging
  directory `refuse_leftovers` would block every later rehydrate of that
  session on, by hand, forever. `target.mkdir()` moved inside the `try`, as
  its first statement (mutation 5); `staging.mkdir()` stays outside, since a
  directory this call did not create must not be the one it removes.
- **The full-suite pollution fix, via a test-file `landed` override** — every
  session `tests/cli/test_reclaim_and_rehydrate.py` successfully reclaims
  leaves its `ingest.Ingestion` row behind with its files deleted;
  `tests/schema`'s `daemon.run_once()` assertions sweep the whole shared
  session-scoped database and re-attempt those sessions' never-populated
  stages, erroring because the files are gone. Fixed with no change to
  production code or to `tests/cli/conftest.py`: a module-local `landed`
  fixture in `test_reclaim_and_rehydrate.py` alone, shadowing the shared one,
  that records every subject it lands and deletes each one's
  `pipeline.Session` row at teardown (cascading to `Ingestion`, the archive
  tables, and anything computed). Confirmed by full-suite counts before and
  after: 1434 passed / 4 failed (`tests/schema/test_daemon.py` x3,
  `tests/schema/test_eye_schema.py` x1) before, 1438 passed / 0 failed after
  — 4 more passes, 4 fewer failures, exactly the tests the teardown was meant
  to stop tripping.

## What the whole-branch review changed

The review ran over the whole branch (`87e7318..9c26398`) and verified each
finding against the code; the controller's rulings are in the ledger's
"Final" lines.

- **Critical: a seventh condition, `timing_resolved`, of the safety kind**
  (`archive/reclaim.py::reclaim_conditions`, directly after
  `every_file_verified`). It passes exactly when a `TimingProvenance` row
  exists *(corrected after merge: and a `SystemTimebase` row exists for every
  `core.AcquisitionSystem` — see the correction at the top)*. Without it, a forced reclaim before the timebase stages had run
  let them compute on the absent directory: `timebase/extract.py::
  find_recordings` returns `[]` for a missing folder by design ("device
  absence never blocks"), so `SystemTimebase` would record
  `fit_status='no_recording'` and `TimingProvenance` tier D — permanent,
  silent, surviving rehydration. The preview already showed it: `[OVERRIDDEN
  by force] not_tier_d -- no tier resolved`. Safety, not judgement, because
  freeing then corrupts data, which the spec's §2 definition of judgement
  excludes; `not_tier_d` stays judgement and unchanged. Cost: a session whose
  timing never resolves can never be freed without a code change.
- **Important: reclaim checks scratch content, not only sizes**
  (`archive/scratch.py::free_session`). After the size check, still before
  anything changes, it refuses unless the DONE markers on scratch list
  exactly the digests recorded in `ArchiveVerification`, and unless every
  scratch file no rig digest names (manifest, DONE markers, operator files)
  hashes the same as its rebuild from the artifact. A same-size re-sent file
  with an updated DONE marker was being freed, and rehydration would have
  restored the old bytes. Every such refusal says to re-archive with `wlpp
  archive` first. **Residual, stated:** a rig-checksummed file edited in
  place on scratch *without* its DONE marker being updated is not detected —
  the archive holds the rig's version, which is what rehydration restores,
  and seeing the edit would mean hashing every scratch file.
- **Minors taken:** the spec amendment's "exact string comparison" now says
  that rehydrate compares strings and reclaim compares `Path` objects; the
  peak-memory docstrings say "a few copies of one stored chunk"; this
  handoff's branch counts are recomputed.

| # | mutation | caught by | result |
|---|---|---|---|
| 14 | `reclaim_conditions`: `timing_resolved` gains `overridable=True` | `tests/archive/test_reclaim.py::test_a_force_does_not_free_a_session_whose_timing_has_not_run`, `::test_pins_condition_kinds_to_production`, `tests/cli/test_reclaim_and_rehydrate.py::test_a_force_does_not_free_a_session_whose_timing_has_not_run` | CAUGHT (all three) |
| 15 | `reclaim_conditions`: `timing_resolved` passes (`len(tier_rows) == 1` → `True`) | the same two `..._whose_timing_has_not_run` tests, `test_no_timing_provenance_row_reports_no_tier_resolved` | CAUGHT (all three) |
| 16 | `free_session`: `if listed != rig_digests:` → `if False:` | `test_reclaim_refuses_a_same_size_resent_file` | CAUGHT, on the refusal's wording: the unchecksummed-file hash check still refuses the session, naming the edited DONE marker (`bcam/DONE`) |
| 17 | `free_session`: `if changed:` → `if False:` | `test_reclaim_refuses_a_same_size_edit_to_an_unchecksummed_file` | CAUGHT (reclaim returned 0) |
| 18 | `free_session`: rows 16 and 17 together | `test_reclaim_refuses_a_same_size_resent_file` | CAUGHT (reclaim returned 0 — the defect as found) |

## Open decision for the requester

Corrected by the whole-branch review: this section said a session forced out
before all its stages had populated makes `daemon.run_once()` *error*. For
the timebase stages that was false — they would have written false rows,
silently — and `timing_resolved` now means no session is freed before they
ran on its real files. What a freed session meets now:

- **The stages that read raw files afterwards** — eye calibration and
  quality, validity, detection — open the ohDPI files named by the
  session's `core.Segment` rows (calibration decodes the sync box log first)
  and raise on a missing file, so a freed session gives them job errors, not
  rows. An errored job-table key is **not** retried, even after
  rehydration, until the job error is cleared by hand
  (`daemon.py::reap_stale_jobs`).
- **The event stage** (`daemon.py::_populate_event_stage`) keeps no job
  table: for a session it has not yet built, it errors on every pass while
  the session is freed and recovers by itself once it is rehydrated.
- **`core.Segment`**, which re-scans a system with no aligned file on every
  pass, finds nothing on a freed session and writes nothing.

What stays open: a stage registered in future that treats a missing
directory as absence, as the timebase stages do, would write false rows for
a freed session (so would a `SystemTimebase` key whose job error is cleared
by hand while its session is freed); and — the review's D5 — a later session
landing at a freed session's recorded path would be read in its place by
every stage that reads `Ingestion.session_dir`, and two sessions recorded at
one path make `wlpp rehydrate` refuse as ambiguous. **Whether the daemon
should skip currently freed sessions is unresolved and not changed on this
branch.**

*Decided and built 2026-09-26, after merge: the daemon skips a freed session, every stage, until it is rehydrated (`archive/scratch.py::currently_freed`, re-read before every stage in `daemon.run_once`, and the archive stage too). Work the daemon never attempted while the session was freed is done on the first pass after rehydration. A key that had already FAILED before the freeing stays parked, as any failed key does, until its job error is cleared by hand -- with one exception that is a DataJoint side effect, not a design: if a pass ran while the session was freed and that job row was more than an hour old (`dj.config` `stale_timeout`), `populate`'s stale-job cleanup, now restricted, deleted it and its diagnostics, and the key is retried after rehydration. Apart from that, everything
this section lists as what a freed session meets no longer happens to it
(`tests/schema/test_daemon_skips_freed_sessions.py`, which also pins that every
stage in `daemon._computed_tables()` keys its work by session, so the skip
cannot exclude a stage whole). One item stays: two sessions recorded at one
path still make `wlpp rehydrate` refuse as ambiguous.*

## Parked follow-ups

The review's Minors 1–6, none of which loses data. 1 and 2 first.

1. The daily report's leftover sweep (`cli/report.py`) names only
   `.archiving` directories, not `.reclaiming` or `.rehydrating` ones.
2. A failure after a mutation has begun (the rename or removal in
   `free_session`, a write in `rehydrate_session`) prints a traceback, not a
   sentence or `MISMATCH` lines.
3. `rehydrate_session`'s `shutil.rmtree(staging, ignore_errors=True)` can
   fail silently while the CLI prints "nothing was left on scratch".
4. An ambiguous rehydrate (two sessions recorded at one path) refuses with
   no way forward.
5. No `fsync` before the rehydrate commit.
6. `wlpp hold` on a freed session prints a traceback.

## Next items — the two findings of spec §12

1. **Archiving a real session will run out of memory.** `store.write_store`
   reads each bulk stream whole with `np.fromfile` and each verbatim file
   whole with `read_bytes`; a two-hour Neuropixels 1.0 AP file is ~166 GB.
   Rehydration streams, but nothing can be rehydrated that could not first be
   archived, so a streaming archive writer is the natural next item, due
   before January.
   *Resolved 2026-09-26: `store.write_store` now streams (`store.py::_write_stream` memory-maps each bulk stream and writes one stored chunk of rows at a time; `_write_verbatim` reads and writes every other file in 16 MiB blocks), so its peak memory is a few chunks, never a whole file.*
2. **`stim.dat` is not a rounding error.** The archival design treats
   everything but the bulk streams as "a rounding error against ~100 GB"
   and stores it verbatim, but Intan's own RHS format documentation
   describes `stim.dat` as one 16-bit word per channel per sample — the same
   size as `amplifier.dat`. On an RHS session this is bulk data stored as
   bytes, met by the in-memory writer above at full size. It deserves its
   own amendment to the archival design, not a line here.

## Read these, in this order

1. `docs/superpowers/specs/2026-09-26-rehydration-design.md` — §0 (the four
   rulings), §2 (the predicate), §4 (deleting), §5 (rehydrating), §12 (the
   two findings above).
2. `.superpowers/sdd/2026-09-26-rehydration/progress.md` and the per-task
   reports — what each task built and what its own review found.
3. `docs/handoffs/2026-08-30-archival-and-compression-is-built-rehydration-is-next.md`
   — what this branch unlocked, and the defect that made a whole-branch
   review worth running here too.
