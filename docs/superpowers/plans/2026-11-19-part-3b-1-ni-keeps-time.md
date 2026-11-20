# Part 3b-1, the NI keeps time: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A session whose sync box did not record it throughout is timed by the NI when SpikeGLX's codes hold
every run. Its events, runs, SpikeGLX segments and probes are placed on the NI's clock, it is tier N (or D), and
it reaches the listing and the NWB file. Every segment is keyed by its file's path.

**Architecture:**
- **Choosing.** `timebase/clock.py` grows from a sync-box-only door into a choice between two clocks.
  `timebase/holding.py` (new) decides whether a code stream holds every run the rig recorded. `choose` applies
  §4.2's rules 1 and 2 with amendments 41-44 and 47. `SessionClock` decodes the NI outside its transaction,
  through DataJoint's three-part make.
- **Placing.** `Clock('nidq')` answers the same three questions the sync box's does: codes, the wall-clock time
  of t = 0, and how each system is placed. `SystemTimebase`, `core.Segment` and `TimingProvenance` branch on the
  clock.
- **Renaming.** The segment key becomes `segment_id`, the file's path, through every table, the listing and the
  NWB build.

**Tech Stack:** Python 3.11/3.13, DataJoint 2.3 (three-part `make_fetch`/`make_compute`/`make_insert`), pydantic
contracts, pytest with the MySQL testcontainer.

**Spec:** `docs/superpowers/specs/2026-10-18-training-records-and-backup-clock-design.md`: §4.2-4.4, §6.2, §7, and
amendments 27-38 as built by 3a. 3b-1 is amendments 39, 41-48, 50 and 51. Groundwork:
`~/.cache/wl-preproc-scratch/2026-10-30-part3b/groundwork.md`.

## Global Constraints

- **The segment key** is `segment_id`, text: the file's path within its system's folder, exactly the string
  `core.Segment.make` computes today (`str(scan.path.relative_to(system_dir))`, or `"."`). It replaces
  `segment_barcode` everywhere. `core.Segment` has no separate `file_path` attribute. `core.RejectedSegment`
  keeps its own `file_path` key; it names rejected files, not segments. Development databases are rebuilt,
  never migrated.
- **The rules:**
  - **Rule 1:** the sync box keeps time if it has a log, was not landed without (`ingest.LandedWithout`), and
    either its codes hold every run, or it restarted within the session (a segment after a restart,
    `SyncBoxSession.roles()`).
  - **Rule 2:** otherwise the NI keeps time, if SpikeGLX was not landed without and its NI codes, across every
    NI file, hold every run.
  - **Otherwise `NoClock`:** lab time is part 4.
- **Holding a run** (amendments 41 and 44):
  - **With the rig's record:** every run the rig's record names (`events/rigruns.py::RigRun.number`) has a run
    of that number in the stream's assembly. Every trial the rig's trial lines give for that run
    (`events/rigtrials.py`, `trial_number`) is an assembled trial with that `trial_id`, and is closed (`end_s`
    not None). A run missing only its RUN_END is held.
  - **With no rig record:** the stream holds the session when it decodes both `SESSION_START` and
    `SESSION_END`.
  - **Codes outside the NI's recording are not required:** the session's start and end codes, where a rig
    record exists.
- **Reasons, exactly:**
  - `"the sync box's codes hold every run the rig recorded"`;
  - `"the sync box restarted within the session; it stays the clock, its restart bridged by wl-sync part 2"`;
  - `f"the NI's codes hold every run the rig recorded; the sync box {why}"`, where `why` is `"recorded no log"`,
    `f"was landed without by {actor}"` or `f"does not: {sentence}"`.
  - Every reason is clipped to 255 characters, ending `"…"`.
- **Under the NI clock:**
  - t = 0 is the first NI file's first sample, at its `.meta`'s rate. Every SpikeGLX stream of that file is on
    the identity map.
  - A later NI file is a restart: rejected with `segments.NI_RESTARTED = "ni_restarted"` (part 3c joins it).
  - Every other system with recordings (rhs, ohdpi, bcam, and a landed partial syncbox log) has no way onto the
    clock yet: `SystemTimebase` `fit_status='unfittable'`, `time_source='none'`, and its files are rejected
    `UNFITTED_SYSTEM`. So the session is tier D (amendment 51) through `TimingProvenance`'s existing `failed`.
- **Tier N:**
  - **N requires all of these:** the clock is the NI, nothing failed, the NI's codes decode with no errors, and
    they hold every run (re-checked).
  - **Under the NI these are null:** `n_barcodes_emitted`, `event_code_agreement` and `trial_count_agreement`.
  - **Enums gain only what 3b-1 uses:**
    - tier: `N`;
    - `SystemTimebase.time_source`: `clock` and `none`;
    - `reference_source`: `rig_clock` and `nidq_meta`.
    
    `L` and `strobes` come with their parts.
- **The wall-clock time of t = 0 under the NI** (amendment 46), checked in this order:
  1. **The rig's clock:** `core.RigClock.rig_at_zero`, as UTC, when within `CLOCK_DISAGREEMENT_S` (60 s) of the
     manifest's `started_at` (`'rig_clock'`).
  2. **The NI file's creation time:** the first NI file's `fileCreateTime`, read in the lab's NI time zone, when
     that zone is set (`wlpp daemon --nidq-timezone <IANA name>`) and it too is within 60 s (`'nidq_meta'`).
  3. **Otherwise the manifest's `started_at`** (`'manifest'`).
- **The NI's codes** are read by `events/extract.py::extract_nidq_words`, chunked as it is now. Decoding happens
  only when rule 1 fails, and never inside `SessionClock`'s transaction.
- **The test process:**
  - Never commit while a test run is going on the same checkout.
  - At the merge, run the full suite on 3.11 and 3.13 plus every reference-gated file with every reference set.
  - `wl-check` after every `wl.yaml` edit.
  - Commit trailers as the session's.

## Review Focus

1. **A sync box whose log ends inside the last run, with no later segment:** the NI keeps time, with the reason
   naming the run. One that restarted stays the clock. (Task 3,
   `test_a_sync_box_that_stops_inside_a_run_hands_the_session_to_the_ni`, and
   `test_a_sync_box_that_restarted_stays_the_clock`.)
2. **A faulted run with no RUN_END on a healthy sync box:** the sync box stays the clock. (Task 3,
   `test_a_run_without_its_end_code_is_still_held`.)
3. **An NI that started after `SESSION_START`:** still the clock, since rule 2 needs only the runs. (Task 3,
   `test_codes_before_the_ni_started_are_not_required`.)
4. **Two NI files:** the first is the clock, the second is rejected `ni_restarted`, and the session is tier D.
   (Task 4, `test_a_second_ni_file_is_a_restart_and_the_session_is_tier_d`.)
5. **A session with timed results and no clock row:** refused, naming what to delete, never silently given the
   NI; the back-fill gives it `syncbox`. (Task 3, `test_a_timed_session_without_a_clock_is_refused`, and
   `test_the_backfill_records_the_sync_box_for_timed_sessions`.)

---

### Task 1: Synthetic sessions without the sync box

**Files:**
- Modify: `wl_preproc/synth/recipe.py` (`_coherent`, the `"syncbox is present at every session"` check, :507-508;
  a profile `NI_CLOCK_RECIPE` and `RECIPES["ni-clock"]`)
- Modify: `wl_preproc/synth/timeline.py` (`build_timeline`: no barcodes when `"syncbox"` is not in
  `recipe.systems`)
- Modify: `wl_preproc/synth/spikeglx.py` (`_nidq_meta_text`: `fileCreateTime`)
- Test: `tests/synth/test_recipe.py`, `tests/synth/test_spikeglx.py`

**Interfaces:**
- Produces: `NI_CLOCK_RECIPE`: `CI_RECIPE`'s blocks with `systems=("spikeglx",)`, `runs=True`, `session_id=
  "2027-03-14_07"`, `seed=20270319`. `RECIPES["ni-clock"]`.
- Produces: `SessionRecipe.nidq_timezone: str = "America/New_York"`, the zone the synthetic NI machine's
  `fileCreateTime` is written in.
- Produces: `fileCreateTime=<YYYY-MM-DDTHH:MM:SS>` in every `.nidq.meta`. It is the local time in
  `recipe.nidq_timezone` of `SYNTH_EPOCH - SPIKEGLX_PRE_ROLL_S`, the NI's first sample, truncated to the second.

- [ ] **Step 1: Write the failing tests.**
  - `test_a_session_without_the_sync_box_is_a_recipe`: `SessionRecipe(**{**CI_RECIPE.model_dump(), "systems":
    ("spikeglx",)})` validates. `systems=()` still raises (the existing message for no system, if any; otherwise
    leave that case alone).
  - `test_without_the_sync_box_no_device_carries_a_barcode`: generate `NI_CLOCK_RECIPE`. `truth.barcodes == ()`,
    and `timebase/segments.py::scan_system("spikeglx", ...)` finds zero barcodes in the NI file. Its code words
    still decode, and `extract_nidq_words` yields `len(truth.code_words)` words.
  - `test_the_ni_meta_names_its_creation_time`: the `.nidq.meta` has `fileCreateTime` equal to the value above.
    For `SYNTH_EPOCH` 2027-03-14 09:00 UTC that is `2027-03-14T05:00:00` minus the pre-roll, truncated (EDT,
    UTC-4).
- [ ] **Step 2: Run.** `.venv/bin/python -m pytest -q tests/synth/test_recipe.py tests/synth/test_spikeglx.py -k
  "sync_box or creation_time"`. Expected: FAIL, the recipe refusing ("syncbox is present at every session").
- [ ] **Step 3: Implement.** Drop the check, then add the profile, the barcode-free timeline and `fileCreateTime`.
  Keep every existing profile byte-identical; the existing synth tests are the proof.
- [ ] **Step 4: Run** `tests/synth`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(synth): a session without the sync box, its NI carrying no barcode ...`.

### Task 2: The segment key is the file's path

**Files:**
- Modify: `wl_preproc/schema/core.py` (`Segment`'s definition and `make`), `wl_preproc/schema/ephys.py` (six uses,
  :230-732), `wl_preproc/nwb/gather.py` (:642-663), `wl_preproc/listing/entry.py` (:91-307),
  `wl_preproc/contracts/protocol.py` (`ListedSegment.segment_barcode: int` to `segment_id: str`;
  `ListedRun.segments: list[int]` to `list[str]`, :440 and :483), `wl_preproc/responder/jobs.py:134`'s comment
- Regenerate: `docs/schemas/session_listing.json` (`wlpp schemas export`, as CI checks)
- Test: every test that names `segment_barcode` (`grep -rl segment_barcode tests`), plus a new
  `tests/schema/test_segment_id.py`

**Interfaces:**
- Produces: `core.Segment` key `(subject, session_datetime, system, segment_id)`, with `segment_id :
  varchar(255)` (the path) and no `file_path` attribute. `ephys.ProbeCensus`, its part and `SegmentConfig` are
  keyed through it. The listing's `segments[].segment_id: str` and `runs[].segments: list[str]`.

- [ ] **Step 1: Write the failing tests.**
  - `test_a_segment_is_keyed_by_its_files_path`: after populate on `CI_RECIPE`, the spikeglx segment's
    `segment_id` is the `.nidq.bin`'s path relative to `spikeglx/`. No `file_path` attribute exists on
    `core.Segment`.
  - `test_the_listing_names_segments_by_path`: the listed `segments[].segment_id` and `runs[].segments` are those
    strings.
  - Rename the field in every existing assertion that names it.
- [ ] **Step 2: Run** the touched files. Expected: FAIL on the missing attribute.
- [ ] **Step 3: Implement** the rename. `nwb/timebase.py`'s `segments` table keeps its `file_path` column, filled
  from `segment_id`, since its description already names the file. Regenerate the exported schema.
- [ ] **Step 4: Run** `tests/schema tests/listing tests/nwb tests/contracts`, then
  `.venv/bin/python -m wl_preproc.cli.main schemas export --out docs/schemas` and `git diff --exit-code
  docs/schemas`. Expected: PASS, and the schema changed only in the segment fields.
- [ ] **Step 5: Commit** `feat(schema): a segment is keyed by its file's path, segment_id ...`.

### Task 3: Which clock: the sync box, else the NI

**Files:**
- Create: `wl_preproc/timebase/holding.py`
- Modify: `wl_preproc/timebase/clock.py` (`choose`, `choose_for`, `Clock`, `clock_for`, `STAGED_REASON` retired,
  `SOURCES` used by `Clock`'s check)
- Modify: `wl_preproc/schema/timebase.py` (`SessionClock`: three-part make; `backfill_session_clocks`)
- Modify: `wl_preproc/cli/main.py` (`wlpp clocks backfill`)
- Test: `tests/timebase/test_holding.py` (new), `tests/timebase/test_clock.py`, `tests/schema/test_session_clock.py`

**Interfaces:**
- Produces: `holding.unheld(decoded: list[DecodedEvent], rig_runs: RigRuns | None, rig_trials: RigRecord | None)
  -> str | None`. It returns None when the stream holds every run (Global Constraints), else one sentence, e.g.
  `"run 3's trial 41 is not in its codes"`, `"run 2 is not in its codes"`, or `"its codes hold no SESSION_END"`.
- Produces: `clock.nidq_files(session_dir: Path) -> list[Path]`, the session's NI recordings
  (`find_recordings("spikeglx", ...)`), sorted. `nidq_codes(path: Path) -> list[tuple[float, int]]`: `(sample /
  fs_hz, code)` from `extract_nidq_words`, seconds from that file's first sample.
- Produces: `choose(session_dir: Path, subject: str, landed_without: frozenset[str] = frozenset()) -> Choice`.
  It raises `NoClock`.
- Produces: `Clock(source, session_dir)`, accepting `"syncbox"` and `"nidq"` (`"lab"` still raises `NoClock`).
  For `"nidq"`, `codes()` is the first NI file's codes (`nidq_codes(nidq_files(d)[0])`). A later file is a
  restart, not on this clock (`NI_RESTARTED`, Task 4). `barcode_reference()` raises `NoClock("the NI's clock
  places no barcode")`.
- Produces: `timebase.backfill_session_clocks() -> int`, the number of sessions given `syncbox` with
  `BACKFILL_REASON = "it was timed by the sync box before its clock was recorded (back-filled at part 3b-1's
  deploy)"`.

- [ ] **Step 1: Write the failing tests** (synthetic sessions from Task 1; the sync box's log cut with the
  existing `Fault`/loss helpers where one fits, or by truncating the log's file):
  - `test_holding.py`, each case on hand-built decoded streams and rig records:
    - `test_every_run_and_trial_present_is_held`;
    - `test_a_run_without_its_end_code_is_still_held`;
    - `test_a_missing_trial_is_not_held_and_says_which`;
    - `test_a_missing_run_is_not_held`;
    - `test_without_a_rig_record_the_session_markers_decide`;
    - `test_codes_before_the_ni_started_are_not_required` (`SESSION_START` absent, every run present: held).
  - `test_clock.py`:
    - `test_a_full_sync_box_keeps_time` (reason exact);
    - `test_a_sync_box_that_stops_inside_a_run_hands_the_session_to_the_ni` (reason starts `"the NI's codes hold
      every run the rig recorded; the sync box does not: run "`);
    - `test_a_sync_box_that_restarted_stays_the_clock` (the restart recipe of
      `tests/schema/test_syncbox_restart.py`);
    - `test_no_sync_box_log_and_an_ni_is_the_ni` (`NI_CLOCK_RECIPE`, reason ends `"recorded no log"`);
    - `test_a_landed_without_spikeglx_is_not_the_clock` (`NoClock`);
    - `test_neither_clock_holds_the_runs_is_no_clock` (message names both reasons and "lab time is part 4");
    - `test_a_reason_is_clipped_to_its_column`;
    - `test_the_ni_clock_gives_the_ni_codes`: `Clock("nidq", d).codes()` matches `truth.code_words` at
      `apply_drift(t, drift) + SPIKEGLX_PRE_ROLL_S`, within one NI sample;
    - extend the guard test (:120) to every way of reaching the sync box directly (amendment 50):
      `extract_syncbox_words`, `extract_syncbox` with `decode_edges`, and aliased imports, read by `ast`, not
      regex, so a docstring does not trip it.
  - `test_session_clock.py`:
    - `test_the_ni_is_decoded_outside_the_clocks_transaction`: spy `extract_nidq_words` and assert
      `dj.conn().in_transaction` is False inside it during `SessionClock.populate()`;
    - `test_a_timed_session_without_a_clock_is_refused`: `NoClock`, the message saying its events must be
      deleted for it to be timed again;
    - `test_the_backfill_records_the_sync_box_for_timed_sessions`: one session timed and its clock row deleted,
      one never timed. Back-fill gives the first `syncbox` with `BACKFILL_REASON`, leaves the second, and returns
      1;
    - replace `test_a_staged_session_without_a_clock_keeps_the_sync_boxs` and
      `test_populate_gives_a_staged_session_the_sync_boxs_clock` (amendment 30's rule, retired by amendment 48)
      with the refusal.
- [ ] **Step 2: Run** the three files. Expected: FAIL (`holding` missing, `Clock` refusing `nidq`).
- [ ] **Step 3: Implement.**
  - **`choose`:** rule 1 reads `syncbox_codes` and `read_syncbox_session(...).roles()`, both small. Rule 2 runs
    only when rule 1 fails.
  - **`choose_for`:** reads `LandedWithout` for both systems. It refuses a session with a `BehaviorRecording` or a
    `TimingProvenance` row and no `SessionClock` row.
  - **`SessionClock` (three-part make):**
    - `make_fetch` returns `(session_dir, landed_without, already_timed)`;
    - `make_compute` calls `choose`;
    - `make_insert` inserts.
    
    Before relying on it, check against DataJoint 2.3.3's `autopopulate.py` that `make_compute` runs outside the
    transaction and that `make_fetch` is re-run and compared inside it. Ledger what you find.
  - **`clock_for`:** a session with no row outside any transaction computes the choice, then inserts. Inside a
    transaction it raises `NoClock("the session's clock is not recorded yet: a daemon pass records it first")`
    rather than decoding there.
  - **`wlpp clocks backfill`:** prints the count.
- [ ] **Step 4: Run** `tests/timebase tests/schema/test_session_clock.py tests/schema/test_events.py`. Expected:
  PASS.
- [ ] **Step 5: Commit** `feat(timebase): the NI keeps time where the sync box's codes do not hold every run ...`.

### Task 4: Devices on the NI's clock

**Files:**
- Modify: `wl_preproc/schema/timebase.py` (`SystemTimebase.make`, `_TIME_SOURCE_ENUM`)
- Modify: `wl_preproc/schema/core.py` (`Segment.make`)
- Modify: `wl_preproc/timebase/segments.py` (`NI_RESTARTED`, `REJECTION_REASONS`)
- Modify: `tests/schema/test_eye_populate.py:386`'s docstring (amendment 50)
- Test: `tests/schema/test_ni_clock.py` (new)

**Interfaces:**
- Consumes: `Clock`, `nidq_files` (Task 3); `segment_id` (Task 2); `NI_CLOCK_RECIPE` (Task 1).
- Produces:
  - **spikeglx, under `nidq`:** a `SystemTimebase` row with `fit_status='fitted'`, `time_source='clock'`,
    `fitted_rate_hz == nominal_rate_hz` (the `.meta`'s `niSampRate`), `drift_ppm=0` and residuals 0.
  - **The first NI file:** a `core.Segment` with `start_s=0.0`, `end_s = n_samples / rate`, `offset_s=0.0`,
    `residual_us=0.0` and `n_barcodes=0`.
  - **A later NI file:** rejected `ni_restarted`.
  - **Every other system:** `fit_status='unfittable'` with `time_source='none'`, its files rejected
    `unfitted_system`.
  - **The sync box's own identity row:** written only when the clock is the sync box.

- [ ] **Step 1: Write the failing tests** in `test_ni_clock.py`, each through a daemon pass on a landed synthetic
  session:
  - `test_the_ni_files_first_sample_is_session_time_zero` (rows as above, on `NI_CLOCK_RECIPE`);
  - `test_the_probes_are_placed_on_the_ni_clock` (`ephys.ProbeCensus` rows exist for the segment, sites as on a
    sync-box session of the same recipe);
  - `test_the_runs_and_trials_are_on_the_ni_clock` (`core.Run` and trial starts equal `truth`'s at
    `apply_drift(t, drift_ppm) + SPIKEGLX_PRE_ROLL_S`, within one NI sample);
  - `test_a_device_with_no_way_onto_the_ni_clock_is_unfittable` (`NI_CLOCK_RECIPE` with `("spikeglx", "ohdpi")`:
    ohdpi's row and rejection as above);
  - `test_a_second_ni_file_is_a_restart_and_the_session_is_tier_d` (a second `.nidq.bin` under its own
    `_g1` folder, written by the synth's restart path if it has one, else copied from the first. Its rejection
    is `ni_restarted`, and the tier is D);
  - `test_a_partial_sync_box_log_under_the_ni_is_unfittable_until_3b_3` (the sync box's row is unfittable with
    `time_source` `none`, and the tier is D).
- [ ] **Step 2: Run** `tests/schema/test_ni_clock.py`. Expected: FAIL.
- [ ] **Step 3: Implement.** Branch on `clock_for(...).source` in the three makes. `time_source` gains `clock` and
  `none`.
- [ ] **Step 4: Run** `tests/schema tests/timebase`. Expected: PASS, every sync-box test unchanged.
- [ ] **Step 5: Commit** `feat(timebase): SpikeGLX on the NI's clock; a device with no way onto it unfittable ...`.

### Task 5: Tier N

**Files:**
- Modify: `wl_preproc/schema/timebase.py` (`_TIER_ENUM` gains `'N'`, `n_barcodes_emitted` becomes nullable,
  `TimingProvenance.make`)
- Modify: `wl_preproc/contracts/protocol.py` (`tier: Literal["A", "B", "C", "D", "N"] | None`),
  `wl_preproc/listing/entry.py` if it maps tiers, and `docs/schemas/` regenerated
- Test: `tests/schema/test_ni_clock.py`, `tests/schema/test_session_listing.py`

**Interfaces:**
- Consumes: `holding.unheld`, `Clock("nidq").codes()`.
- Produces: `TimingProvenance` under `nidq` (Global Constraints):
  - **Tier N** when the session is not failed, has `decode_errors == 0`, and `unheld(...)` is None.
  - **Tier D** otherwise.
  - **Recorded either way:** `n_full_code_records=1`, `n_strobe_witnesses=0`, and
    `n_barcodes_emitted`/`event_code_agreement`/`trial_count_agreement` null.

- [ ] **Step 1: Write the failing tests:**
  - `test_an_ni_session_whose_codes_hold_every_run_is_tier_n`;
  - `test_an_ni_session_with_a_decode_error_is_tier_d` (flip one payload word in the NI file's word 1);
  - `test_the_listing_gives_an_ni_session_tier_n_and_clock_nidq`;
  - `test_sync_box_tiers_are_unchanged` (`CI_RECIPE` keeps its tier, as today's test pins it).
- [ ] **Step 2: Run.** Expected: FAIL (the enum refuses `N`).
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `tests/schema tests/listing tests/contracts` and the schema export diff. Expected: PASS.
- [ ] **Step 5: Commit** `feat(timebase): tier N, the NI kept time and its codes hold every run ...`.

### Task 6: t = 0 on the wall clock, and the NWB file under the NI

**Files:**
- Modify: `wl_preproc/timebase/clock.py` (`Clock.wall_reference(started_at, rig_at_zero: float | None = None)`;
  `NIDQ_TIMEZONE: str | None = None` and `set_nidq_timezone(name: str) -> None`, which validates through
  `zoneinfo.ZoneInfo`)
- Modify: `wl_preproc/timebase/_nidq_meta.py` (read `fileCreateTime` as `file_created: datetime.datetime | None`,
  naive)
- Modify: `wl_preproc/cli/main.py` (`wlpp daemon --nidq-timezone`)
- Modify: `wl_preproc/nwb/gather.py` (:728, passing `core.RigClock`'s `rig_at_zero` when a row exists)
- Modify: `wl_preproc/schema/nwb.py` (`reference_source` enum), `wl_preproc/contracts/nwb_description.py`
  (`reference_source` Literal; `clock`, following that module's own versioning rule in its docstring: added
  without a version bump only if the rule allows an additive field), `wl_preproc/nwb/timebase.py` (texts by
  clock), `wl_preproc/nwb/describe.py`, `wl_preproc/nwb/build.py`
- Test: `tests/timebase/test_clock.py`, `tests/nwb/test_timebase.py` (or the module testing `add_timebase`),
  `tests/schema/test_ni_clock.py`

**Interfaces:**
- Produces: `wall_reference` returns the same dict keys as `reference_from` (`reference_time`, `source`,
  `manifest_started_at`, `started_at_difference_s`), with `source` one of `barcode`, `manifest`, `rig_clock` or
  `nidq_meta`.
- Produces: `add_timebase(..., clock: str)`. Under `nidq`:
  - the module description is `"How every time in this file was placed on session time: t = 0 is the NI's first
    sample, the NI keeping time because the sync box did not record the session throughout. PTP, where present,
    is a cross-check only."`;
  - the tier text is `"A to D, or N: the NI kept time and its codes hold every run (parent spec section 4.7; design
    spec 2026-10-18 section 7)."`;
  - the drift text is `"Clock drift against the session's clock, parts per million."`;
  - `clock_reference.source`'s text lists all four sources.
  
  Under the sync box every text is unchanged.

- [ ] **Step 1: Write the failing tests:**
  - `test_t_zero_comes_from_the_rig_clock_when_it_agrees` (`rig_at_zero` 20 s from `started_at`: `rig_clock`);
  - `test_a_rig_clock_past_a_minute_falls_back_to_the_ni_file` (zone set: `nidq_meta`, reference equal to the
    synthetic `fileCreateTime` in `America/New_York`, as UTC);
  - `test_without_a_zone_the_manifest_places_t_zero` (`manifest`);
  - `test_an_unknown_zone_is_refused_at_the_daemon` (`set_nidq_timezone("Mars/Base")` raises `ValueError`);
  - `test_an_ni_session_builds_an_nwb_file_saying_the_ni_kept_time` (end to end on `NI_CLOCK_RECIPE` with runs
    and a montage: the file builds, `processing/timebase`'s description is the NI's, `timing_provenance.tier ==
    "N"`, and the description's `reference_source` is one of the four);
  - `test_a_sync_box_nwb_file_is_unchanged` (the existing texts, pinned).
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `tests/nwb tests/schema tests/timebase tests/contracts` and the schema export diff.
  Expected: PASS.
- [ ] **Step 5: Commit** `feat(nwb): an NI-timed session's file says the NI kept time; t = 0 from the rig's clock
  or the NI file ...`.

### Task 7: The records, and wl.works' amendment

**Files:**
- Modify: `docs/pending-wl-works-amendments.md` (a new entry, in its format: `segment_id` string paths, tier N,
  `clock: nidq`, the reference sources `rig_clock` and `nidq_meta`, at 3b-1's merge)
- Modify: `wl.yaml` (`status`), `docs/CHECKPOINT.md`, `docs/handoffs/2026-11-19-part-3b-1.md` (new)
- Modify: `contracts/manifest.py`'s docstring if it still says the timebase is always the sync box (§5's last
  line: true of the label only)

- [ ] **Step 1:** Write the amendment entry, the handoff (what was built, rulings from the ledger, the suite), and
  the status lines.
- [ ] **Step 2:** `wl-check`. Expected: `wl.yaml: no findings`.
- [ ] **Step 3: Commit** `docs: part 3b-1's records ...`.
