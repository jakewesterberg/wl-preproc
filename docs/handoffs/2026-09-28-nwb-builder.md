# The NWB builder: one activation's file, for everything before ephys

Branch `spec/nwb-builder`, forked from `main` at `82e79e3`. Spec
`docs/superpowers/specs/2026-09-28-nwb-builder-design.md` (`1805489`, amended
`6540211` and `78ac564`); plan `docs/superpowers/plans/2026-09-28-nwb-builder.md`
(`78ac564`). Piece 1 of Phase 3's NWB export (parent spec §8). The requester
chose it as next on 2026-09-28, and chose inline execution with one final
review on 2026-09-29.

Every line of the plan was proven in a scratch worktree before the plan was
written, and applied here verbatim from it.

---

## 1. What was built

`wl_preproc/nwb/build.py::build(activation_key, nwb_root)` writes one
`request.Activation`'s file to `{nwb_root}/{session_id}/{identifier}.nwb`, with
`identifier` = `{session_id}.montage-{m}.activation-{a}`. It re-opens the
file, checksums every dataset, runs `nwbinspector`, and records the result in
a new table, `nwb.NwbFile`, with one `NwbFile.Dataset` row per dataset. It
runs from the daemon (`wlpp daemon --nwb-root PATH`, opt-in like
`--nas-root`) and from `wlpp nwb build --subject … --session-datetime …
--montage-id … --activation-id … --nwb-root …`. The command will not
overwrite a recorded activation: delete its row to rebuild.

`gather.py` is the one module that reads the database and the raw ohDPI
file. Every writer takes plain data and is tested without either.

**The file.** Every time is session seconds, t = 0 at the sync box's first
decoded barcode. Everything is trimmed to the activation's blocks (spec §5).
- **`/intervals/blocks`**: wl.works' asserted boundaries, the measured ones
  beside them, and per-system coverage. **`/intervals/trials`**: trial id,
  outcome, block and coverage. **`/intervals/task_events`**: every decoded
  event code as a zero-length interval (Neurosift does not display NWB
  2.10's `EventsTable`), with its trial, block and condition.
- **`processing/timebase`**: the timing tier and counts, every system's
  clock fit, every segment's placement, and where the wall-clock time of
  t = 0 came from (`clock_reference`). No PTP value is written (spec §4.4).
- **`processing/behavior`**: the glitch-repaired gaze of each eye
  (`EyeTracking`, degrees, calibrated frame) and OpenIris's raw pupil
  columns (`PupilTracking`), all four series sharing one timestamps
  dataset; `eye_calibration`; per eye, the stretches the mask withheld and
  the stretches glitch repair replaced.
- **`processing/eye_events`**: 18 tables, `{detector}_{trace}` for six
  detectors and three traces, each run with its label and, on saccadic
  rows, its measurements; six `{detector}_source` tables (which eye the
  both-eyes trace used); and `detector_agreement`.

**Also changed on the way.**
- `schema/eye.py::row_session_times`, new, gives every ohDPI row its session
  time. `_session_time_to_row` is corrected to be its exact inverse: it
  placed `end_s` at sample `n_samples - 1`, off by up to one sample (spec
  §4.3).
- The job request carries `subject_details` (species, sex, date of birth),
  and `accept` writes them into element-animal's own record (spec §9).

## 2. What a file needs to be `written`

**wl.works' subject details.** `nwbinspector` rates a subject with neither age
nor date of birth as CRITICAL, and a file with a critical finding is
`invalid`: kept, but never published by piece 2. **Until wl.works sends
`subject_details.date_of_birth`, no real file is publishable.** The request
is the OPEN entry in `docs/pending-wl-works-amendments.md`.

A future `session_start_time` is the other critical check measured on these
files. A real session is never in the future.

**Refused, with a row and a reason and no file:** no `TimingProvenance`,
timing tier D, no blocks in the activation's block set, or more than one
ohDPI segment. A refusal is never retried automatically. **Partial:** an
eye without a calibration is left out, and the file's description says so.

## 3. The clock

The wall-clock time of t = 0 is 2020-01-01 UTC plus the first barcode's
value (wl-sync's barcode epoch), `reference_source = 'barcode'`. Where that
and the manifest's `started_at` differ by more than 60 s, `started_at` is
used and the source says `manifest`.
- **Every synthetic file takes the fallback**: synthetic barcodes count
  from 1,000,000.
- **The epoch is restated in `gather.py`**, because the wl-sync commit this
  repository pins predates `wl_sync/clock.py`.
  `test_the_restated_barcode_epoch_is_wl_syncs` pins the two equal wherever
  a newer wl-sync is installed; here it skips.
- **wl-sync's `clock_trusted` segment flag is not read yet.** It waits on
  moving the wl-sync pin, which is its own change.

## 4. Still not done

- **`archive/reclaim.py::canonical_nwb_present` is still `False`.** It is
  piece 2's, with publication and the canonical lifecycle. Until then a
  real reclamation still needs a recorded force.
- **Ephys** (electrodes, units, LFP, MUA) is piece 3's, after Phase 2b.
- The wl.works half of the subject details (their caller, and row 18b's
  fake wl-preproc).

## 5. Rulings

Made while planning (each also in the plan, and in the spec where it amends
it):
1. `build(activation_key, nwb_root)`, not an output path; the command takes
   named options. One path rule, in one place.
2. Events are a zero-length `TimeIntervals`, `/intervals/task_events`.
3. The barcode epoch is restated, not imported.
4. An event exactly on a block's end is kept: it is that block's
   `BLOCK_END`.
5. A missing date of birth stays CRITICAL. Cost: no file is publishable
   until wl.works sends it.
6. Blocks that touch are one interval, so a blink across wl.works' split is
   one row, not two. Found while writing the plan's Review Focus tests.
7. The trials table has no `condition` column. No table stores a condition
   on a trial; it reaches the file on its `CONDITION` event. Cost: a
   reader joins the two by time.
8. The recovery test in `test_detect_populate.py` counts planted-size
   saccades only: the one-sample calibration fix let REMoDNaV store a
   0.11°, 10 ms noise event on that fixture.
9. The refusal tests restore the saved timing row rather than recompute it;
   the end-to-end fixture splits its one measured block in two at 7.5 s; the
   crossing-run test moves that boundary into the first planted saccade
   for one build; the stage-failure test uses the two one-block
   derivatives, because `accept` returns the activation it already holds
   for a block set.

Made while executing:
- The full suite read the reference material from
  `~/.cache/wl-preproc-references` and ran 3.13 from
  `~/.cache/wl-preproc-venv313`, not the session scratchpad, because a
  macOS-update reboot on 2026-09-29 cleared `/private/tmp`. Same material,
  same pinned commits, same resolved versions.

## 6. Measured

| Task | Before the code | After |
|---|---|---|
| 1 | 3 failed, 38 passed | 149 passed, 1 skipped (with `test_detect_populate.py`) |
| 2 | 21 failed, 4 passed | 129 passed (`tests/responder`, schema export) |
| 3 | 15 failed | 15 passed |
| 4 | 15 failed, 25 passed, 1 skipped, 7 errors | 206 passed, 1 skipped |

**Mutations, all caught by the test the plan names:** T1a–b (the inverse and
the row times), T2a–b (the details written, the species), T3a–g (the
skipped file-creation date, ragged pairs, chunk size, shared timestamps,
events on a block's end, the partial file, touching blocks), T4a–l (events
filter, clock trust, derivative block set, `invalid` status, tier D, two
segments, missing eye, freed skip, the command's refusal to overwrite, the
stage's per-activation catch, no eye recording, runs never cut).

**Full suite**, with the BMD and NSLR references set and
`WLPP_OHDPI_REFERENCE` unset, as CI has it: **1820 passed, 31 skipped, 1 deselected, 1 xfailed** on 3.11 and **1819
passed, 33 skipped, 1 xfailed** on 3.13, 0 failed, on `9ed427e`. `main` at
`0aa4928` gave 1781 and 1780; the branch adds 40 tests, one of which skips.
