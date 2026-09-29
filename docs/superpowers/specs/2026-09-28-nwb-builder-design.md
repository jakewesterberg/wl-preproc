# The NWB builder: one activation's file, for everything that exists before ephys

**Design spec, 2026-09-28.** The first of three pieces of Phase 3's NWB export
(parent spec `2026-08-12-wl-preproc-design.md` §8). It builds the file. Piece 2
(publication and the canonical lifecycle) and piece 3 (ephys content) get their
own specs.

**Why now.** Phase 2b is blocked on the compute machine, and NWB export is the
product this pipeline exists for. Nothing of it exists yet: no design, no
code, no declared dependency. Until it does, every scratch reclamation needs a
recorded force, because `archive/reclaim.py`'s `canonical_nwb_present` is a
hard-coded `False`.

**The requester's decisions, 2026-09-28:**
- **The builder first**, before the lifecycle and publication.
- **The subject's species, sex and date of birth come from wl.works,** in the
  job request that already carries each session's ELN metadata.
- **All six detectors' events go into every file,** each labelled, with the
  agreement scores between them.
- **Built directly on `pynwb`,** not NeuroConv.
- **The layout in §3**, and the time, writing and validation rules in §4–§8.
- **A timebeat (PTP) clock will be injected into every system.** Per wl-works'
  time-service design (`wl-works/docs/superpowers/specs/
  2026-09-20-row-60-lab-time-service-design.md` §0.1 and §4), the sync box's
  barcode stays the authoritative alignment signal and PTP is a redundant
  cross-check, never an alignment source. §4.4 is the rule that follows here.

---

## 1. What it is, and what it is not

**It is:** a function that, given one `request.Activation`, writes one NWB
file over that activation's blocks, from the tables this pipeline already
fills and the raw ohDPI file. Plus the daemon stage and the command that run
it, and the table that records what was written.

**It is not:**
- **Publication.** Moving the file to the analysis array, the automatic
  12-hour canonical activation and its retries, superseding, and the real
  `canonical_nwb_present` query are piece 2. This piece writes to a scratch
  root and records the result.
- **Ephys.** The electrode table, units, LFP and MUA are piece 3, after Phase
  2b. Their places in the file (`/general/extracellular_ephys`, `/units`,
  `processing/ecephys`) are left empty and nothing here uses those names.
- **Products not built yet:** photodiode, behaviour video, stimulation events,
  characterization maps, the capability report.
- **wl.works' block verdicts** (`block_behaviour_assertion`,
  `block_neural_assertion`). The job request does not carry them, so the
  canonical block set is every block in the montage (§5).

## 2. Architecture

- **A package, `wl_preproc/nwb/`.**
  - One writer per part of the file (§3), each taking plain data and adding
    to an `NWBFile`, so each is tested without a database.
  - A thin reader layer that fetches that data from the tables.
  - One entry point, `build(activation_key, out_path) -> BuildResult`. It
    builds, writes (§6), re-opens, checksums (§7) and validates (§8), and
    returns the status, the per-dataset checksums and the inspector's
    findings.
- **A table, `nwb.NwbFile`,** keyed on `request.Activation`, with a part
  table `NwbFile.Dataset` (§7). Columns: `status enum('written','invalid',
  'refused')`, `path`, `n_bytes`, `built_at`, `nwb_identifier`,
  `reference_time`, `reference_source enum('barcode','manifest')`,
  `started_at_difference_s`, `n_critical`, `inspector_findings` (the house
  `<blob>` codec), `reason`.
- **A bespoke daemon stage,** `_nwb_stage(nwb_root)`, after the computed
  tables and before the archive stage.
  - Opt-in exactly like the archive stage: without an `nwb_root` it does not
    run and reports `nwb=None`.
  - Key source: every `Activation` without an `NwbFile` row, skipping freed
    sessions.
  - Errors are caught per activation and returned, as the archive stage does.
  - A bespoke stage rather than a `dj.Computed` table because it writes a
    file under a configured root, which a `make()` cannot be given. *Ruled
    while writing this spec: the design as first presented said a computed
    table. Cost if wrong: the stage follows the archive stage's pattern
    instead of `populate`'s job reservation.*
- **A command, `wlpp nwb build <subject> <session_datetime> <montage_id>
  <activation_id> --out <path>`,** which runs the same function for one
  activation.

  *Ruled while planning, 2026-09-28: `build(activation_key, nwb_root)` takes
  the root and derives the path from the identifier (section 6), and the
  command takes `--subject`, `--session-datetime`, `--montage-id`,
  `--activation-id` and `--nwb-root`. One path rule, in one place.*
- **Declared dependencies:** `pynwb>=4.1,<5` (installed today only because
  element-animal pulls it in) and `nwbinspector`, each with a reason in
  `wl.yaml`'s `third_party`.

## 3. The file

Every time is session seconds (§4). Every dataset is trimmed to the
activation's blocks (§5).

**File-level metadata**
- `identifier`: `{session_id}.montage-{m}.activation-{a}`.
  - *Amended by the final review, 2026-09-29 (its C1): `{subject}.{session_id}.montage-{m}.activation-{a}`.
    A session id is the sync box's date and index, not scoped to a subject,
    so two animals can share one; `archive/stage.py::nas_root_for_subject`
    fixed the same defect for the NAS copy.*
- `session_id`: the manifest's session id (the session directory's name).
- `session_description`: the activation's role, its montage and its blocks'
  task types.
- `session_start_time` and `timestamps_reference_time`: the wall-clock time
  of session t = 0 (§4.2).
- `experimenter`: from the activation's job request.
- `subject`: `subject_id`, `species`, `sex`, `date_of_birth` (§9).

**`/intervals/blocks`** (`TimeIntervals`): `start_time`, `stop_time` (the
asserted boundaries, `core.Block`), `block_id`, `task_type`, `works_block_id`,
the measured boundaries (`trial.Block`) as `measured_start_time` and
`measured_stop_time`, and per-system coverage columns from `BlockCoverage`
(`coverage_{system}`, `covered_s_{system}`).

**`/intervals/trials`** (`nwbfile.trials`): `start_time`, `stop_time`,
`trial_id`, `outcome` (the trial type), `block_id`, `condition` where present,
and per-system coverage columns from `TrialCoverage`.

*Ruled while planning, 2026-09-28: never present today, so the trials table
has no `condition` column. The event stage stores a condition code on its
`CONDITION` event (`schema/events.py`), not on a trial, and `trial.Trial`
has none, so the code reaches the file as that event's `condition` in
`/intervals/task_events`. Tying a `CONDITION` event to a trial by time is new
meaning, left until a trial-level condition exists. Cost if wrong: a reader
joins the two by time.*

**`/events/task_events`** (`EventsTable`, NWB core since 2.9): one row per
decoded event code, `timestamp`, `event_type` (the `event.EventType` name),
and the attributes the event stage stores (`trial_id`, `block_id`,
`condition`). A meanings table gives each event type its description from
`contracts/events.py`.

*Amended while planning, 2026-09-28 (section 12.1 verified): Neurosift does
not display NWB 2.10's `EventsTable` -- its `Events` viewer handles only the
older `ndx-events` extension's types. So the events are
`/intervals/task_events`, a `TimeIntervals` with start equal to stop at each
event's time, which Neurosift draws on its timeline. `nwbinspector` flags a
stop that does not exceed its start as a best-practice violation, not a
critical one; the table's description says why. No meanings table: each
row's `event_type` is the code's own name.*

**`processing/timebase`**
- `timing_provenance`: one row, `TimingProvenance`'s tier and counts.
- `system_clocks`: one row per system, `SystemTimebase`'s fit (rate, drift,
  residuals, status).
- `segments`: one row per segment, `core.Segment`'s file, first native
  sample, offset and extent. With `system_clocks` this is enough to recover
  every native timestamp (parent spec §4.5).
- A `clock_reference` row: the reference time's source and the manifest's
  `started_at` beside it (§4.2).

**`processing/behavior`**
- `EyeTracking` with `SpatialSeries` `gaze_left` and `gaze_right`: degrees
  of visual angle in the calibrated frame, x rightward and y upward, `float32`.
  This is the glitch-repaired gaze every stored detection was made from
  (`schema/detect.py::_repaired_gaze`), NaN where the file has no finite
  value.
- `PupilTracking` with `TimeSeries` `pupil_left` and `pupil_right`: OpenIris's
  `PupilX`, `PupilY`, `PupilWidth`, `PupilHeight` and `PupilAngle`, in camera
  pixels and degrees, `float32`, uncalibrated. Nothing reads pupil yet; this is
  its first reader.
- The four series share one `timestamps` dataset (§4.3).
- `eye_calibration`: one row per eye, `EyeCalibration`'s source, model,
  coefficients, validation error and residuals.
- `eye_validity_left`, `eye_validity_right` (`TimeIntervals`): the stretches
  the mask withheld, with their label (`blink`, `invalid`).
- `eye_glitch_repairs_left`, `eye_glitch_repairs_right` (`TimeIntervals`):
  the stretches `glitch.py` repaired.

**`processing/eye_events`**
- One `TimeIntervals` per detector per trace, named `{detector}_{trace}`
  (`engbert_kliegl_left` … `bmd_conjunction`), 18 in all:
  - every run whose label is not a mask label (`blink`, `invalid`): saccade,
    microsaccade, pso, fixation, pursuit, drift;
  - columns `label`, and on saccadic rows `amplitude_deg`,
    `peak_velocity_deg_s`, `start_x_deg`, `start_y_deg`, `end_x_deg`,
    `end_y_deg`, `direction_deg`, `reliability`;
  - the table's description names the detector, its paramset index and its
    parameters.
- Six `{detector}_source` tables (`TimeIntervals`): `EyeDetection.Source`,
  which eye each stretch of the both-eyes trace came from.
- `detector_agreement` (`DynamicTable`): `DetectorAgreement`'s rows.

## 4. Time

### 4.1 One clock

**Every time in the file is session seconds: t = 0 at the sync box's first
decoded barcode** (`timebase/segments.py::session_reference`), the clock
every table already uses.

### 4.2 The wall-clock time of t = 0

**It comes from the barcode itself.** wl-sync's barcode value is whole
seconds since 2020-01-01 UTC off the sync box's wall clock
(`wl_sync/clock.py::BARCODE_EPOCH`, `value_from_clock`). So the first
barcode's value places t = 0 on the wall clock to within one second, and to
the sync box's PTP-disciplined accuracy once the lab time service exists.
- `session_start_time` = `timestamps_reference_time` = 2020-01-01 UTC + the
  first barcode's value, `reference_source = 'barcode'`.
- The manifest's `started_at` is recorded beside it, with the difference in
  `started_at_difference_s`.
- **Where the two disagree by more than 60 s**, the barcode is not trusted
  as a wall clock. The figure is borrowed from wl-sync's
  `CLOCK_TRUST_TOLERANCE_S`, how far its own checkpoint may lead the wall
  clock before it distrusts the clock; the two are different comparisons, so
  it is a reasonable bound, not a derived one. Then
  `started_at` is used, `reference_source = 'manifest'`, and the file says
  so in `clock_reference`.
- *wl-sync's segment header now carries `clock_trusted`, but the wl-sync
  commit this repository pins predates it. Reading it needs the pin moved,
  which is its own change; until then the 60 s rule stands in for it.*
- *Corrected while planning, 2026-09-28: the pinned wl-sync predates
  `wl_sync/clock.py` altogether, not only `clock_trusted` (the module arrived
  in wl-sync's commit `3ce66b9`, 2026-08-17). The epoch is restated in
  `nwb/gather.py` and pinned equal to wl-sync's by a test that runs wherever
  a newer wl-sync is installed. A synthetic session's barcodes are counters
  from 1,000,000 (`synth/timeline.py`), not seconds since 2020, so every
  synthetic file takes the manifest fallback -- which is what exercises it.*

### 4.3 The eye's sample times

**A new public function gives every ohDPI file row its session time**, from
the segment the timebase stored: `start_s + true_index * (end_s − start_s) /
n_samples`, where `true_index` is the row's true sample index (the file's
frame number minus its first, so a dropped frame leaves a gap rather than
shifting later rows).
- `core.Segment.end_s` is the time of sample `n_samples`, one past the last
  (`schema/core.py`: `scan.duration_s / rate.scale + offset_s`, with
  `duration_s = n_samples / fs_hz`).
- **`schema/eye.py::_session_time_to_row` is corrected to be its exact
  inverse.** It maps `end_s` to sample `n_samples − 1`, so it is off by up to
  one sample (2 ms), largest at the end of the file. Ruling (this spec's
  author): fix it in this piece, since the file writes times the calibration
  windows are read by. Cost if wrong: calibration windows move by at most one
  sample.
- **A session with more than one ohDPI segment is refused** (§10), as
  `EyeValidity` and `EyeDetection` already assume one.
- The eye series store explicit timestamps rather than a nominal rate: the
  ohDPI rate is measured, not exactly 500 Hz (498.55 Hz on the reference
  recording), and parent spec §8.1.1's "eye at 500 Hz is lossless" means no
  resampling.

### 4.4 PTP is never a timestamp here

**When devices carry PTP stamps, they appear only in `processing/timebase`,
as a column labelled a cross-check, and never as the timestamps of any data.**
wl-works' time-service design §4 names the hazard: a file carrying two
timestamps invites an analysis to read the plausible wrong one. The barcode
is authoritative wherever a shared-signal alignment exists; this piece writes
no PTP value, and the rule is recorded so the piece that first does keeps it.

## 5. Trimming to the activation

**Parent spec §8.1: each NWB is self-contained over its own activation's
block set.**
- **The block set:** a canonical activation covers every `core.Block` inside
  its montage (`core.Montage`); a derivative covers its `ActivationBlock`
  rows.
- **Continuous data** (gaze, pupil): only the samples whose time falls in
  one of the blocks. Gaps between non-adjacent blocks are real gaps in the
  timestamps.
- **Events, trials and detected runs:** kept when their start falls in a
  block. A run is never cut; one ending after its block keeps its true end.
  - *Ruled while planning, 2026-09-28: an EVENT is kept when it falls in a
    block or exactly on its end. An event is an instant, and the one on a
    block's end is that block's own `BLOCK_END` marker; half-open intervals
    are for samples, so adjacent blocks never share one. Measured on the
    synthetic session: the half-open rule dropped `BLOCK_END`.*
- **Validity and repair stretches:** clipped to the block edges.
  - *Ruled while planning, 2026-09-28: blocks that touch are one interval
    for trimming, so a stretch across the edge two blocks share stays one
    stretch. wl.works may assert two blocks where one was measured, and
    clipping at their shared edge would make one blink two rows. Which
    samples, runs and events fall inside is unchanged.*
- **Tables not tied to time** (calibration, clocks, segments, agreement): as
  stored, for the session.

## 6. Writing

- **Atomically:** written to `{path}.partial`, then renamed (parent spec
  §11.5). A failed build leaves no file under the final name.
- **Readable over HTTP range requests** (parent spec §8.1.2):
  - every dataset compressed on its own with gzip, which browser HDF5
    readers support; nothing whole-file;
  - continuous series chunked time-major, about 256 KB a chunk (for a
    two-column `float32` series, 32,768 samples);
  - `/units` is absent in this piece; §8.1.2's rule for its columns is piece
    3's.
- **Where:** `{nwb_root}/{session_id}/{identifier}.nwb`. `nwb_root` is a
  scratch location given to the stage and the command. Piece 2 publishes
  from there.
  - *Amended by the final review, 2026-09-29 (its C1):
    `{nwb_root}/{subject}/{session_id}/{identifier}.nwb`, for §3's reason.
    And a build whose path another activation's row already records is
    refused rather than replace that file: one subject's two rigs can number
    the same day's sessions alike.*
- **Size:** for a two-hour session, the eye series are about 250 MB before
  compression, most of it pupil; every table is small.

## 7. Checksums

**Parent spec §8.2, for wl.works Plan 24 §3.3.** Every dataset this piece
writes is written-once.
- **What is hashed:** each dataset's decoded contents, as `blake3` over its
  C-order bytes, with its dtype and shape. Never a group: `colnames` is an
  attribute that changes when a column is appended.
- **A ragged column is a pair:** a column and its `_index` are recorded
  together, so a change cannot hide in the half left unnamed.
- **Stored** in `NwbFile.Dataset`: `dataset_path`, `dtype`, `shape`,
  `blake3`, `paired_with`.
- **Stable:** rebuilding the same activation from the same inputs gives the
  same checksums. `identifier` and `built_at` are attributes, not hashed.

## 8. Validation

**The file is re-opened and `nwbinspector` runs on it** (parent spec §8.1:
"validated with `nwbinspector` before publication").
- All findings are stored in `inspector_findings`, with the count at
  `CRITICAL` importance in `n_critical`.
- **Any critical finding makes the status `invalid`.** The file is kept for
  inspection; piece 2 publishes only `written` files.
  - *Amended by the final review, 2026-09-29 (its I3): any finding at
    `CRITICAL` or above. nwbinspector ranks `PYNWB_VALIDATION` (the file
    fails the NWB schema) and `ERROR` (pynwb cannot read it) above
    `CRITICAL`, and `n_critical` counts all three. One exception: an `ERROR`
    that one of the inspector's own checks raised says nothing about the
    file -- nwbinspector 0.7's column checks raise on every empty table --
    so it does not block, and is stored with the rest.*
- The inspector's default configuration, not DANDI's. A DANDI export is a
  derivative (the `export` action), with its own requirements.

*Measured while planning, 2026-09-28, with `nwbinspector` 0.7.2 on these
files: two checks are critical under the default configuration.*
- *`check_subject_age`: a subject with neither age nor date of birth. So a
  file built without wl.works' subject details (section 9) is `invalid`, and
  piece 2 will not publish it: those details are required for publication in
  practice. Ruling (this spec's author): keep the rule rather than exempt the
  check, because an NWB file without the animal's age is a real gap. Cost if
  wrong: until wl.works sends the details, no file is publishable.*
- *`check_session_start_time_future_date`: a start in the future. Real
  sessions never are; the end-to-end test's synthetic session is dated in the
  past for this reason.*
- *Everything else it reports on these files is a suggestion or a
  best-practice violation: empty tables, perfectly regular timestamps on
  gap-free synthetic data, the zero-length events, non-standard module names.*

## 9. The subject's details, from wl.works

**The requester's decision:** wl.works' job request carries them.
- `contracts/protocol.py::MetadataBundle` gains an optional
  `subject_details`: `species` (Latin name, as element-animal's `Species`
  prefers for NWB), `sex` (`M`, `F`, `U`) and `date_of_birth`.
- `responder/jobs.py::accept` writes them into element-animal's own tables:
  `Subject.sex`, `Subject.subject_birth_date` and `Subject.Species`,
  replacing the stub `ingest/landing.py` creates.
- `docs/schemas/job_request.json` is re-exported; CI's schema check sees the
  change.
- The matching wl.works change is written up in
  `docs/pending-wl-works-amendments.md`, as earlier cross-repository changes
  were.
- **Absent, the file is still built,** with the subject's sex `U`, no
  species and no date of birth. The inspector's findings say so.

## 10. Refusals

**A refused activation gets an `NwbFile` row with `status = 'refused'` and a
reason, and no file.** A refusal is never retried automatically; its row
stays until someone deletes it (the house rule for a stage whose key would
otherwise be retried every pass).
- no `TimingProvenance` row, or tier D: no trustworthy session time;
- no blocks in the activation's block set;
- more than one ohDPI segment in the session (§4.3).

*Amended by the final review, 2026-09-29 (its I4): the stage and the command
first ask whether the activation is ready, and one that is not is skipped
with no row and tried again next pass. Ready means a `TimingProvenance` row
exists, and no key of the session is still outstanding in the computed
tables the file is built from (`BlockCoverage`, `TrialCoverage`,
`EyeCalibration`, `EyeValidity`, `EyeDetection`, `DetectorAgreement`), for
the paramsets the file reads: the default validity and detection ones. A
missing `TimingProvenance` row is therefore "not yet", not a refusal. A file
built earlier would have left an outstanding key out and been recorded as
final. `gather` still refuses a missing row if called directly. Rebuilding a
recorded file when its inputs later change -- an `invalid` row once wl.works
sends the date of birth, or any row after an upstream recompute -- is piece
2's lifecycle; until then the row is deleted by hand to rebuild. The command
also refuses a freed session, as the stage skips one (its I5).*

**One partial case:** with no computed `EyeCalibration` for an eye, that
eye's gaze, validity, repairs and detections are left out and the file is
still built. Its `session_description` says which eye is missing.

## 11. Testing

- **Each writer, on plain data:** the right NWB types, names, columns,
  units and descriptions.
- **The row-to-time function and `_session_time_to_row`:** exact inverses,
  on a recording with dropped frames.
- **End to end, on the synthetic stepped session** through the daemon:
  - the file reads back with `pynwb`;
  - its times equal the source tables' times;
  - trimming: a derivative over one block holds nothing from the others;
  - `nwbinspector` reports no critical finding;
  - `NwbFile.Dataset` lists every dataset, and a rebuild gives the same
    checksums.
- **Laziness:** the file is read through a file-like object that counts the
  bytes read, which is what HTTP range requests turn into. Reading one second
  of gaze must cost a small fraction of the file.
- **Each refusal** in §10, and the partial case.
- **The subject details:** a job request carrying them fills the subject's
  row, and the file's `subject` carries them.

## 12. What a plan must verify rather than assume

1. **Neurosift reads gzip-compressed datasets and the `/events` group** (NWB
   2.9's `EventsTable`). If it cannot display `EventsTable`, the events move
   to a `TimeIntervals`-shaped table.
2. **Which `nwbinspector` checks fire at `CRITICAL`** on this file, and
   whether a missing subject species is one of them under the default
   configuration.
3. **`pynwb` and `nwbinspector` on Python 3.13,** which CI also runs, and the
   versions CI resolves (a handoff records `pynwb` 4.2.0 there).
4. **element-animal's write path for species** (`Species` lookup and the
   `Subject.Species` part).
5. **Sharing one `timestamps` dataset across four series** writes as an HDF5
   link, and the inspector accepts it.

*Answered while planning, 2026-09-28:*
1. *Neurosift: gzip is HDF5's own filter, which its reader supports; the
   events are a `TimeIntervals` (section 3's amendment).*
2. *The critical checks: section 8's amendment.*
3. *Python 3.13: `pynwb` 4.2.0 and `nwbinspector` 0.7.2 install and build
   these files there; 3.11 resolves `pynwb` 4.1.0.*
4. *Species: element-animal's `Species` lookup, then its `Subject.Species`
   part, one species per subject, replaced when wl.works sends another.*
5. *Shared timestamps: written as an HDF5 link to one dataset; the inspector
   raises nothing about it.*

## 13. Out of scope

Everything in §1's "It is not" list, plus: moving the wl-sync pin to read
`clock_trusted`; the electrode table's intersection-or-union question for an
overlap derivative (Phase 2a spec open question 2); extracellular electrical
stimulation's NWB representation (parent spec §13 item 1).
