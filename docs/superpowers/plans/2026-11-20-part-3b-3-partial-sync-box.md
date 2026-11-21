# Part 3b-3: A Sync Box That Recorded Part of the Session — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Under the NI's clock, a sync box that stopped partway places the eye tracker, the cameras and itself over
the part it covered, through the NI's own barcodes. Its code words cross-check the NI's, so such a session can be
tier N.

**Architecture:**
- The NI recorded the box's barcodes on its own barcode line, so under the NI those barcodes are the session's
  barcode reference.
- Every system carrying barcodes is then fitted by today's barcode path, unchanged.
- The span the reference covers bounds which eye data any stage may use.
- The box's code words, cut to what its log recorded, must all be found, in order, in the NI's stream.

**Tech Stack:** Python 3.11 and 3.13, numpy, DataJoint 2.3, pynwb; pytest with the MySQL testcontainer.

**Spec:** `docs/superpowers/specs/2026-10-18-training-records-and-backup-clock-design.md`, amendments 54-64. Those
amendments bind. Amendment 65 is the next piece, not this one. Read also amendments 40-51 (3b-1), 52-53 (3b-2),
§6.2, §6.4 and §7.

## Global Constraints

- **Reference** (amendment 54). Under the NI's clock, `Clock.barcode_reference()` is the NI's own barcodes:
  - from the first NI file only (`clock.nidq_files(session_dir)[0]`);
  - gap-clear, as `segments.scan_system("spikeglx", …)` decodes them;
  - value → NI seconds on the identity map.

  With none, the reference is empty and nothing is placed by barcode, as in 3b-1.
- **Covered span** (amendment 55): `[first, last]` of that reference, in session seconds.
- **Who is fitted** (amendment 56): the sync box, `ohdpi` and `bcam`, each by today's barcode path, with
  `time_source='barcode'`. SpikeGLX stays on the identity map. Intan stays on strobes, and its barcodes are not
  used under the NI.
- **Outside the span** (amendment 57):
  - a file whose barcodes all lie outside the reference is rejected as `outside_covered_span`, which does not fail
    the session;
  - a system whose every file is that is left out and named, `unfittable` but not failing;
  - a file that decoded no barcode stays `no_barcode` and fails;
  - every other rejection fails.
- **Eye data** (amendment 58): used and published only inside the span. That covers calibration, detection,
  quality, the main sequence, and the NWB file's samples and events. The NWB file names the stretch left out.
- **Cross-check** (amendment 59):
  - only where the box's log holds code words: its session-time segments, within the span, less every window
    `SyncBoxSession.losses()` gives;
  - the box's decode errors do not count;
  - below `AGREEMENT_THRESHOLD` (0.999) the session is D;
  - stored as `event_code_agreement`, with `n_full_code_records` 2.
- **The box's losses** (amendment 60) do not fail the timing check under the NI.
- **The gate** (amendment 61): `syncbox_fitted` also needs `clock.source == "syncbox"`.
- **End codes** (amendment 62): an end code reaches the end only when it is the log's last decoded code, with only
  `BLOCK_END` or `RUN_END` after it. `CHOICE_RULES = "3b-3"`.
- **NWB** (amendment 63): `system_clocks.time_source` reads `barcode` for those systems; the NI texts say so; the
  description gains nothing; the left-out stretch is named in the file's notes.
- **Unchanged under the sync box:** every sync-box session gives the same rows, tiers and NWB file as before.
  `tests/nwb/test_writers.py::test_a_sync_box_nwb_file_is_unchanged` passes unedited.
- **Synthetic sessions:** every existing recipe's output is byte-identical; new recipe fields default to off.
- **Floats:** compare to a tolerance, never as text.
- **Database:** development databases are rebuilt, never migrated.
- **Tests:**
  - each is seen to fail first;
  - targeted tests during tasks;
  - the full suite once, at the merge, on both interpreters, with every reference set;
  - never commit while a test run is going on the checkout.
- **Commit trailer:**
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_016qk6ZmpHJTmi88idVn1B44
  ```

## Decisions this plan makes (the spec is silent)

1. **The span is computed, and also recorded.**
   - `Clock.covered_span()` computes it from the files. It is cached as the clock's other reads are, so each eye
     stage asks it without an ordering between tables.
   - `TimingProvenance` stores it as `covered_start_s` / `covered_end_s` (null outside this case), for the record
     and the NWB file's notes.
   - This is how "stored once, recomputed from the files" (amendment 55) is read.
2. **An empty reference keeps 3b-1's behaviour.**
   - If the NI decoded no barcode, every barcode system under the NI is `unfittable` with `time_source` `none`,
     and its files are `unfitted_system`, as today.
   - This holds even when those files decoded the box's barcodes. Otherwise a dead NI barcode line would make
     every eye and camera file `outside_covered_span` and publish the session, which amendment 57 forbids.
3. **The cross-check is containment, not agreement.**
   - The box's words over its recorded stretches are a part of the NI's whole stream.
   - So the value is the fraction of the box's words found, in order, in the NI's stream:
     `agreement.code_containment(part, whole) -> float`, matched count over `len(part)`, on the same anchored
     matcher as `code_agreement`.
   - `code_agreement` divides by the longer stream, so it would count every word the box never recorded.
   - The box's stretches are cut in its own session time (`SyncBoxSession.codes()`, `.losses()`, `.reference()`).
     So no box-to-NI fit is needed for the cross-check, and a split clock's code-word drift cannot move a word
     across a stretch edge relative to the NI.
   - The span's ends in box time are the box's own times for the reference's first and last values that the box
     also holds.
4. **The synthetic box can stop.** A recipe field `syncbox_stop_s: float | None = None`.
   - From that time the box emits no barcode, and so no device carries one.
   - Its log holds only the records before it, with no trailer, as a Pi that lost power leaves it.
   - The 3b-1 helper `_cut_the_sync_box_log` cuts only the log, leaving every device with barcodes after the cut,
     which cannot happen. The new 3b-3 tests use the field (fix the fixture, not the spec).
5. **3b-1's `partial` session changes tier.** Under 3b-3 its sync box is fitted through the NI's barcodes and
   cross-checked, so it is tier N where it was D. Its assertions in `tests/schema/test_ni_clock.py` are updated to
   the new truth in Task 4, and its fixture moves to `syncbox_stop_s`.

## Review Focus

1. **A dead NI barcode line with a partial sync box.** The reference is empty, and every eye and camera file must
   fail the session (D), never be "outside the span". Test: Task 4, `test_an_empty_reference_places_nothing_by_barcode`.
2. **An eye file straddling the span's end.** It is placed, but no sample after the span reaches calibration,
   detection, quality, the main sequence or the NWB file. Test: Task 6, `test_no_eye_stage_uses_a_sample_past_the_span`,
   and Task 7, `test_the_nwb_eye_series_ends_inside_the_span`.
3. **A box whose log has a loss inside its part.** Its lost stretch is out of the cross-check, so an honest gap
   stays N. A box whose words differ from the NI's is D. Test: Task 5,
   `test_a_box_loss_inside_its_part_does_not_fail_the_cross_check` and `test_a_box_whose_codes_disagree_is_d`.
4. **A sync-box session is untouched.** Same rows, tiers and NWB file. Test: the existing sync-box suites, plus
   Task 4, `test_under_the_sync_box_nothing_changes`.
5. **A camera file wholly after the stop.** It decodes no barcode, so it is `no_barcode` and fails, by amendment
   57 as decided. A camera file whose barcodes lie only before the NI started is `outside_covered_span`, and the
   session stays N. Test: Task 4, `test_a_file_whose_barcodes_precede_the_ni_is_outside_and_does_not_fail`.

---

### Task 1: The synthetic box can stop

**Files:**
- Modify: `wl_preproc/synth/recipe.py`: the field beside `syncbox_restart`, and the `_coherent` checks.
- Modify: `wl_preproc/synth/timeline.py`: `build_timeline`'s barcode loop.
- Modify: `wl_preproc/synth/syncbox.py`: the writer, with records before the stop only and no trailer.
- Test: `tests/synth/test_syncbox_stop.py` (create).

**Interfaces:** Produces `SessionRecipe.syncbox_stop_s: float | None = None`. It must be `0 < stop < duration_s`,
needs `"syncbox"` in `systems`, and goes with neither `syncbox_restart` nor `syncbox_roll`.

- [ ] **Step 1: Write the failing tests:**
  - `test_no_device_carries_a_barcode_after_the_stop`: with `systems=("syncbox", "spikeglx", "ohdpi", "bcam")`
    and stop 9.0, every `truth.barcodes` time is below 9.0.
  - `test_the_log_holds_nothing_after_the_stop_and_no_trailer`: read the log with `wl_sync.log.read_log`. Every
    record's tick is below the stop's, and the file's last line is not a trailer.
  - `test_no_stop_leaves_every_recipe_unchanged`: for each `RECIPES` value, `syncbox_stop_s is None`.
  - `test_a_stop_needs_a_sync_box_and_lies_inside_the_session`: exact messages, as the neighbouring checks write
    them.
- [ ] **Step 2:** `.venv/bin/python -m pytest tests/synth/test_syncbox_stop.py -q`. Expect FAIL: there is no
  field.
- [ ] **Step 3: Implement.** Read how `syncbox_restart`'s down period stops barcodes in `timeline.py` and how
  `_write_segment` takes `until_s` in `syncbox.py`, and reuse both. Check whether the writer appends a trailer and
  how to omit it.
- [ ] **Step 4:** `.venv/bin/python -m pytest tests/synth -q`. Expect everything to pass.
- [ ] **Step 5:** commit `feat(synth): a sync box that stops partway, as a Pi that lost power`.

---

### Task 2: An end code reaches the end only where it ends the log

**Files:**
- Modify: `wl_preproc/timebase/holding.py`: `unreached_end` and `_markers_unheld`'s use there.
- Modify: `wl_preproc/timebase/clock.py`: `CHOICE_RULES`.
- Test: `tests/timebase/test_holding.py`.

- [ ] **Step 1: Write the failing tests**, using the module's own builders of decoded streams:
  - `test_a_session_end_followed_by_trial_codes_does_not_reach_the_end`: a `SESSION_END` read from a lost escape,
    followed by `TRIAL_START`. The test passes when `unreached_end(...)` is not None.
  - `test_a_session_end_followed_by_run_and_block_ends_reaches_it`: `SESSION_END` then `BLOCK_END`, `RUN_END`.
    The test passes when it is None.
  - `test_a_last_run_end_that_is_not_the_last_code_does_not_count`: the last run's `RUN_END` followed by a
    `TRIAL_START`. The last trial line decides.
  - `test_the_rules_are_3b_3`: `clock.CHOICE_RULES == "3b-3"`.
  - Keep every existing holding test passing, including 3b-1's I1 test (a corrupt last-trial word, then `RUN_END`
    and `SESSION_END` last).
- [ ] **Step 2:** `.venv/bin/python -m pytest tests/timebase/test_holding.py -q`. Expect FAIL.
- [ ] **Step 3: Implement.** "Last" means the last decoded code event. Decode errors are not codes.
- [ ] **Step 4:** `.venv/bin/python -m pytest tests/timebase -q`. Expect everything to pass.
- [ ] **Step 5: Mutation.** Drop the "last" condition; the first test fails. Clear `__pycache__` first, then
  restore.
- [ ] **Step 6:** commit `fix(timebase): an end code reaches the end only where it ends the log (amendment 62)`.

---

### Task 3: The NI's own barcodes, and the span they cover

**Files:**
- Modify: `wl_preproc/timebase/clock.py`: `Clock.barcode_reference` and a new `Clock.covered_span`.
- Test: `tests/timebase/test_ni_reference.py` (create).

**Interfaces:**
- `Clock.barcode_reference() -> dict[int, float]` under `nidq`: the first NI file's gap-clear barcodes, value →
  `start_us / 1e6`, the NI's seconds.
- `Clock.covered_span() -> tuple[float, float] | None`. Under `nidq`, `(min, max)` of the reference, or None when
  it is empty. Under `syncbox`, always None: the whole session is covered.

- [ ] **Step 1: Write the failing tests** on a synthetic session `NI_CLOCK_RECIPE` with
  `systems=("syncbox", "spikeglx", "ohdpi", "bcam")` and `syncbox_stop_s=9.0`, its clock chosen as the NI's:
  - `test_the_ni_reference_is_the_nis_own_barcodes`: each value maps to the truth barcode's time on the NI's clock
    (`t * (1 + ppm) + SPIKEGLX_PRE_ROLL_S`), within one NI sample.
  - `test_the_covered_span_runs_from_the_first_to_the_last`: `covered_span()` equals the reference's min and max.
  - `test_with_no_barcode_on_the_ni_the_reference_is_empty`: on `NI_CLOCK_RECIPE` without a sync box, the
    reference is `{}` and the span is None.
  - `test_under_the_sync_box_the_span_is_none`.
- [ ] **Step 2:** run it. Expect FAIL with `NoClock("the NI's clock places no barcode")`.
- [ ] **Step 3: Implement.** Reuse the spikeglx scan: `segments.scan_system("spikeglx", …)` filtered to
  `nidq_files(...)[0]`. Cache it the way `Clock`'s other reads are.
- [ ] **Step 4:** `.venv/bin/python -m pytest tests/timebase -q`. Expect everything to pass.
- [ ] **Step 5:** commit `feat(timebase): under the NI, its own barcodes are the reference (amendments 54-55)`.

---

### Task 4: Systems carrying barcodes, fitted under the NI

**Files:**
- Modify: `wl_preproc/schema/timebase.py`:
  - `SystemTimebase.make`'s NI branch;
  - `TimingProvenance`: `failed` excludes `outside_covered_span`; the `syncbox_fitted` gate; the box's losses not
    asked under the NI; the columns `covered_start_s=null : double` and `covered_end_s=null : double`.
- Modify: `wl_preproc/schema/core.py`: `Segment.make`'s NI branch.
- Modify: `wl_preproc/timebase/segments.py`: `OUTSIDE_COVERED_SPAN = "outside_covered_span"` in
  `REJECTION_REASONS`, with a comment in its neighbours' style.
- Modify: `tests/schema/test_ni_clock.py`: the `partial` session moves to `syncbox_stop_s=12.0`, and its tier
  becomes N (Decision 5).
- Test: `tests/schema/test_partial_sync_box.py` (create), with a `landed` module fixture in `test_ni_clock.py`'s
  pattern.

**Behaviour under the NI:**
- For `syncbox`, `ohdpi` and `bcam`, with a nonempty reference, `SystemTimebase` takes the existing barcode path
  against `clock.barcode_reference()`, with `time_source='barcode'`.
- `Segment.make` takes the existing barcode loop for them, with one change: a file that decoded barcodes, none in
  the reference, is `OUTSIDE_COVERED_SPAN` in place of `NO_SHARED_BARCODE`.
- A system with an empty reference keeps 3b-1's branch (Decision 2).
- In `TimingProvenance` under the NI:
  - a rejection whose reason is `OUTSIDE_COVERED_SPAN` is not a failure;
  - a system that is `unfittable` because every file is `OUTSIDE_COVERED_SPAN` is not a failure either;
  - the box's `fails_the_timing_check` is not asked;
  - `syncbox_fitted` is false;
  - the span is stored.

- [ ] **Step 1: Write the failing tests.** The fixture lands these sessions, each on `NI_CLOCK_RECIPE`, with the
  sync box, `ohdpi` and `bcam`, `syncbox_stop_s=9.0`, and drifting `ohdpi` and `bcam`:
  - `partial`;
  - `before_ni`, where a `bcam` file's barcodes all precede the NI's first sample. Build it by trimming the NI
    file's head past them with `test_clock.py::_cut_the_ni`'s pattern, or say why another construction is truer;
  - `dead_ni_line`, where bit 0 of the NI's digital word 0 is cleared, so the NI decodes no barcode;
  - `synced`, with the sync box keeping time (no stop).

  The tests:
  - `test_the_eye_tracker_and_camera_are_fitted_through_the_nis_barcodes`: `ohdpi` and `bcam` are `fitted` with
    `barcode`. Each segment's offset maps the truth of its first barcode onto the NI's time within 100 µs.
  - `test_the_sync_box_is_fitted_through_the_nis_barcodes`.
  - `test_a_partial_sync_box_session_is_tier_n_and_records_its_span`: tier N, and `covered_start_s` /
    `covered_end_s` equal `Clock.covered_span()` within 1e-9.
  - `test_a_file_whose_barcodes_precede_the_ni_is_outside_and_does_not_fail`: its rejection is
    `outside_covered_span`, and the tier is N.
  - `test_an_empty_reference_places_nothing_by_barcode`: on `dead_ni_line`, `ohdpi` and `bcam` are `unfittable`
    with `time_source` `none`, and the tier is D.
  - `test_under_the_sync_box_nothing_changes`: on `synced`, the rows equal those of a session landed before this
    task's change. Assert the fields this task could touch: `time_source`, `fit_status`, rejection reasons and the
    tier.
  - `test_the_passes_report_no_error`: as `test_ni_clock.py`'s.
- [ ] **Step 2:** `.venv/bin/python -m pytest tests/schema/test_partial_sync_box.py -q`. Expect FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** `.venv/bin/python -m pytest tests/schema/test_partial_sync_box.py tests/schema/test_ni_clock.py tests/schema/test_intan_by_strobes.py tests/schema/test_timebase.py tests/schema/test_syncbox_losses.py -q`.
  Expect everything to pass, with `partial` now tier N.
- [ ] **Step 5: Mutations.**
  - Count `OUTSIDE_COVERED_SPAN` as a failure: the before-NI test fails.
  - Drop Decision 2's branch: the empty-reference test fails.

  Clear `__pycache__` each time, then restore.
- [ ] **Step 6:** commit `feat(timebase): under the NI, the eye tracker, cameras and sync box are fitted by the NI's barcodes (amendments 56-57, 60-61)`.

---

### Task 5: The cross-check

**Files:**
- Modify: `wl_preproc/events/agreement.py`: `code_containment`.
- Modify: `wl_preproc/schema/timebase.py`: `TimingProvenance`'s NI branch.
- Test: `tests/events/test_agreement.py`, and `tests/schema/test_partial_sync_box.py` (append).

**Interfaces:**
- `code_containment(part: Sequence[int], whole: Sequence[int]) -> float`: the fraction of `part` matched in order
  in `whole`, on `code_agreement`'s anchored matcher with the same whole-stream band. It is 1.0 for an empty
  `part`.
- The box's words: `SyncBoxSession.codes()` with time in `[lo, hi]`, outside every `losses()` window, where `lo`
  and `hi` are the box's own `reference()` times of the smallest and largest NI-reference values it also holds.
  - A window with `start_s` None runs from −∞.
  - A window with `stop_s` None runs to +∞.
- Tier under the NI: N needs everything 3b-1 needs, and the containment ≥ `AGREEMENT_THRESHOLD` where a box word
  exists. Then `event_code_agreement = containment` and `n_full_code_records = 2`.

- [ ] **Step 1: Write the failing tests:**
  - `test_containment_of_an_exact_part_is_one`.
  - `test_containment_counts_only_the_part`: a part of 10 words inside a whole of 1000 gives 1.0, where
    `code_agreement` gives 0.01.
  - `test_containment_tolerates_a_dropped_word_in_the_whole`: assert the value equals 9/10, by `approx`.
  - `test_a_partial_box_that_agrees_records_its_agreement`: on `partial`, `event_code_agreement == 1.0` and
    `n_full_code_records == 2`.
  - `test_a_box_loss_inside_its_part_does_not_fail_the_cross_check`: a `partial` session with a `desync` closed
    inside the box's part (`SyncBoxLossSpec`). Its words in the window are removed from the box's log, as the
    box would lose them. Tier N.
  - `test_a_box_whose_codes_disagree_is_d`: a `partial` session with one box code word changed by the fixture,
    using `test_ni_clock.py::_flip_a_payload_word`'s pattern applied to the box's log. Tier D, with
    `event_code_agreement < 0.999`.
- [ ] **Step 2:** run them. Expect FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** run the two test files and `tests/events`. Expect everything to pass.
- [ ] **Step 5: Mutations.**
  - Use `code_agreement` in place of containment: the agreeing-partial test fails.
  - Skip the loss windows: the loss test fails.

  Clear `__pycache__` each time, then restore.
- [ ] **Step 6:** commit `feat(timebase): a partial sync box's codes cross-check the NI's over what it recorded (amendment 59)`.

---

### Task 6: Eye data only inside the span

**Files:**
- Modify the eye stages that read samples or place them in session time:
  - `wl_preproc/schema/eye.py`: `EyeCalibration` (`_containing_segment` and the trial selection) and `EyeQuality`;
  - `wl_preproc/schema/detect.py`: both `read_ohdpi` sites;
  - `wl_preproc/schema/main_sequence.py`.
- Create, if it keeps the rule in one place: `wl_preproc/eye/span.py`, holding
  `inside_span(times: np.ndarray, span: tuple[float, float] | None) -> np.ndarray` (an all-True mask for None).
- Test: `tests/schema/test_partial_sync_box.py` (append).

**Interfaces:**
- Each stage gets the span from `clock_for(session_key, session_dir).covered_span()`.
- Each stage uses only samples whose session time is inside it:
  - a calibration trial outside it is not a calibration trial;
  - a detected event outside it is not stored;
  - quality counts only samples inside it;
  - the main sequence takes only events inside it.

- [ ] **Step 1: Write the failing tests** on `partial`, whose eye recording runs past the stop:
  - `test_no_eye_stage_uses_a_sample_past_the_span`: every stored detected event's session time lies inside the
    span, and so does every calibration trial used.
  - `test_eye_quality_counts_only_the_span`: the counted samples equal the samples inside the span.
  - `test_under_the_sync_box_the_eye_stages_are_unchanged`: on `synced`, every eye table's rows equal those of the
    unchanged code path, which uses the whole recording.
- [ ] **Step 2:** run them. Expect FAIL.
- [ ] **Step 3: Implement.** Read each stage before changing it. The rule is one mask, applied where samples
  enter.
- [ ] **Step 4:** run `tests/schema/test_partial_sync_box.py` and `tests/schema -k "eye or detect or main_sequence"`.
  Expect everything to pass.
- [ ] **Step 5:** commit `feat(eye): eye data is used only inside the span the sync box covered (amendment 58)`.

---

### Task 7: The NWB file

**Files:**
- Modify: `wl_preproc/nwb/timebase.py`, the NI texts:
  - `system_clocks.time_source` gains `barcode`, "a system the NI's own barcodes placed, over the part the sync
    box covered";
  - `fitted_rate`, `residual_rms`, `worst_residual` and `first_sample` say barcodes where they place.
- Modify: `wl_preproc/nwb/gather.py`:
  - eye samples and events limited to the span (`inside_span`, Task 6);
  - a note naming the stretch left out ("eye data outside the sync box's covered span, {a:.3f} s to {b:.3f} s, is
    left out: no barcode placed it") and each `outside_covered_span` file.
- Test: `tests/schema/test_partial_sync_box.py` (append); `tests/nwb/test_writers.py`, for the NI texts.

- [ ] **Step 1: Write the failing tests:**
  - `test_the_nwb_eye_series_ends_inside_the_span`: on `partial`'s NWB file, every eye timestamp lies inside the
    span.
  - `test_the_nwb_file_names_what_it_left_out`: the notes hold the sentence with the span's end and the session's
    end.
  - `test_the_nwb_file_says_barcodes_placed_the_eye_tracker`: `system_clocks` rows `ohdpi`/`barcode` and
    `spikeglx`/`clock`, with the texts verbatim.
  - `test_a_sync_box_nwb_file_is_unchanged` passes unedited.
- [ ] **Step 2:** run them. Expect FAIL.
- [ ] **Step 3: Implement.** Write the four rewritten NI texts in the file's voice. Each must be true under the NI
  with and without barcodes, and the test pins them.
- [ ] **Step 4:** run `tests/nwb`, `tests/schema/test_partial_sync_box.py` and `tests/schema/test_ni_clock.py`.
  Expect everything to pass.
- [ ] **Step 5:** commit `feat(nwb): under the NI, barcodes place what they place, and eye data stays inside the span (amendment 63)`.

---

### Task 8: Records

- Spec: `## Amendments, 2026-11-20, made while planning part 3b-3`, holding this plan's Decisions 1-5, one line
  each.
- `docs/ops/lab-host-protocol.md`: tier N may hold a partial sync box, its eye tracker and cameras placed over its
  part; the new reason `outside_covered_span`.
- `docs/pending-wl-works-amendments.md`: the NI entry gains a line saying the same. A listed rejected segment's
  `reason` may be `outside_covered_span`, which the listing types as a plain string.
- `docs/handoffs/2026-11-20-part-3b-3.md`, in 3b-2's shape, with the `FINAL_REVIEW` and `SUITE_RESULTS`
  placeholders.
- Commit `docs: part 3b-3's amendment, wl.works' line and the handoff`.

---

## Self-review

- **Coverage.**

  | Amendment | Task |
  |---|---|
  | 54-55 | 3 |
  | 56-57, 60-61 | 4 |
  | 58 | 6 and 7 |
  | 59 | 5 |
  | 62 | 2 |
  | 63 | 7 |
  | 64 (testing) | 1, 4-7 |

  Amendment 65 is out of scope.
- **Types.**
  - `covered_span()` returns `(float, float) | None`, and `inside_span` takes it.
  - `code_containment(part, whole)` takes `int` sequences.
  - `OUTSIDE_COVERED_SPAN` is one constant.
