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
