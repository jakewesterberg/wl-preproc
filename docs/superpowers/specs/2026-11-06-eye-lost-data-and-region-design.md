# Lost data and the screen region: a saccade carried across a short loss, and the mask at the recorded field

**The requester's answers of 2026-11-06**, to a question asked jointly with wl-xcon: when a saccade has one
missing sample in the middle, our offline detectors end it there, so a 12-sample, 8° saccade becomes two
saccades and a 2° saccade can become two microsaccades, while wl-xcon's online detector carries it across a
lost datagram, counted and marked. The answer: **the two agree, and wl-preproc changes.** A saccade is carried
across a short loss of data, counted and flagged; a sample that is present but invalid still ends it. With it,
wl-xcon's second point: the mask's screen region, a placeholder, is to come from each session's recorded display
setup.

**Status.**
- Chosen on 2026-11-06: one small eye-side piece, first of wl-xcon's asks (before C2a, C2b, C3, XC-399 and a
  setting's value that is an appearance).
- Branch `spec/eye-lost-and-region`, from `main` at `915a825` (wl-sync's header follow-up merged).
- Designed in three parts, each approved by the requester on 2026-11-06 (§0); the rulings made while writing it
  are marked **Ruling** where they appear, for the requester's review.

**Read for this spec,** each passage relied on opened at its source:
- This repository: `eye/detect/validity.py` (`validity_labels`, `ValidityParams`, `DEFAULT_VALIDITY_PARAMS`),
  `eye/detect/labels.py`, `eye/detect/registry.py` (`DetectFn`, `_within_usable`), `eye/detect/velocity.py`,
  `eye/detect/engbert_kliegl.py`, `eye/detect/measure.py`, `eye/detect/glitch.py`, the four ports and the two
  reimplementations, `eye/ohdpi.py`, `schema/detect.py` (`EyeValidity`, `EyeDetection`, `_insert_trace`,
  `_min_duration_samples`, `register_default_paramsets`), `events/rigsession.py`, `schema/core.py`
  (`SessionRecord`), `eye/detect/contract_fixtures.py` and `docs/fixtures/saccade/v2.json`; the specs
  `2026-08-31-saccade-detection-design.md` (§2, §3's amendment of 2026-09-28, §11 open question 1),
  `2026-10-11-blink-padding-design.md`, `2026-10-11-eye-loss-design.md` and
  `2026-10-30-true-angle-saccades-design.md`.
- wl-xcon: `wl_xcon/saccade.py` at `8e82a1f` and `624febd` (branch `saccade-rules-2026-11-04`, its detector
  VERSION 5, in review), `tests/test_saccade.py`, `docs/references/decisions.md`; `wl_xcon/taskd.py`
  (`Session._fixed_config`), `wl_xcon/geometry.py` and `tasks/rig.py` at `main` `957ec98`. Its answers of
  2026-11-06 (§12) were checked against that code.
- The groundwork: `~/.cache/wl-preproc-scratch/2026-11-06-eye-bridge-region/groundwork.md`.

---

## 0. The requester's decisions

Taken on 2026-11-06, in the question interface:
1. **The two detectors agree** (asked jointly with wl-xcon): both carry a saccade across a short loss of data,
   counted and flagged; a sample masked as invalid still ends it. wl-xcon's rule stands; ours changes.
2. **Lost** is a dropped camera frame, the tracker losing a reflection (`feature_lost`), a value that is not a
   number (`non_finite`), a sample faster than 1000°/s (`too_fast`), and a single-sample glitch we fill in.
   **Invalid** is a blink with its padding, and gaze off the screen.
3. **The screen region is the session's visible field plus 2°.** The field is recorded with each session.
4. **One eye-side piece, first of wl-xcon's asks,** with the bridging and the region together.
5. **The design, in three parts, approved one by one:**
   1. a lost stretch of up to 50 ms is offered to every detector, its missing positions filled by a straight
      line; a longer one is withheld and ends a run; then one shared rule: an event may span lost samples,
      never invalid ones, its ends are measured, only measured samples count toward its minimum length, and an
      event holding lost samples is flagged with their count;
   2. a flagged saccade's size, direction and endpoints from its measured ends, its peak speed from measured
      samples only, its duration from the frames' own times; **the main sequence and vigor fits leave flagged
      saccades out**, and the report counts them;
   3. the region from the recorded field plus 2°, the placeholder only for a session that recorded none; a
      `lost` label and a lost-sample count stored, a database declared before told its `ALTER TABLE`; a v3 of
      the saccade fixtures for wl-xcon.

Also answered that day, and unrelated to this piece: a gap in the sync box's records that swallows a whole run
fails a session only where it overlaps a measured run (the header follow-up's handoff, §6).

## 1. What happens today

- **One `available` array per eye** reaches all seven detectors: `None` where a detector may label, else the
  mask's `blink` or `invalid` (`EyeDetection.make` rebuilds it from the stored `EyeValidity.Run` rows). Lost
  and invalid are merged before anything is stored: `validity_labels` ORs every criterion into one `unusable`
  array, and only `blink` or `invalid` comes out; only the per-criterion fractions survive.
- **A dropped camera frame has no row.** The gaze trace is the file's rows; a gap sits between two rows, found
  by their frame numbers. The shared velocity, `(x[n+2] + x[n+1] − x[n−1] − x[n−2]) · fs/6`, treats rows as
  evenly spaced, so the four rows whose window spans the gap get wrong velocities; their positions are real.
  The mask withholds those four as `frame_gap`, and the margin (`dilate_samples`, 5 each side) grows them to
  **14 rows**: one dropped frame inside v2's 8° saccade leaves Engbert-Kliegl no saccade at all.
- **"An event never contains an unavailable sample" is enforced eight times:** each detector splits at
  withheld samples itself, and `registry._within_usable` then drops any run with a withheld stretch inside it
  (the requester's decision of 2026-09-28) and trims one with withheld samples at its ends.
- **The screen region is a placeholder:** `region_half_width_deg = 20`, `region_half_height_deg = 15`, "no
  measured basis on this rig" (the saccade-detection spec's §11, open question 1). On this rig the visible field
  is 30.54° × 18.41° in direct view and 12° × 12° through the stereoscope, so the placeholder withholds real
  field in the one and admits gaze 8° beyond it in the other.

## 2. Lost and invalid: the mask

Every sample the mask does not offer is either **lost** (no measurement, or none to trust) or **invalid** (a
measurement, of the wrong thing):

| Criterion | Kind | Notes |
|---|---|---|
| `frame_gap` | lost | the four rows whose velocity spans a dropped frame; **no margin** (§2.2) |
| `feature_lost` | lost | with its margin |
| `non_finite` | lost | with its margin |
| `too_fast` | lost | with its margin |
| a repaired glitch | lost | `glitch.repair_glitches` fills it in and today offers it as measured; it is now lost |
| `blink` | invalid | with its margin and its padding (50 ms before, 80 ms after) |
| `out_of_region` | invalid | with its margin; the region from the session (§7) |
| `short_epoch` | invalid | a measured stretch too short to analyse between two withheld ones (§2.3) |

1. **A margin takes the kind of what it grows.** Where a lost margin and an invalid sample meet, the sample is
   invalid: `MASK_PRECEDENCE` becomes `blink`, `invalid`, `lost`.
2. **Ruling: a dropped frame takes no margin.** The margin's stated reason is that "a tracking failure does not
   begin and end cleanly"; a dropped frame is not one, and its neighbours' positions are measurements. The
   blink-padding spec's line keeping the frame gap's margin (its §3) is amended. A dropped frame now costs four
   lost rows, not fourteen withheld ones.
3. **Ruling: a short epoch stays invalid.** It is measured data cut off by withheld data on both sides, which
   (§3) is now only invalid data or a lost stretch longer than 50 ms; it is computed after the bridging, so a
   short lost stretch no longer makes one.
4. `ValidityMask` carries a per-sample label of three values, and its per-criterion fractions are unchanged in
   meaning.

## 3. What the detectors are offered

- **A lost stretch** is a maximal run of lost samples. **It is short when the measured samples either side of it
  are at most `max_lost_ms` apart, 50 ms by default** (wl-xcon's `max_gap_s`, bounded since its `624febd` by
  the same measure: "from the last measured sample to the next"). **Ruling:** the times are the frames' own,
  from their frame numbers, and the bound is compared in whole frames, `floor(max_lost_ms · fs_hz / 1000 +
  1e-9)` of them (24 at 498.554 Hz; 25 at a fixture's 500 Hz), so no float rounding decides a boundary.
- **A short lost stretch is offered to every detector,** `available` marking it `lost`. Where its samples have
  no trustworthy position (`feature_lost`, `non_finite`, `too_fast`) the gaze offered is filled by the straight
  line between the measured samples either side, as a repaired glitch already is; a dropped frame's four rows
  keep their measured positions.
- **A longer lost stretch is withheld,** as unavailable samples are today, and ends a run.
- **Ruling:** the four ports (NSLR, REMoDNaV, U'n'Eye, BMD) and the two reimplementations (Otero-Millan,
  Nyström-Holmqvist) treat a sample marked `lost` as usable: they run unchanged on the trace offered, and the
  shared rule (§5) holds their runs to it. Their authors' code is not touched (true-angle spec, decision 1).

## 4. Engbert-Kliegl: the contract's detector

Engbert-Kliegl is ours, and the detector wl-xcon ports, so its rule is wl-xcon's exactly:
- **A lost sample's velocity is unknown.** It continues an open run and never opens one; only measured
  velocities count toward `min_duration_samples` (6). wl-xcon: "unknowns carry a run but never make one".
- **A short lost stretch carries an open run;** a longer one ends it at the last measured sample before it, the
  run kept if its measured velocities already make it.
- **The threshold scale** is computed over measured velocities only (lost ones excluded, as incomplete windows
  already are under true angle).
- **A run is flagged** when its detection window holds a lost sample, including one of the two velocities
  before its start (wl-xcon's `flagged`).
- BMD's gate is Engbert-Kliegl on the same `available`, so it follows.

## 5. The shared rule, after every detector

`registry._within_usable` becomes the one rule every detector's runs are held to (it replaces the 2026-09-28
guard, which it keeps for invalid data):
1. **A run may span lost samples, never withheld ones.** With withheld samples (invalid, or a long lost
   stretch) only at its ends it is trimmed back; with a withheld stretch inside it and offered samples both
   sides it is dropped, as today.
2. **Its ends are measured:** lost samples at either end are trimmed off, so its endpoints are measurements.
3. **Lost samples carry a run but never make one:** a run holding lost samples is kept only if its measured
   samples alone reach its detector's own minimum, `_min_duration_samples` (the detector's declared minimum,
   never less than 2; the requester's decision of 2026-09-28). A run with none is untouched.
4. **A run is flagged** when a lost sample lies inside it or in the two samples before its start, where its
   onset was decided (wl-xcon's `flagged`, §4), for every detector alike; the lost samples inside it are
   counted (§8).
5. A `pso` still goes with its saccade.

## 6. Measuring a flagged event, and where it is used

- **Size, direction, endpoints, eccentricity and polar angle** from its first and last samples, which §5.2 makes
  measured. A 2° saccade with a dropped frame inside is one 2° saccade, not two microsaccades.
- **Peak speed** is the largest speed among its measured samples only: a filled-in stretch's speed is not a
  measurement. Where the loss fell on the peak, the stored peak is low, and the flag says so.
- **Duration** runs from its first frame's time to its last's, by frame number, so a dropped frame inside counts
  its real time; the main sequence's `duration_ms` is computed the same way, not as rows over `fs_hz`.
- **Used:** a flagged saccade is detected, counted, stored and published with its count. **The main sequence and
  vigor fits leave it out** (the requester's decision), and the report says how many each fit left out.
- **Detector agreement and the two-eye conjunction** take flagged events as any other; a two-eye event is flagged
  when either eye's run is.

## 7. The screen region

- **The field** is `setup.half_field_deg` in the session's `xcon/config.json`, `[horizontal, vertical]` half-angles
  in per-axis degrees, already capped by the stereoscope's `mask_deg` (`wl_xcon/taskd.py::_fixed_config`,
  `geometry.py`; present since wl-xcon's `c634749`, 2026-09-29). It is the field a stimulus can be drawn in,
  in the same per-axis degrees as our calibrated gaze.
- **The region** is that field plus `region_margin_deg`, **2°** by default, on each axis: a sample is off the
  screen when `|x| > h + 2` or `|y| > v + 2`, per eye, as the mask tests today.
- **Read once, where the session's config already is:** `events/rigsession.py::read_rig_session` reads
  `config.json` for the session's kind and calibration; it reads `setup.half_field_deg` too, and
  `core.SessionRecord` stores it (two nullable columns). `EyeValidity` requires the event stage, so the row
  exists before any mask is computed.
- **Ruling: a session with no recorded field keeps the paramset's placeholder** (±20° × ±15°): the reference
  recording, whose validations depend on it; a wl-xcon session before 2026-09-29; a config naming another
  animal, which the reader already declines. `EyeValidity` records which region it used and whether it was the
  session's, and the daily report names each session on the placeholder.
- **Synthetic sessions record a field:** the synthesizer writes a `setup` as wl-xcon does, this rig's direct
  view by default (30.54° × 18.41°), so the fixtures meet the rule real sessions meet; a recipe can name another.
- **Ruling, for wl-xcon to confirm: through the stereoscope, each eye's gaze is compared with the field as
  recorded.** Each eye's image carries a vergence offset `atan(E / D)`, 1.45° on this rig, and whether our
  per-eye calibration puts gaze in its own viewport's frame or the cyclopean one is wl-xcon's calibration's to
  say (§12). If cyclopean, the 2° margin covers the offset with 0.55° to spare; if the offset must be applied,
  it is one more term here, not a redesign.
- `contracts/events.py`'s comment that the pipeline holds no screen geometry is amended: it holds the field, in
  degrees, and still no pixels or distances.

## 8. Storage and migration

- **The label vocabulary gains `lost`**, in `EyeValidity.Run` and `EyeDetection.Run` (`labels.py`: "adding an
  enum value later is a schema change, and the migration window closes January 2027"). In `EyeDetection.Run` a
  lost sample inside an event takes the event's label; one in no event is stored as a `lost` run.
- **`EyeDetection.Run` gains `n_lost`**, the lost samples inside the run, 0 for one measured throughout, and
  **`flagged`**, §5.4's flag (a lost sample in the two before its start flags a run that holds none).
- **`EyeValidity` records its region:** the half-width and half-height it used, and whether they were the
  session's field or the placeholder.
- **`core.SessionRecord` gains the field,** `half_field_h_deg` and `half_field_v_deg`, null where none was
  recorded.
- **The `eye_validity` paramset gains** `max_lost_ms` (50), `region_margin_deg` (2) and `region_from_setup`
  (true), each defaulting so a paramset stored before them loads unchanged and still names what it did. The
  new default is a new paramset beside the old (the blink-padding spec's §3).
- **A database declared before** refuses each changed stage and names its `ALTER TABLE`, each statement
  restoring the columns as DataJoint declares them (the header follow-up's pattern, its §8), and names the
  deletion of what the old default paramset computed (XC-398's amendment 5).

## 9. The contract: v3 of the saccade fixtures

- **v2 is never edited** (`wl.yaml`'s `saccade-detector-fixtures`). Its `about` states today's rule, "an
  unavailable one never is" in an event, so v3 does not merely add traces: it replaces that sentence.
- **v3's `available`** holds `null` where a detector may label, `"lost"`, or a mask label (`"blink"`,
  `"invalid"`). A lost sample's position is null where it has none.
- **v3's `about`** states §4's rule in the terms of the fixture: a lost sample's velocity is unknown; it
  continues an open run and never opens one or counts toward `min_duration_samples`; a run ends where a lost
  stretch from the last measured sample to the next passes `max_lost_ms`, inclusive; an invalid sample ends a
  run; a run is flagged when a lost sample lies inside it or in the two samples before its start.
- **v3 adds four traces** to v2's eleven: v2's 8° saccade with one lost sample inside (one flagged saccade);
  with a blink inside (it ends, and each piece is kept only if long enough); with a lost stretch longer than
  50 ms (it ends, the run so far kept); and a stretch of lost samples alone (no event).
- **wl-xcon checks these definitions before anything is built** (§12), and remaps its contract test, which
  maps every masked sample to `None` today, to v3.

## 10. NWB and the report

- **NWB:** each eye-events table gains `lost_samples` and `flagged` columns; the validity table's labels include `lost`
  (`MASK_LABELS`). A session's `eye_usable_fraction` stays the share of measured samples, lost ones not counted.
- **The daily report:** per eye, the share of lost samples beside the blink and invalid shares; per detector,
  the flagged saccades and microsaccades; per fit, how many flagged saccades it left out; and each session whose
  region was the placeholder.

## 11. Testing

- **The probe as a test, on all seven detectors:** v2's `centre-horizontal` 8° saccade, one frame dropped at
  each of its interior samples in turn, gives one flagged saccade of that size; without the bridging (§3) it gave
  none or two.
- **Engbert-Kliegl's rule** against v3, trace by trace, and its edges: a lost stretch of exactly the bound
  (carried) and one frame past it (ended, the run kept); lost samples alone (no run); a lost sample in the two
  velocities before a run's start (flagged).
- **The mask:** each criterion's kind; no margin on a frame gap; a lost margin meeting a blink (invalid); a
  short epoch computed after the bridging.
- **The shared rule:** a run across a lost stretch kept and flagged; across a blink dropped; lost ends trimmed;
  a run whose measured samples fall short of its detector's minimum dropped.
- **Measurement:** the peak from measured samples only; the duration across a dropped frame by frame time;
  the main sequence and vigor without flagged saccades.
- **The region:** read from a session's `setup`; the 2° margin on each axis; the placeholder for a session
  without one, named in the report; a synthetic session's recorded field.
- **Migration:** each changed stage on a database declared before refuses and names its statement, which
  restores the columns as DataJoint declares them.
- **The reference recording's validations** keep the placeholder, so their numbers move only by the bridging,
  and the seven-way agreement is re-measured.

## 12. Told to and asked of wl-xcon

Told on 2026-11-06: the requester's answer, and the definitions of lost and invalid. Its replies, checked against
its code:
- its run ends when a lost stretch passes `max_gap_s`, 50 ms, from the last measured sample to the next, the run
  so far kept if long enough by its measured velocities (`624febd`, VERSION 5, batch and online alike);
- its eye datagram carries no blink or validity field; what OpenIris sends when tracking fails is unverified
  until its V3 bench, so it can tell invalid apart only once that says what failure looks like;
- its flag is in memory only until its build T's saccade record (XC-427).

**Its questions, answered by this spec:**
1. *After an invalid sample, are its neighbours' velocities computed or unknown?* Neither is offered: the mask
   withholds an invalid sample's margin, 5 samples (10 ms) each side, and a blink's padding, 50 ms before and
   80 ms after, so no velocity whose window reaches it is used, and a run ends before it.
2. *Which index does a run end on?* Runs are half-open, `[start, stop)`; a run's first and last samples are
   measured (§5.2), and it stops at the first sample past its last measured one.
3. *Is the 2° margin per axis against the rectangle, or in true angle?* Per axis, against the rectangle
   `half_field_deg + 2`, in per-axis degrees, as `half_field_deg` is.
4. *Per eye or on the cyclopean gaze?* Per eye.
5. *Through the stereoscope, in which frame is each eye's field?* **Asked back:** in which frame does wl-xcon's
   calibration put each eye's gaze, its own viewport's or the cyclopean one (§7's ruling)?
6. *A tolerance at the boundary?* Ours compares whole frames (§3); for a clock built as `i / 500`, a comparison
   of `≤ max_gap_s + 1e-9` decides 25 samples the same way at every position.

## 13. Records

- The saccade-detection spec's §2 and §11 open question 1 (the region measured, by the session's field), the
  blink-padding spec's §3 (a frame gap's margin) and the eye-loss spec (`feature_lost` is lost, not invalid)
  each get a dated amendment pointing here.
- `HANDOVER-wl-expcontroller.md`: wl-preproc's answer, with v3.
- `wl.yaml`: `saccade-detector-fixtures` gains v3; `status`.
- A handoff, `docs/handoffs/2026-11-06-eye-lost-data-and-region.md`, and the checkpoint.

## 14. Not in this piece

- wl-xcon's own invalid input and its region test (its build, to these definitions).
- Re-deriving the speed ceiling (`max_speed_deg_s`, 1000), the other half of §11's open question 1.
- Rebuilding the gaze trace at true length, with a row for each dropped frame: considered and not chosen, since it
  moves every row index after a gap in stored data and the NWB files for the same result.

## Amendments, 2026-11-06, made while proving the plan

1. **The mask applies the bound** (§3): a lost stretch no run can be carried across, longer than `max_lost_ms` or
   without a measured sample on a side, is stored `invalid`, so every detector meets exactly what is stored:
   measured, `lost` (carried), or withheld. Its criteria's fractions still say what it was.
2. **The bound is the loss of data, from the last position measured to the next** (§3; wl-xcon's check of
   2026-11-06, its R10, which its detector already did): a dropped frame's neighbours have measured positions,
   though their velocities span it, so one dropped frame is `1 + n_missing` frames, not `5 + n_missing`. A lost
   reflection's, a value's that is not a number, an impossible speed's and a filled glitch's positions, and their
   margins', are not measurements. Each stretch of lost samples is judged whole, so two losses within two samples
   of each other are one stretch (wl-xcon's question of 2026-11-06). The two samples at each end of the
   recording, whose velocities are zero-filled, are no measured sample beside a stretch, so a loss beside them is
   withheld (both sessions, 2026-11-07). v3's `about` says both.
3. **A paramset stored before** names no bound, no margin and no setup, and loads with 0, 0 and false: it masks as
   it always did, every criterion withheld as `invalid` with its margin, a frame gap's included, at its own
   region (§8).
4. **What a detector is offered over a carried loss** (§3): every lost sample's position is filled along frame
   time by the straight line between the samples either side, except a row whose velocity spans a dropped frame,
   whose position is a measurement; the velocities are the filled gaze's (`validity.fill_lost`).
5. **No duration by frame time** (§6): a saccade with a dropped frame inside holds lost samples and is flagged, so
   every saccade a fit takes is evenly sampled and its rows are its duration.
6. **A short loss at a run's end is carried to the landing** (§4, §5.2; found by wl-xcon's review of its own
   detector on 2026-11-06, and agreed by both): trimming a run back to its last measured velocity cut a saccade
   that landed inside a short loss down to a fraction of its size, unflagged (an 8-degree saccade reported as
   0.45 degrees). Engbert-Kliegl carries a run through a carried loss, and where the first measured velocity
   after it is not over the threshold the run ends there, that sample its last, so its size reaches the
   landing. The other six detectors' runs keep their lost ends, and `measure.measure_event_run` measures such a
   run out to the measured samples beyond them. A run ends at its last measured velocity over the threshold only
   at a withheld stretch, a long loss among them, or the trace's end. An unknown velocity still never opens a run.
7. **The flag is judged on the run as stored** (§5.4; wl-xcon's question of 2026-11-06): a lost sample inside it
   or in the two samples before its start, clipped to the trace. A run ended by a withheld stretch is unflagged,
   as a run ending at a blink is, and the two zero-filled velocities at each edge are not lost. On the
   conjunction, either eye's lost samples count.
8. **The field is kept in a table of its own, `core.SessionSetup`** (§7, §8), not in columns of `SessionRecord`,
   so the event stage needs no `ALTER TABLE`. **The frame is cyclopean** (wl-xcon, 2026-11-06: each eye's map is
   fitted to the one cyclopean target, its stereoscope calibration task not yet built), so §7's ruling stands
   with no offset term.
9. **The NWB columns are `n_lost` and `flagged`**, the stored names (§10). **The report** gives each eye's lost
   share beside its blink and invalid shares, each detector's flagged saccades left out of its fit on its vigor
   line, and each eye masked at the placeholder.
10. **v3's `lost` is a sample with no position** (§9), as wl-xcon's detector represents a lost datagram: a
    velocity is unknown where its window holds one, and `contract_fixtures.detector_input` is the rule reading
    such a trace into the mask's terms. Its five traces: one lost sample inside v1's 8-degree step (one saccade,
    flagged, five unknown velocities); a short loss at its landing (carried to the landing, its size whole,
    flagged); a blink inside it (no run crosses it); 74 ms with no position inside it (the run ends before the
    loss, kept, unflagged); lost samples alone in fixation (no event). A test rebuilds v3 and compares it with
    the file, so the builder's every rule is held.

## Amendments, 2026-11-07, made in the final review

11. **The two-eye fallback counts lost data as missing** (§6; the review's C1). The plan had it count a lost
    sample as offered, so a one-eye event the other eye lost data during was no longer kept whole, and where the
    other eye's detector, running on filled gaze, found nothing, the event left the two-eye trace: 3.7% of
    Engbert-Kliegl's two-eye events on the reference recording's first ten minutes, by the reviewer's measure.
    The requester's rule of 2026-09-28 keeps such an event whole when the other eye's data was missing at any
    point during it, and lost data is missing data. Where both eyes lost data the two-eye trace stores `lost`.
    Re-measured on the whole recording, Nystrom-Holmqvist keeps 613 left and 202 right saccades whole (281 and
    177 before lost data): 535 of the left's because the right eye lost data in them and withheld none, mostly
    the repaired glitches the mask did not mark before (amendment 14), much as before the glitch repair of
    2026-09-28 (587 and 205). That trace's reference check asserted it `neither` wherever both masks are set; a
    kept run now carries across its own short loss, so the check asserts that outside the kept runs, that each
    kept run is marked by its own eye, and that none covers a sample its own eye withholds (all 1197 samples
    where the old assertion failed lie in 103 kept runs, each on its own eye's carried loss).
12. **A two-eye run is measured on its eye's own mask** (§6; the review's I1): on the eye measured throughout it,
    the left first, else on one that lost data in it but withheld none, so a lost sample's speed is never its
    peak. Measured on the two-eye trace's own fill, a flagged two-eye saccade's peak read up to 27% high.
13. **A saccade whose take-off a short loss hides is measured, and labelled, from the last measured sample before
    the loss** (§4, §6; the review's I2, the mirror of amendment 6). An unknown velocity still never opens a run,
    and the run's start stays its first measured velocity over the threshold, which on wl-xcon's rig is the onset
    its states are timed by; only the size, direction, start position and polar angle reach back. Agreed by
    wl-xcon on 2026-11-07 and built as its version 11 (`991191c`, on its branch saccade-rules-2026-11-04, under
    its review), which agrees with ours everywhere it checked but v3's blink trace, which its input cannot yet
    express (no mask, its invalid input being still to build); v3 gains a trace pinning it. On its detector,
    version 10 measured v1's 8-degree step with one sample lost at take-off at 6.551 degrees (5.802 with two,
    5.082 with three); nothing on its rig reads a saccade's size yet (its XC-400 and XC-427 will).
14. **A repaired glitch is lost on its own sample alone, with no margin** (§2), as the table says and wl-xcon read
    it (its R5): with the margin, one glitch inside a short saccade erased it.
15. **The daily report on a database declared before** names the statements in place of the eyes it cannot read
    at the placeholder, rather than raising.
16. **The reference-recording validations hand each detector what `EyeDetection.make` does** (§11; the review's
    I3): the mask with the repaired glitches lost, then the gaze over a carried loss filled and its velocity
    (`tests/eye/detect/_offered.py`). Re-measured with the recording set: the six reference-gated modules' 68
    tests pass, Nystrom-Holmqvist's both-eyes fallback keeping 613 left and 202 right saccades whole (amendment
    11).
