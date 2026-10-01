# Probes and areas in every NWB file and its description

**The hardware-free slice of Phase 3's NWB export, piece 3.**

**Status.**
- Approved in conversation on 2026-09-30, section by section. The requester's
  decisions are in §0.
- **Parent spec:** `2026-08-12-wl-preproc-design.md` §8.1, whose electrode
  table has "probe geometry (ProbeInterface), per-electrode area".
- **The tables it fills:** `2026-08-22-phase-2a-ephys-schema-design.md` §3.
  They were declared by Phase 2a, and nothing has filled them yet.
- **The description:** `2026-09-29-nwb-publishing-design.md` §2. Its `probes`
  list has been empty since version 1.

**The rig and compute machine are still unavailable** (the requester,
2026-09-30). Nothing here needs them. Spike sorting, units, LFP and MUA, which
make up the rest of piece 3, do.

---

## 0. The requester's decisions, 2026-09-30

1. **Both areas, each labeled for what it is.**
   - The file and the description carry the **aim**: the target area, with
     its atlas and atlas level.
   - They also carry the **latest area assignment** wl.works has when the
     file is built, with its source (at the rig, histology, and so on).
   - A later assignment does not rewrite a built file. wl.works stays the
     authority, and a regenerated canonical picks the new area up.

   Declined: the aim alone; the assignment alone.
2. **The recording says which probe was used; wl.works says where it went.**
   - Serial, part number and active sites come from the recording's own
     settings (for SpikeGLX, the `.meta`).
   - wl.works' report for the same serial adds the insertion, the
     trajectory and the areas.

   Declined: trusting wl.works' probe list, with the recording used only for
   geometry.
3. **Every electrode carries its probe's area.**
   - This is NWB's usual convention.
   - The file says the area is per insertion, not depth-resolved.

   Declined: leaving electrode locations `unknown`.
4. **Fill Phase 2a's probe tables now.**
   - A daemon stage records each recorded probe.
   - `accept()` records what wl.works reports.
   - A pass links the two.
   - The builder reads the tables.
   - Spike sorting later needs exactly these tables.

   Declined: reading the `.meta` files at build time and leaving the tables
   empty.

**What wl.works holds about areas, read in its Plan 19 (its `main` at
`a28ee96`):**
- **The aim:** the insertion's `targetArea`, with `atlas` and `atlasLevel`,
  "a copy of the plan's target".
- **Assignments:** `insertion_area_assignment`, append-only, one area per
  row, per insertion. `source` is one of `histology`, `functional_mapping`,
  `waveform_depth`, `structural_imaging`, `at_rig` and `other`.
- **No per-channel area,** in its own words: "wl.works holds no per-channel
  area at all". The NWB is that detail's only home.

## 1. What it is, and what it is not

**It is:**
- recording each SpikeGLX probe from the session's own files (§2.1);
- recording what wl.works reports about each insertion (§2.2), and linking
  the two (§2.3);
- writing the probes, their electrodes and their areas into every NWB file
  (§3.1), and into its description (§3.2);
- the contract change and the wl.works amendment that bring the areas (§4).

**It is not:**
- **Per-depth or per-channel areas.** Nothing produces them yet; sorting or
  reconstruction will.
- **Spike sorting, units, LFP or MUA.** These are piece 3 proper, and need
  the compute machine.
- **Intan probe geometry.** An RHS header does not say which probe was
  attached (§5).
- **Rewriting a built file** when a later assignment arrives (decision 1).

## 2. The tables, and how they fill

### 2.1 From the recording: the `probes` stage

A new daemon stage, `probes`, runs on every landed session. For each SpikeGLX
segment's `.meta` it reads three things:

- **`imDatPrb_pn`, the part number.** It fills `ephys.ProbeType`, and its
  `Electrode` part rows from `ephys/geometry.py::electrode_rows`. A part
  number that `probeinterface` does not know is recorded with no electrode
  rows (§5).
- **`imDatPrb_sn`, the serial.** It fills `ephys.Probe`, which points at its
  `ProbeType`.
- **`~imroTbl`, the active sites.** It fills `ephys.ElectrodeConfig`,
  content-hashed on the electrode set as Phase 2a §3 specifies, with its
  `Electrode` part rows.

A new table records what each segment recorded, in the recording's own
words:

| `ephys.SegmentProbe` | |
|---|---|
| key | `-> core.Segment`, `probe_serial` |
| | `-> ElectrodeConfig` |

A segment whose `.meta` lacks a serial, or cannot be read, gets no row. The
stage reports it by segment, once per pass, until the file changes.

### 2.2 From wl.works: `accept()`

`accept()` records every `metadata.probes` entry of every job request. Two new
tables hold it:

| `ephys.InsertionReport` | what wl.works says about one insertion; the latest request wins, as with subject details |
|---|---|
| key | `-> pipeline.Session`, `insertion_number` |
| | `probe_serial` (plain text: the probe may not be recorded yet) |
| | `trajectory_id = null` |
| | `target_area = null`, `target_atlas = null`, `target_atlas_level = null` |

| `ephys.AreaAssignment` | each assignment wl.works reports, append-only, as its own table is |
|---|---|
| key | `-> pipeline.Session`, `insertion_number`, `asserted_at` |
| | `area`, `source` (wl.works' list) |

### 2.3 Linking

In the `probes` stage, for each report whose serial matches a probe recorded in
the same session (`SegmentProbe`), the stage writes the Phase 2a tables:
- `ProbeInsertion`: the insertion, its probe and its trajectory;
- `InsertionLocation`: the aim, when the report has one;
- `SegmentConfig`: for each segment that probe recorded.

**Arrival order does not matter.** A report can arrive before the recording is
read, or after it. A corrected report relinks on the next pass: a changed
trajectory or aim is updated, and a changed serial moves the link.

## 3. What the file and its description say

### 3.1 In the NWB file

A file's activation covers one montage. A montage has, by definition, no
probe move and no bank change (parent spec §8.3). So each probe has one
active-site map per file. For each probe the montage's segments recorded:

- **A device** named for the serial: `probe-<serial>`, with its part number.
- **An electrode group** per insertion, or per probe when wl.works reported
  no insertion.
  - Its `location` is the **area label**: the latest assignment if there is
    one, else the aim, else `unknown`.
  - Its description spells out both areas, for example *"target: V4d (CHARM,
    level 6); assigned at rig: V4d (2027-01-12)"*.
- **One row per active site in the electrode table.**
  - It carries the position in µm (NWB's `rel_x`, `rel_y`), the shank and the
    site number.
  - `location` is the group's area label.
  - Three more columns keep both areas visible on every row: `target_area`,
    `assigned_area` and `assigned_area_source`.
  - The table's description says the areas are per insertion, not
    depth-resolved.

### 3.2 In the description

`NwbDescription.schema_version` becomes **2**: `probes` gains a defined shape,
replacing `list[dict]`. Each entry holds:

| field | |
|---|---|
| `serial`, `probe_type` | from the recording, or from the report when nothing was recorded |
| `insertion_number`, `trajectory_id` | from the report, or null |
| `n_electrodes` | the active sites in this file, or 0 when there is no geometry |
| `target` | `{area, atlas, atlas_level}` or null |
| `assignment` | `{area, source, asserted_at}` or null |
| `area_from` | `assignment`, `target` or `unknown`: which one the label came from |

The file-level `notes` list says when:
- a recorded probe had no report;
- a report matched no recorded probe;
- a probe has no geometry, and why.

### 3.3 Readiness

A file waits until the `probes` stage has read every SpikeGLX segment in its
montage. This joins piece 1's readiness gate (`nwb/gather.py::readiness`),
which names what it waits on. The wl.works report arrives with the job request
that creates the activation, so it is normally there first. A file built
before a later assignment keeps what it had (decision 1).

## 4. The contract, and what wl.works must do

**`contracts/protocol.py::ProbeEntry` gains two optional fields.** Requests
without them stay valid.

| field | |
|---|---|
| `target` | `{area: str (≤32), atlas: str (≤32), atlas_level: int}`, or absent: all three or none |
| `area_assignment` | `{area: str (≤32), source: one of wl.works' six, asserted_at: datetime}`, or absent: the latest assignment for the insertion |

The fields are regenerated into `docs/schemas/job_request.json`, and
documented in `docs/ops/lab-host-protocol.md`.

**A new OPEN entry in `docs/pending-wl-works-amendments.md`:** send each
insertion's aim (`targetArea`, `atlas`, `atlasLevel`) and its latest
`insertion_area_assignment` in every job request's `metadata.probes`. It
flags one thing read but not confirmed there: Plan 19's assignment row lists no
atlas column, so an assigned area arrives without one.

## 5. Failure cases

Every case below is written into the file and the description, and only the
last refuses the build:

| Case | What happens |
|---|---|
| A report's serial matches no recorded probe (a typo, a swapped probe) | noted; the recorded probe is listed with area `unknown` |
| A recorded probe has no report | listed from the recording, area `unknown`, noted |
| An unknown part number | the probe is listed with its type, no electrodes, noted |
| A `.meta` without a serial, or unreadable | the stage reports the segment; the file notes the segment's probe as unknown |
| An Intan (RHS) probe | listed from wl.works' report alone, no electrodes, noted: an RHS header does not name the probe |
| **One probe with two different active-site maps inside one montage** | **the build is refused**, naming the segments. A bank change should have started a new montage (parent spec §8.3), so the montage definition is wrong; a file built across it would later be sorted across it. |

## 6. What a plan must verify rather than assume

1. **The `.meta` keys** `imDatPrb_sn`, `imDatPrb_pn` and `~imroTbl`, across
   Neuropixels 1.0 and 2.0 formats. Also how `~imroTbl` maps to site numbers
   for each probe type. `probeinterface.read_spikeglx` is the format oracle
   in tests only: it is a dev dependency (`timebase/_nidq_meta.py`).
2. **How `core.Segment` rows correspond to SpikeGLX runs and their `.meta`
   files,** including multi-probe runs (`imec0`, `imec1`).
3. **Phase 2a's content hash** for `ElectrodeConfig`: read its §3, don't
   restate it.
4. **pynwb's electrode table API** (`add_electrode`, `rel_x`/`rel_y`, custom
   columns), measured on the pinned version.
5. **Where the readiness gate reads the `probes` stage's completion,** so a
   session with no SpikeGLX segments is ready at once.
6. **Whether any existing test fixture or table** already writes `ProbeType`
   or `Probe`, which would collide.

## 7. Testing

- **The synthetic generator** writes `imDatPrb_sn` and a partial bank
  selection (fix the fixture, not the spec). One variant has two probes, and
  one has a bank change inside a montage.
- **Reading the `.meta`:** unit tests, with `probeinterface` as the format
  oracle.
- **The `probes` stage, against the database:**
  - the census;
  - reports and assignments;
  - linking, in both arrival orders;
  - relinking after a corrected report.
- **The file:** its devices, electrode groups and electrode table, the area
  label and both area columns, for each of `assignment`, `target` and
  `unknown`.
- **The description:** version 2, its exported JSON Schema, and each note
  case.
- **The refusal,** and the readiness wait.
- **Over real HTTP:** a job request carrying `target` and `area_assignment`,
  and one refused for a partial `target`.
- **The full suite runs once, on both interpreters.**

## Amendments, 2026-09-30, made while proving the plan

The plan (`plans/2026-09-30-nwb-probes.md`) was proven in code before it was written, and
these settle what §0's decisions left open or correct what the code found. §0 stands.

1. **`SegmentProbe` is `ephys.ProbeCensus.Probe`** (§2.1). A master row per SpikeGLX
   segment marks its run read, with no probe as readily as with two, and readiness waits on
   it. Each part row is one imec stream (`imec0`, …): its serial, part number, electrode
   configuration (null when its sites cannot be placed) and `problem`.
2. **What a `.meta` cannot say is recorded once, not reported every pass** (§2.1, §5). A
   landed file does not change. So the part row names what is missing in `problem`: a
   serial, a part number, a type `probeinterface` does not know, an imroTbl it cannot map,
   or a file it cannot read. The file's notes carry it. A probe whose sites cannot be placed
   gets no `ProbeType` (Phase 2a's invariant: no type without its electrodes), no `Probe` and
   no configuration. It is listed with its part number and no electrodes.
3. **A serial is one physical probe.** A `.meta` naming a serial already registered as
   another type records a problem, and its sites are recorded under neither type.
4. **A serial reported for two insertions of one session is not joined** (§2.3). That is a
   moved probe, and the request does not say which segments each insertion covers. Neither
   is linked, the file lists the probe with area `unknown` and notes why, and the wl.works
   entry flags the case.
5. **An insertion that a later request does not mention is left as it was** (§2.2), since a
   request may name only its own montage's insertions. For an insertion it does mention,
   the latest request wins entirely, including a field it now leaves out. **The linker
   takes back only links it could have made** (§2.3): reports are never deleted, so every
   insertion it linked has one, and a `ProbeInsertion` with no report was made some other
   way and is left alone. The full suite found this: a test's synthetic insertion, with a
   sort pointing at it, was being taken back on every pass.
6. **A file waits for the census of every SpikeGLX segment in its session** (§3.3), not only
   its montage's: a segment not yet read has no extent to place it by.
7. **The builder reads `ProbeCensus`, `InsertionReport` and `AreaAssignment`** (§0
   decision 4), not the linked `ProbeInsertion`, `InsertionLocation` and `SegmentConfig`.
   One path covers a joined probe and one that cannot be joined; the linked tables hold the
   same facts, joined for sorting.
8. **pynwb fixes the electrode table's own description** (§3.1): NWB's `ElectrodesTable`,
   measured on pynwb 4.1. That areas are per insertion and not depth-resolved is said in
   each area column's description and each group's instead. A device carries the serial as
   `serial_number` and the part number as its `DeviceModel`.
9. **`probeinterface` is a runtime dependency, not a test-only oracle** (§6 item 1):
   `pyproject.toml` declares it, and the census maps each imroTbl with its `read_spikeglx`.
   `wl.yaml` did not declare it, and now does.
10. **Both SpikeGLX layouts are read** (§2.1): the generator's flat names and SpikeGLX's own
    per-probe folders. The rig's real layout has not been seen.
11. **The generator's restart writes its second run under SpikeGLX's own names**,
    `<session_id>_g1_t0.nidq.bin` and `<session_id>_g1_t0.imec<N>.<band>.bin` (§7). Under
    flat names, spikeinterface's reader put both runs in segment 0 and refused the folder;
    under these it reads two segments.
12. **The timebase fitted a restarted system wrongly; fixed here** (found by §7's restart).
    Each recording's barcodes are timed from its own first sample, so a restarted run counts
    from zero again. Pooled under one intercept, a synthetic 15 s session restarted at 9 s
    fitted −951,278 ppm and fell to tier D. `timebase/fit.py::fit_rate_across` fits one rate
    with an intercept per segment, as parent spec §4.5 describes. One segment fits exactly as
    before.
13. **A restart across task codes costs the timing tier. Recorded, not changed.** The NI
    record lacks the codes sent while SpikeGLX was stopped, and `events/agreement.py`'s
    0.999 threshold reads them as disagreement: a 0.5 s gap across CI_RECIPE's block
    boundary gave agreement 0.75 and tier D. A bank change on the rig pauses the task, so
    the fixtures restart mid-trial. If the lab ever restarts SpikeGLX with the task running,
    the agreement should compare only the stretches the NI recorded (handoff §3).
14. **A file's probes come from the segments its own blocks overlap, not from its whole
    montage** (§3.1, §5; the final review's I1). With the montage window, a derivative on one
    side of a bank change inside wl.works' montage was refused, permanently. Yet that
    derivative is parent spec §8.3's remedy for exactly that case. A file whose blocks cross
    the change is still refused, as §5 requires.
15. **The reader records anything `probeinterface` raises as a problem**, and records a site
    table shorter than the file's AP channels as one too (amendment 2; the final review's
    M1). A table holding only its header raised `TypeError`, which parked the segment's
    census job until it was cleared by hand. A table cut mid-entry passed as a map of fewer
    sites.
