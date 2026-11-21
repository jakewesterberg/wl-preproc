# The Eye Tracker Placed by Its Analog Copy on the NI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Under the NI's clock, an eye-tracker file is placed by matching its own columns to their analog copy on
the NI's analog inputs. That covers any NI session, with a partial sync box or none, over the whole recording. The
copy's lag is measured where barcodes also place the file.

**Architecture:**
- **The matcher** (`timebase/analog_copy.py`) works on plain arrays, as `timebase/strobes.py` does. It turns
  windows of the eye file's traces, lined up against an NI channel, into `(ni_s, device_s)` pairs.
- **The reading layer** (`place_eye_by_copy`) streams the NI file and the eye file into that matcher, as
  `timebase/intan.py::place_intan` does for strobes.
- **`SystemTimebase`** fits the pairs with `fit.py::fit_pairs_across`, after removing the lag. `Segment` places the
  file with `fit_offset_to_pairs`, exactly as Intan is placed.
- **A part table** records which channel matched, the lag used and where it came from.
- **An eye placed this way** is exempt from the covered span's cut (amendment 58).

**Tech Stack:** Python 3.11 and 3.13, numpy, DataJoint 2.3, pynwb; pytest with the MySQL testcontainer.

**Spec:** `docs/superpowers/specs/2026-10-18-training-records-and-backup-clock-design.md`, amendments 77-84
(`spec/eye-analog-copy`, `54dd173`). These amendments bind. Also read:
- amendments 51 and 54-58, and 66-76 (3b-3);
- amendment 28;
- `docs/superpowers/specs/2026-08-16-phase-1c4-timebase-design.md` §2.1, which amendment 84 supersedes for this use
  alone.

## Global Constraints

- **Scope** (77): under `SessionClock.source == 'nidq'` only, for `ohdpi` only. Under the sync box nothing
  changes. Intan and the cameras are out of scope.
- **Every outcome,** for the eye system under the NI (77, 78):

  | Copy | Barcodes place it over the span | Result |
  |---|---|---|
  | placed | yes | the whole file by the copy; the lag measured here (79) |
  | placed | no | the whole file by the copy; the lag carried (79) |
  | absent | either | 3b-3 unchanged: barcodes over the span, or amendment 57 |
  | failed | yes | 3b-3's placement over the span; the failure named in the file and the daily report |
  | failed | no | rejected `no_analog_match`, which fails the session |

  - **absent:** no channel gives `MIN_PAIRS` distinct windows.
  - **failed:** some channel does, but the fit is refused.
  - **More than one eye file:** the copy is not used, and 3b-3's rules apply.
  - **`no_shared_barcode`** (76) still fails, whatever the copy does.
- **The match** (78, 78a):
  - Every non-multiplexed NI analog channel (the census's XA count, in saved order after MN and MA) is tried.
  - The candidate eye traces, for `Left` and `Right`, are: `P1-P4 X` (`CR1X - CR4X`), `P1-P4 Y`, `P1 X` (`CR1X`),
    `P1 Y` and `PupilWidth`.
  - The eye's own time is `(frame_number - first_frame_number) / fs_hz`, with `fs_hz` from `read_ohdpi`.
  - The NI's time is its samples at `niSampRate`, from the first NI file, `clock.nidq_files(session_dir)[0]`.
- **The fit** (78): `fit_pairs_across`.
  - Pairs further than `RESIDUAL_BOUND_US` from the line are dropped and the line refitted, up to
    `MAX_DROPPED_FRACTION` of all pairs; beyond that, refused.
  - Dropped stretches are named.
- **The lag** (79):
  - `lag_s = mean(ni_s - barcode_session_s(device_s))` over the copy's pairs inside the span. Pairs are corrected as
    `(ni_s - lag_s, device_s)`.
  - It must satisfy `0 <= lag_s <= LAG_CEILING_S`, and the corrected copy must agree with the barcodes within one
    frame period (`1 / fs_hz`) over the span. Otherwise the session is tier D.
  - **Carried:** the latest measured lag on the same rig (the manifest's `rig`) at or before this session's
    datetime, from a session whose copy placed it and whose tier is not D. With none, `lag_s = 0` and
    `lag_measured = false`.
  - A later measurement never moves a session already placed (28).
- **Names:**
  - `time_source` gains `'analog'`;
  - the reason constant is `segments.NO_ANALOG_MATCH = "no_analog_match"`;
  - outcomes are `'placed'`, `'failed'` and `'absent'`.
- **Constants** (78's last bullet), in `timebase/analog_copy.py`, each with a comment saying it was set on synthetic
  data and is revisited on real traces (`docs/hardware-checks.md` 17):

  | Constant | Value |
  |---|---|
  | `DECIMATED_RATE_HZ` | 1000.0 |
  | `WINDOW_S` | 10.0 |
  | `STEP_S` | 10.0 |
  | `SEARCH_PREFIX_S` | 600.0 |
  | `MIN_CORRELATION` | 0.9 |
  | `MIN_PEAK_MARGIN` | 0.1 |
  | `RUNNER_UP_EXCLUSION_S` | 0.05 |
  | `MIN_PAIRS` | 20 |
  | `RESIDUAL_BOUND_US` | 500.0 |
  | `MAX_DROPPED_FRACTION` | 0.1 |
  | `LAG_CEILING_S` | 0.03 |
- **Memory:** a 12 h NI file is never held whole. The channel search reads `SEARCH_PREFIX_S` of every XA channel.
  The placement reads the chosen channel in full, decimated to `DECIMATED_RATE_HZ` float32, streamed through
  `channels.column_chunks`.
- **Unchanged:**
  - every sync-box session, and every NI session without a copy, gives the same rows, tiers and NWB file as on
    `main`;
  - `tests/nwb/test_writers.py::test_a_sync_box_nwb_file_is_unchanged` passes unedited;
  - every existing synthetic recipe's output is byte-identical, and new recipe fields default to off.
- **Floats:** compare to a tolerance, never as text.
- **Database:** development databases are rebuilt, never migrated. The enum and the part table need a rebuild.
- **Tests:**
  - each is seen to fail first;
  - targeted runs during tasks;
  - the full suite on 3.11 (`.venv/bin/python`) and 3.13 (`~/.cache/wl-preproc-venv313/bin/python`) once, at the
    end, with every reference set;
  - never commit while a test run is going on this checkout.

## Review Focus

1. **The eye file starts before the NI does** (the rig may run first, amendment 44). The anchor search runs over the
   whole channel, and pairs exist only where the NI recorded. Task 2's
   `test_an_eye_trace_that_starts_before_the_channel_is_anchored`.
2. **Dropped eye frames.** Device time comes from frame numbers, not rows, or every later pair is early. Task 3's
   `test_a_dropped_frame_does_not_shift_later_pairs`.
3. **A clipped copy.** The DAC beyond the NI's ±5 V range flattens windows: they give no pair, and they never give
   a confident wrong one. Task 2's `test_clipped_windows_give_no_pair`.
4. **One eye untracked** (its columns all zero). A zero-variance candidate is skipped, with no division by zero and
   no NaN pair. Task 2's `test_a_constant_candidate_is_skipped`.
5. **A 12 h file's memory.** The search never reads past its prefix. Task 3's
   `test_the_channel_search_reads_only_its_prefix`.

---

### Task 1: The synthetic copy on the NI

**Files:**
- Modify: `wl_preproc/synth/recipe.py` (`SessionRecipe`), `wl_preproc/synth/spikeglx.py` (`_nidq_meta_text`,
  `write_nidq`), `wl_preproc/synth/ohdpi.py` (its module docstring's "Why a digital line" paragraph gains a
  sentence naming amendment 84)
- Test: `tests/synth/test_eye_copy.py`

**Interfaces:**
- Produces:
  - `recipe.EyeCopy` (frozen pydantic model) with these fields:

    | Field | Type | Default |
    |---|---|---|
    | `channel` | `int` | — |
    | `n_channels` | `int` | 16 |
    | `lag_s` | `float` | 0.005 |
    | `gain_v` | `float` | 0.004 |
    | `offset_v` | `float` | 0.3 |
    | `noise_v` | `float` | 0.002 |
    | `smoothing_samples` | `int` | 30 |
    | `jitter` | `bool` | `True` |
    | `stall` | `tuple[float, float, float] \| None` | `None` |
    | `clip_v` | `float` | 5.0 |
    | `source` | `Literal["eye", "noise", "flat"]` | `"eye"` |

    The `stall` tuple is `(start_s, stop_s, extra_lag_s)`.
  - `SessionRecipe.eye_copy: EyeCopy | None = None`.
  - With `eye_copy` set, the NI file has `n_channels` XA channels before its two digital words:
    - `snsMnMaXaDw=0,0,{n},2`, `nSavedChans`, `fileSizeBytes`, `niXAChans1`, `~snsChanMap` updated;
    - `niAiRangeMax=5` kept.
  - Channel `channel` carries the copy. The others carry independent noise of `noise_v`.

- [ ] **Step 1: Write the failing tests**
  - `test_a_recipe_without_a_copy_writes_the_same_bytes`: the NI bin and meta for an existing recipe hash as on
    `main`.
  - `test_the_copy_lands_on_its_channel`: `read_nidq_meta` gives `n_analog_channels == 16`. The digital words
    decode the same barcodes as without a copy. Channel `channel`'s correlation with the session's true left
    `P1-P4 X` is above 0.95, after shifting it by `lag_s`. Every other channel's is below 0.2.
  - `test_the_copy_is_not_the_column_the_matcher_reads`: the copy equals no linear map of any one candidate column
    (a least-squares fit of the channel on each candidate leaves residual RMS above `noise_v`). This is what keeps
    the matcher's tests from passing on a tautology (78).
  - `test_a_stall_delays_its_stretch`: with `stall=(20, 25, 0.04)`, the copy inside 20-25 s trails by
    `lag_s + 0.04`.
- [ ] **Step 2:** Run `tests/synth/test_eye_copy.py`. Expected: FAIL (no `EyeCopy`).
- [ ] **Step 3: Implement.** The copy of frame k is held from `frame_time_k + lag_s + jitter_k` to the next
  frame's.
  - **The jitter:** with `jitter`, the processing delay is drawn per frame, around the paper's numbers:
    - a median of 1.1 ms;
    - 2% of frames between 10 and 50 ms;
    - seeded from the recipe's seed.
  - **The value:**
    - 0.7 × `P1-P4 X` + 0.3 × `P1-P4 Y`, through `tanh` at 3 times the trace's standard deviation;
    - then times `gain_v`, plus `offset_v`, a moving average over `smoothing_samples`, noise, and clipping at
      ±`clip_v`.
  - `source="noise"` writes noise alone; `"flat"` writes `offset_v`.
- [ ] **Step 4:** Run `tests/synth/` and `tests/timebase/test_extract.py`. Expected: PASS.
- [ ] **Step 5:** Commit `feat(synth): the eye tracker's analog copy on the NI's analog inputs`.

### Task 2: The matcher

**Files:**
- Create: `wl_preproc/timebase/analog_copy.py`
- Test: `tests/timebase/test_analog_copy.py`

**Interfaces:**
- Produces (pure functions on arrays, no I/O):
  - **Errors:** `class NoCopy(Exception)` means absent. `class CopyRefused(Exception)` means failed; its message
    names why: too few pairs, too many dropped, or the residual.
  - **`CopyMatch`** (frozen dataclass):

    | Field | Type |
    |---|---|
    | `pairs` | `tuple[tuple[float, float], ...]`, each `(ni_s, device_s)` |
    | `channel` | `int` |
    | `signal` | `str`, e.g. `"Left P1-P4 X"` |
    | `n_windows` | `int` |
    | `n_dropped` | `int` |
    | `dropped_stretches` | `tuple[tuple[float, float], ...]`, in device seconds |

  - **`find_channel(eye_time_s: np.ndarray, candidates: dict[str, np.ndarray], channels: list[np.ndarray],
    fs_hz: float) -> tuple[int, str]`:** the best `(channel, signal)` over the prefix given, by the count of
    distinct windows; raises `NoCopy`.
  - **`match_copy(eye_time_s: np.ndarray, trace: np.ndarray, channel: np.ndarray, fs_hz: float) -> CopyMatch`:**
    - The anchor comes from the first window that is distinct against the whole channel, by FFT cross-correlation.
    - Each later window is searched within ±`RUNNER_UP_EXCLUSION_S` of the line so far.
    - The peak is refined below a sample by a parabola.
    - A window gives a pair at its centre when its peak correlation is ≥ `MIN_CORRELATION` and beats the best
      shift outside ±`RUNNER_UP_EXCLUSION_S` by ≥ `MIN_PEAK_MARGIN`.
    - Then come the drop and refit of Global Constraints, which raise `CopyRefused`.
    - The returned `channel` field is the caller's to fill.
- Consumes: `fit.fit_pairs_across`, for the line and its residuals.

- [ ] **Step 1: Write the failing tests** on arrays built in the test, with a known lag of 5 ms, a scale of 0.004,
  noise, and a 100 ppm rate difference:
  - `test_a_copy_is_matched_to_within_a_tenth_of_a_frame`: every pair's `ni_s - device_s` is within 0.2 ms of the
    truth, and `n_dropped == 0`.
  - `test_the_channel_and_signal_are_found_among_noise`: 16 channels, the copy on channel 11 → `(11, "Left P1-P4
    X")`.
  - `test_noise_alone_is_no_copy`: `NoCopy`.
  - `test_a_flat_channel_is_no_copy`: `NoCopy`.
  - `test_a_stall_is_dropped_and_named`: a 5 s stretch 40 ms late gives `n_dropped > 0`, with a dropped stretch
    covering it, and the line unchanged within 0.2 ms.
  - `test_too_many_late_windows_refuse`: a stall over 20% of the trace gives `CopyRefused`, naming the dropped
    fraction.
  - `test_an_eye_trace_that_starts_before_the_channel_is_anchored` (Review Focus 1).
  - `test_clipped_windows_give_no_pair` (Review Focus 3): windows clipped at the rail give no pair, and those that
    do lie on the line.
  - `test_a_constant_candidate_is_skipped` (Review Focus 4): one candidate all zero, with no warning and no NaN.
  - `test_a_periodic_trace_refuses_rather_than_matching_a_wrong_period`: a sinusoid trace gives `NoCopy` or
    `CopyRefused`, never a `CopyMatch`.
- [ ] **Step 2:** Run it. Expected: FAIL (no module).
- [ ] **Step 3:** Implement, with the constants of Global Constraints, each commented.
- [ ] **Step 4:** Run `tests/timebase/test_analog_copy.py`. Expected: PASS.
- [ ] **Step 5:** Commit `feat(timebase): match an eye trace to its analog copy`.

### Task 3: Reading the files

**Files:**
- Modify: `wl_preproc/timebase/analog_copy.py`, `wl_preproc/eye/ohdpi.py` (export the candidate column names)
- Test: `tests/timebase/test_analog_copy_files.py`

**Interfaces:**
- Produces:
  - `CopyPlacement` (frozen dataclass): `match: CopyMatch`, plus `first_sample: int`, the native eye index of the
    first pair.
  - `place_eye_by_copy(session_dir: Path, eye_path: Path) -> CopyPlacement`. It raises `NoCopy` or `CopyRefused`,
    logging a WARNING that names the file, as `place_intan` does.
  - `read_xa_channels(ni_path: Path, stop_s: float | None) -> tuple[float, list[np.ndarray]]`: decimated to
    `DECIMATED_RATE_HZ` float32 by block means over `column_chunks`, reading no further than `stop_s`.
  - `eye_candidates(eye_path: Path) -> tuple[np.ndarray, dict[str, np.ndarray]]`: device seconds from frame
    numbers, and the ten candidate traces.
- Consumes: Task 1's synthetic files, Task 2's functions, `clock.nidq_files`, `_nidq_meta.read_nidq_meta`,
  `eye.ohdpi.read_columns`.

- [ ] **Step 1: Write the failing tests** on a synthetic NI session with a copy and no box:
  - `test_a_session_with_a_copy_is_placed`: the pairs fit within 0.5 ms of the true frame times plus `lag_s`.
  - `test_a_dropped_frame_does_not_shift_later_pairs` (Review Focus 2): the recipe's frame-gap fault.
  - `test_the_channel_search_reads_only_its_prefix` (Review Focus 5): `read_xa_channels` is wrapped to record its
    `stop_s`. The search passes `SEARCH_PREFIX_S` beyond the eye file's start, and only the chosen channel is read
    in full.
  - `test_a_session_without_xa_channels_is_no_copy`: `NoCopy`.
- [ ] **Step 2:** Run it. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run the two analog-copy test files. Expected: PASS.
- [ ] **Step 5:** Commit `feat(timebase): place an eye file by its copy on the NI`.

### Task 4: The clock fit, the lag and the placement

**Files:**
- Modify:
  - `wl_preproc/schema/timebase.py`: `_TIME_SOURCE_ENUM` gains `'analog'`; `SystemTimebase` gains part `Copy`
    and the ohdpi path in `make`; `TimingProvenance.make` fails on a bad copy;
  - `wl_preproc/schema/core.py`: `Segment.make` places by the copy's pairs;
  - `wl_preproc/timebase/segments.py`: `NO_ANALOG_MATCH` joins `REJECTION_REASONS`.
- Test: `tests/schema/test_eye_by_copy.py`, with a module `landed` fixture, in `test_partial_sync_box.py`'s shape.

**Interfaces:**
- Produces: `SystemTimebase.Copy` (one row per ohdpi system under the NI where the copy was looked for):

  | Column | Type | Meaning |
  |---|---|---|
  | `outcome` | `enum('placed','failed','absent')` | |
  | `refusal` | `varchar(255)` | `''` unless failed |
  | `ni_channel` | `smallint`, null | |
  | `signal` | `varchar(32)`, null | |
  | `n_pairs` | `int unsigned`, null | |
  | `n_dropped` | `int unsigned`, null | |
  | `lag_s` | `double`, null | |
  | `lag_measured` | `bool`, null | |
  | `lag_source_datetime` | `datetime`, null | the session it was measured in; this one's own when measured here |
  | `agreement_us` | `double`, null | the largest corrected disagreement over the span, where measured |
  | `rig` | `varchar(64)` | |

  The ohdpi row is `time_source='analog'`, `fit_status='fitted'`, with `fit_pairs_across` over the corrected pairs.
  A copy that fails with no barcodes is `unfittable` with `time_source='analog'`.
- Consumes: Task 3's `place_eye_by_copy`.

- [ ] **Step 1: Write the failing tests.** The `landed` fixture lands these sessions:

  | Session | What it holds |
  |---|---|
  | `copy_only` | NI, eye with a copy, no box |
  | `copy_partial` | NI, a box stopping at 8.7 s, eye with a copy |
  | `copy_carried` | same rig, an hour later, no box |
  | `copy_absent` | NI, a box stopping partway, eye with no copy |
  | `copy_noise` | `source="noise"` |
  | `copy_flat` | `source="flat"` |
  | `copy_stall` | a stall over 25% of the file, and no box |
  | `copy_short_stall` | a stall over 5% of the file, and no box |
  | `copy_disagree` | the copy written from a clock 3 ms/s off |
  | `copy_negative` | `lag_s=-0.004`, with a box |
  | `copy_two_files` | two eye files |

  Tests:
  - `test_a_session_with_only_a_copy_is_tier_n`: ohdpi is `analog` and `fitted`; Copy is `placed`, with
    `lag_measured` false and `lag_s` 0. The segment's start is within 7 ms of the truth: the unmeasured lag of 5 ms plus the 1.1 ms median jitter.
  - `test_a_partial_box_measures_the_lag_and_places_the_whole_file`: `lag_measured` is true; `lag_s` is within
    0.5 ms of the recipe's 5 ms, plus the median jitter. The segment's end lies past the covered span.
  - `test_the_next_session_on_the_rig_carries_the_lag`: `copy_carried`'s `lag_source_datetime` equals
    `copy_partial`'s session datetime, and its start is within 1 ms of the truth.
  - `test_no_copy_leaves_3b_3_unchanged`: for `copy_absent`, Copy is `absent`; ohdpi is `time_source='barcode'`
    and `fitted`; the tier is N; and `covered_start_s` / `covered_end_s` and the eye segment's extent equal those of
    3b-3's `partial` built from the same recipe without `eye_copy`.
  - `test_noise_and_flat_channels_are_no_copy`: Copy is `absent`; with no barcodes the eye file is `no_barcode`
    and the session D (57).
  - `test_a_failed_copy_with_no_barcodes_is_no_analog_match`: `copy_stall` is rejected `no_analog_match`, and the
    session is D.
  - `test_barcodes_and_copy_that_disagree_make_d`: `copy_disagree` is D, with `agreement_us` above 2000.
  - `test_a_negative_lag_makes_d`.
  - `test_two_eye_files_are_not_placed_by_the_copy`: Copy holds no row; the 3b-3 rules apply.
  - `test_a_disagreeing_sessions_lag_is_not_carried`.
- [ ] **Step 2:** Run it. Expected: FAIL.
- [ ] **Step 3:** Implement Global Constraints' outcome table in `SystemTimebase.make`, as `_make_by_copy`, beside
  `_make_by_strobes`.
  - `Segment.make`'s ohdpi branch uses `fit_offset_to_pairs` on the corrected pairs, as Intan's does.
  - `TimingProvenance`'s `failed` gains any Copy row whose measured lag lies outside the bounds or whose
    `agreement_us` exceeds one frame.
- [ ] **Step 4:** Run `tests/schema/test_eye_by_copy.py tests/schema/test_partial_sync_box.py
  tests/schema/test_ni_clock.py`. Expected: PASS.
- [ ] **Step 5:** Commit `feat(timebase): the eye tracker placed by its analog copy under the NI`.

### Task 5: Eye data past the span

**Files:**
- Modify:
  - `wl_preproc/eye/span.py`: gains `eye_span(session_key: dict, session_dir: Path) -> Span | None`, which is None
    when ohdpi's `SystemTimebase.time_source` is `'analog'`, else `clock_for(...).covered_span()`;
  - its callers: `schema/detect.py` (two), `schema/eye.py` (two) and `nwb/gather.py` (one). `schema/coverage.py`
    already keeps extents for a source other than `barcode`, so it is left alone.
- Test: `tests/schema/test_eye_by_copy.py`, extended.

- [ ] **Step 1: Write the failing tests:**
  - `test_a_copy_placed_eye_is_calibrated_past_the_span`: `copy_partial`'s calibration uses a window after 8.7 s.
  - `test_a_copy_placed_eyes_saccades_past_the_span_are_stored`.
  - `test_a_copy_placed_eyes_nwb_samples_run_past_the_span`.
  - `test_a_barcode_placed_eye_is_still_cut`: `copy_absent` is unchanged.
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run `tests/schema/test_eye_by_copy.py tests/schema/test_partial_sync_box.py`. Expected: PASS.
- [ ] **Step 5:** Commit `feat(eye): an eye placed by its copy is used past the covered span`.

### Task 6: What the file and the report say

**Files:**
- Modify:
  - `wl_preproc/nwb/timebase.py`: the NI texts gain `analog`;
  - `wl_preproc/nwb/gather.py`: notes;
  - `wl_preproc/cli/report.py`: the daily report's new section.
- Test: `tests/schema/test_eye_by_copy.py`, extended; `tests/nwb/test_writers.py`, the verbatim pins that change.

- [ ] **Step 1: Write the failing tests:**
  - `test_the_nwb_file_says_the_eye_was_placed_by_its_copy`: `system_clocks.time_source`'s text names `analog`,
    as "by its analog copy on the NI, matched over the whole recording".
  - **A note** names the channel `XA<k>` and the signal, then one of:
    - "lag 0 s, unmeasured, so its time may be off by the eye tracker's output latency, of the order of 4-5 ms";
    - "lag X ms, measured in this session";
    - "lag X ms, measured in session `<datetime>`".
  - `test_dropped_stretches_are_named_in_the_file`: `copy_short_stall` is placed, and its note names the
    dropped stretch.
  - `test_a_failed_copy_is_named_where_barcodes_placed_the_file`: a note and a daily-report line.
  - `test_the_report_names_a_lag_that_jumps`: a lag differing from the rig's previous by more than one frame period
    is listed under "Eye placed by its analog copy".
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement. Ruling R15's form is kept: each `time_source` value is stated for `fitted` and
  `unfittable`, so every row reads true.
- [ ] **Step 4:** Run `tests/schema/test_eye_by_copy.py tests/nwb/ tests/cli/test_report*.py`. Expected: PASS.
- [ ] **Step 5:** Commit `feat(nwb): the file and the report say how the eye was placed`.

### Task 7: Records

- **`docs/hardware-checks.md`:** rows 15-18 and 16a cite the built constants and the `Copy` table.
- **`docs/ops/lab-host-protocol.md`:** under the NI, the eye tracker may be placed by its analog copy; the new
  reason is `no_analog_match`.
- **`docs/pending-wl-works-amendments.md`:** the NI entry gains a line. A rejected segment's `reason` may be
  `no_analog_match`, and the NWB file's `system_clocks.time_source` may be `analog`. Both are plain strings.
- **`docs/handoffs/2026-11-21-eye-by-analog-copy.md`,** in 3b-3's shape, with `FINAL_REVIEW` and `SUITE_RESULTS`
  placeholders.
- Commit `docs: the analog copy's records and the handoff`.

---

## Self-review

- **Coverage.** Amendments 77 and 78 → Tasks 2-4; 78a → Tasks 1 and 3; 79 → Task 4; 80 → Tasks 4 and 6; 81 →
  Tasks 1 and 4; 82 → Task 7; 83 is carried, not built; 84 → Task 1's docstring and Task 6's note text.
- **Types.** `CopyMatch` and `CopyPlacement` are as Task 2 and Task 3 define them. Pairs are `(ni_s, device_s)`
  throughout, corrected to `(ni_s - lag_s, device_s)` only in Task 4. `NO_ANALOG_MATCH` is one constant.
