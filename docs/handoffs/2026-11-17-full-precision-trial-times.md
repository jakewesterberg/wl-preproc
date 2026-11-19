# Full-precision trial and block times

element-event stores a trial's and a block's start and stop as MySQL `float`, 32 bits: 0.49 ms a step at 2 hours,
3.9 ms from 9.1 hours to a 12-hour session's end, each value up to half a step from the one measured. The requester
chose on 2026-11-16 to keep the measured times in a table of our own, before part 3b and before real sessions are
written, and answered its pitfalls on 2026-11-17.
- **Spec:** `docs/superpowers/specs/2026-11-17-full-precision-trial-times-design.md`, approved 2026-11-17.
- **Plan:** `docs/superpowers/plans/2026-11-17-full-precision-trial-times.md`, three tasks, executed native.
- **Branch:** `feat/full-precision-times`, from `spec/full-precision-times` (`9d22ba0`, the plan), itself from
  `main` at `bee887d`.

## What was built

- **`core.TrialTime` and `core.BlockTime`** (`a84d5bb`; spec items 1 and 3): the measured start and stop as doubles,
  foreign-keyed to element-event's rows and deleted with them. `core.insert_trials`/`insert_blocks` write both
  copies from one row; `core.measured_trials`/`measured_blocks` read them and raise `MissingTimeRows`, naming each
  session and its count, where a trial or block has no time row, rather than joining and dropping it.
- **Every reader reads them, and run membership is exact** (`6457319`; items 2 and 4): the NWB file's blocks, trials
  and outside-runs note, the listing, `TrialCoverage` (one query a trial, as before) and the main sequence's blocks.
  `events/runs.py::starts_inside` compares doubles, in the same commit, so the listing and the file still agree; event
  times keep `event_bounds`.
- **A guard and the descriptions** (`48febd1`; items 5 and 7): nothing but `schema/core.py` names element-event's four
  time columns (whole names; `events.py`'s `_trial_stop_time` helpers are not columns); the NWB file's starts say
  they are stored as measured and accurate to the session's clock fit and the 1 ms code-word slot.
- **The review's two fixes** (`6cfa5df`): a stop says it is its `TRIAL_END`/`BLOCK_END`, or when that was lost the
  tightest bound the recording gives; `MissingTimeRows` names the remedy that works.

Item 6 (the hand-kept lists) needed nothing: the delete preview leaves out tables anchored by the event stage's rows,
as it leaves out `core.Run`, and the guardrail sweeps find the new tables themselves.

## Rulings

- `tests/nwb/test_helpers.py`'s containment test modelled float32 storage too; corrected to exact membership at
  12 hours (the spec's rule: correct, never loosen).
- The guard matches whole column names, so a column named inside a longer identifier would pass it.
- One session's `MissingTimeRows` stops the whole listing stage rather than that session alone: only a write outside
  the daemon's transaction can leave a time row missing, and serv is purged before deploy. If wrong, `GET /sessions`
  fails for every session until the one is repaired by hand.

## The review

A fresh reviewer (Opus) read `bee887d..48febd1`, the plan and the spec: no Critical, two Important, four Minor; the
five Review Focus cases each tested through MySQL. **Fixed** (`6cfa5df`): Important 1, an inferred stop described as
measured, the spec's anti-goal (`test_a_stop_says_when_it_is_the_tightest_bound_and_not_a_measured_end`); Important 2,
the error's remedy ("a re-run of the event stage") does nothing, since the daemon never re-runs a session's event
stage: it now says to call `schema.events.populate_session(key, session_dir)` and clear `TrialCoverage`'s errored
jobs. **Deferred**, for the older minors:
1. `tests/schema/test_nwb_build.py:153` takes its expected trial starts from the float32 copy; read
   `core.measured_trials`.
2. `tests/schema/test_main_sequence_populate.py:493`'s docstring still says `_groups` reads the FLOAT columns.
3. The guard misses a read that names no column (a whole-row fetch of `trial.Trial`/`Block`); a second check could
   flag unprojected fetches.
4. The NWB gather reads the measured times twice (the tables and the outside-runs note), a few ms at 14,400 trials.

## The suite

At `6cfa5df`, on both interpreters: 3.11 **3013 passed, 76 skipped, 1 deselected, 1 xfailed**; 3.13 **3013 passed,
77 skipped, 1 xfailed**. The skips are the reference-gated eye tests, run apart with every reference set (the
NSLR and BMD code, the Andersson data set and the OpenIris recording): **265 passed, 1 failed** on each
interpreter. The failure, `tests/schema/test_detect_populate.py::test_the_run_count_measured_against_the_reference_recording`,
is older than this branch and fails the same way on `main` at `bee887d`; no merge since the eye piece ran it,
each leaving the OpenIris recording unset as CI does.

## Found on the way: two-eye rows whose label contradicts their size

Bisected to `fe0e2f3` (the eye piece's final review, I2, merged in `7ec509e` on 2026-11-07). Two things fail.
- **The test's helper is stale.** `_event_amplitudes` measures a run with plain `measure`, and the stage has
  measured through `measure_event_run` since the eye piece, out to the measured samples beyond a carried loss.
  Measured that way, the left and right traces contradict on 0 rows (22 and 105 before).
- **A real defect then shows on the two-eye trace:** 31 of 4,212 conjunction event rows. `_conjunction_label`
  labels a run by `classify` over the bare intersection on the left eye's gaze, while `_run_row` stores the
  size `measure_event_run` gives on the eye `_measured_on` picks, widened back across a loss before the
  take-off. On 14 rows the left eye is the one measured, so the row stores, e.g., `microsaccade` beside
  12.8 degrees (0.19 over the bare intersection); on the other 17 the right eye's size is stored under a label
  from the left eye's gaze. The main sequence selects rows by label.

The requester chose on 2026-11-19 to merge this branch with the test failing, and to fix the defect next. The
corrected helper and the 31 rows are in `~/.cache/wl-preproc-scratch/2026-11-19-conjunction-label/`. From now on
a merge's reference-gated run includes the OpenIris recording.

## Deploy and told to

- **serv is purged before this deploys** (the requester: "serv can be purged, we are still in dev phase"); no
  migration.
- **wl.works**, with the scaling piece's BMD note it is still owed: block and trial times in the listing and the NWB
  file are the measured doubles, moving by up to half a float32 step (1.95 ms late in a long session); a stop whose
  end marker was lost is the tightest bound, as before.
- **wl-xcon:** nothing.
- **From wl.works, 2026-11-18:** SpikeGLX 20260115 and later name the part number in `~imroTbl`'s header
  (`(NP1032,384)`, where earlier versions wrote the type, `(1030,384)`), and probeinterface 0.3.2 parses it with
  `map(int, ...)`. Checked here: our census catches the `ValueError`, so such a probe records a problem and no
  electrode sites rather than failing its segment. The requester placed the fix after the two-eye label fix.
