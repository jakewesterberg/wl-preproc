# wl-preproc — Automated Post-Session Preprocessing Pipeline

**Status:** Design spec, awaiting review
**Date:** 2026-08-12
**Context:** Lab launches January 2027. Pipeline to be built and validated against synthetic data before any real recordings exist.

---

## 1. Purpose and scope

An automated pipeline that runs after every session in an NHP systems neuroscience lab and produces analysis-ready NWB plus a verified permanent archive of raw data.

**In scope:**

- Behavior-only training sessions: event codes, trials, eye tracking, behavior video
- Recording sessions: Neuropixels (SpikeGLX), Intan RHS (stim/record), or both
- Cross-device time alignment, trial parsing, event decoding
- LFP, MUA envelope, spike sorting with QC
- Eye calibration and saccade/microsaccade detection
- NWB assembly, validation, publication to the analysis array
- Lossless compression, verification, and tiered archival of raw data

**Out of scope:** analysis, figure generation, behavioral-task authoring, the behavior-camera acquisition system (built separately — see §4.6 for the contract it must satisfy).

### 1.0 wl-preproc has two roles, and the second was found late

`wl-works`' `docs/ops/waiting-on.md` records what nothing here had: wl-preproc is **"the lab's auto-pipelines runner"**, and **five separate wl.works stages dispatch to this one box**.

| Role | What it is |
|---|---|
| **A — autonomous pipeline** | Everything else in this spec: post-session ingest → NWB → archive, self-triggered, no human in the loop |
| **B — dispatch host** | A responder publishing its own action list, running jobs wl.works requests: Plan 18's 18b (behavioural analysis), Plan 20's 20b (spike sorting), Plan 24's 24b (trim-and-export), row 27's 27b (`@animal_warper` atlas registration), row 29's 29b (stimulus calibration) |

Role B is not later work. Three of those five stages are the mechanism by which role A's own outputs are re-run with different parameters, so a design that builds only role A cannot be re-run at all. **Both roles share one job runner** (§11.3): two runners each concluding the machine is free is the defect Plan 22 §2.3 refused for VRAM allocation.

Consequences carried through this spec: a network responder (§11), outbound egress to GitHub to pull `wl-bhveval` and report resolved commit SHAs, and the ability to run tools with nothing to do with ephys.

### 1.1 Guiding principles

1. **The sync box is the master clock.** Every recording system is an optional subscriber. Device topology is *data*, not code paths — a training session is the same pipeline with fewer rows.
2. **One imperative component per entry point.** The ingest watcher (role A) and the responder (role B) are the only non-`populate()` code, and both do the same thing: insert a Manual-tier row. Manual reprocessing is never a second execution path.
3. **Nothing irreplaceable is deleted without verification.** Raw data survives compression roundtrip checks, checksum verification, and confirmed archival before scratch is reclaimed.
4. **Coverage is explicit.** Which trials each system actually recorded is queryable data, never inferred by an analyst.
5. **Timing confidence is recorded, not assumed.** Every session carries a provenance record and a derived tier.
6. **Supersede, never overwrite.** A regenerated canonical NWB is a new artifact superseding the old, which stays readable (§8.3). wl.works' own rule — nothing is destroyed silently — reaches this machine.
7. **wl.works is the control plane; wl-preproc is the compute plane.** Block quality judgments, activations and dataset selections are wl.works'. This machine executes and reports; it renders no verdicts.

---

## 2. Decisions register

| Area | Decision |
|---|---|
| Runtime | Python-first: SpikeInterface, Kilosort4, NeuroConv/pynwb |
| Orchestration | DataJoint + DataJoint Elements, with explicit newcomer guardrails |
| Compute | 16-core AM4, 128 GB RAM, Quadro P6000 (24 GB), ≥4 TB NVMe scratch, ≥10 GbE |
| Sync master | Sync box (Raspberry Pi 4 + `pigpio` DMA), one per rig, present at every session |
| Barcode | 32-bit monotonic counter, 5 ms bits, 200 ms frame, **1 Hz** |
| Event codes | **16-bit** parallel + strobe |
| Code routing | Full 16 bits to Pi and NI; **strobe only** to Intan RHS |
| Trial truth | Codes own timing; task file owns parameters; cross-validated, hard-fail on mismatch |
| NI hardware | **PXIe-6363** (32 waveform DI) — *not* the 6341 (8 DI) |
| Intan | RHS stim/record + I/O expander (16 DI / 16 DO / 8 AI / 8 AO) |
| Eye | ohDPI (OpenIris, 2× FLIR BFS-U3-16S2M-CS @ 500 Hz), Pi-triggered |
| Eye detection | **Both** Engbert–Kliegl and U'n'Eye, always, as separate paramset-keyed rows |
| Retention | Lossless, permanent, keep everything |
| Session key | `(subject, session_datetime)` — Elements standard; datetime sourced from the behavioral control system |
| Segment key | First barcode value in the segment (uint32) |
| Control plane | wl.works. It owns blocks, quality verdicts, activations and dataset selection |
| Transport | **Pull, not push.** wl.works polls this host; wl-preproc never opens the connection |
| Concurrency | **Queue, never refuse.** Dedupe on `(selection, task type)`; one job runner holds the machine |
| NWB grain | **One NWB per activation.** One canonical per session, plus derivatives |
| Canonical regeneration | New activation superseding the old; the superseded NWB stays readable |
| Archive grain | **One compressed artifact per session** — not per segment, not per block |
| Continuous data rate | **500 Hz, uniformly** — LFP, MUA, eye, pupil. Timing-critical signals stored as event times instead |
| "Checked good" gate | Claimed by this pipeline; gates **scratch reclamation only**, never compression or archival |
| Channel map | wl.works **derives** it; wl-preproc **verifies** it against the recorded `.meta` and disagrees out loud |

---

## 3. System architecture

### 3.1 Components

| # | Component | Runs on | Role |
|---|---|---|---|
| 1 | Sync box | Pi 4, one per rig | Barcode generation, camera triggers, event-code capture, local buffer, push to server |
| 2 | Acquisition systems | Rig PCs | SpikeGLX, Intan RHX, task PC, OpenIris DPI, FLIR behavior cameras |
| 3 | Ingest watcher | Server | Detects session-complete, validates manifest, discovers topology, inserts Manual-tier rows |
| 4 | **Responder** | Server | HTTP endpoint wl.works polls. Publishes the action list, accepts requests carrying an idempotency key, inserts Manual-tier rows. **Never opens a connection outward** (§11) |
| 5 | Populate daemon | Server | **The single job runner.** All computation via `populate()`, whatever inserted the request |
| 6 | MySQL + external stores | Server + NAS | Metadata and provenance; blobs on NAS |
| 7 | Archive mover | Server | Compress, verify roundtrip, checksum, transfer to cold, record location, **write the completion sentinel** |
| 8 | NWB publisher | Server | Assemble, validate with `nwbinspector`, checksum written-once columns, publish to the analysis array |

**Components 3 and 4 are the same shape.** Both are imperative, both validate and insert a Manual-tier row, and neither computes anything. That is what stops roles A and B from becoming two pipelines with two sets of bugs.

### 3.2 Data flow

```
Rig acquisition (local disk)
        │  10/100 GbE
        ▼
NVMe scratch (T0, ≥4 TB)  ──►  processing ──►  NWB ──► analysis array
        │
        ├──► compress (lossless) ──► roundtrip verify ──► checksum
        │            │
        │            ├──► SATA HDD warm pool (T1, pinned cache)
        │            ├──► NAS (T2, permanent)
        │            └──► institutional cold (T3, permanent)
        │
        └──► scratch reclaimed only after archive verified
```

Rig transfers land **directly on server scratch**, not via the NAS. The NAS never handles hot uncompressed data.

### 3.3 Storage tiers

| Tier | Medium | Contents | Lifetime |
|---|---|---|---|
| T0 | NVMe ≥4 TB | Active processing | Reaped after archive verified |
| T1 | SATA HDD | Pinned compressed raw (cache of archive artifact) | Auto-pin 60 days; manual pin indefinite |
| T2 | NAS | NWB, derived products, compressed raw | Permanent |
| T3 | Institutional cold | Compressed raw | Permanent |

**Sizing:** a 2 h dual-probe NP session is ~360 GB raw, ~180 GB compressed, and occupies ~700–800 GB of scratch during processing (raw + sorter's preprocessed copy + temporaries). 4 TB scratch is ~5 sessions deep. A 20 TB warm pool holds ~110 sessions. At two sessions/week dual-probe, budget **15–20 TB/year** of permanent compressed storage.

**T1 is a cache, not a fourth format.** It stores the identical verified artifact that goes to cold storage, so rehydration for reprocessing is "decompress to scratch" — the same code path as a cold fetch, minus the slow retrieval.

### 3.4 Repository layout

```
wl_preproc/
  schemas/   DataJoint schemas: lab, subject, session, sync, event, ephys, eye, video, stim
  ingest/    watcher, manifest validation, device discovery, session-complete detection
  sync/      barcode decode, timebase construction, coverage model, provenance metrics
  events/    code decoding, trial tables, task-file adapters
  ephys/     spikeinterface wrappers, artifact removal, lfp, mua, kilosort, qc
  eye/       ohDPI reader, calibration, detection (Engbert–Kliegl, U'n'Eye)
  export/    nwb assembly + validation
  archive/   compression, roundtrip verification, checksums, tiered transfer
  cli/       wlpp commands
firmware/
  syncbox/   pi 4 pigpio service, config, session log format
hardware/
  breakout/  distribution PCB: buffers, level shifters, optoisolators
tests/
  synth/     synthetic session generator
  integration/
docs/
```

### 3.5 Frozen interfaces

These are the actual pre-January deliverable. Everything else is replaceable; these are not:

1. Session directory layout
2. `session_manifest.yaml` schema
3. Sync box log format
4. Event code protocol (§4.2)
5. Behavior-camera sidecar schema (§4.6) — **the contract the separate camera project builds against**
6. NWB target layout (§8)
7. **The wl.works↔wl-preproc protocol** (§11.2) — wl.works' own notes say it is better written before the hardware exists, and its 18b contract tests run against a *fake wl-preproc*, which is impossible without it

---

## 4. Sync, timebase, and events

### 4.1 Barcode

| Parameter | Value |
|---|---|
| Payload | 32-bit monotonic counter, persisted across reboots, never reset |
| Bit slot | 5 ms (150 samples @ 30 kHz) |
| Wrapper pulse | 10 ms — unambiguous against a 5 ms bit slot |
| Frame duration | ~200 ms |
| Interval | 1 s |
| Duty cycle | 20% |

A 32-bit counter at 1 Hz remains globally unique for ~136 years, making cross-session mis-alignment structurally impossible.

**Alignment guarantees** (worst-case window placement):

| Segment length | Guarantee | Handling |
|---|---|---|
| ≥ 2.2 s | ≥2 complete barcodes | Local offset fit + local rate verification |
| ≥ 1.2 s | ≥1 complete barcode | Offset from barcode, rate inherited from device-level fit |
| < 1.2 s | May contain zero | **Unalignable** → `RejectedSegment`, excluded, flagged |

Trials can be as short as 3 s, so a single-trial segment always clears the two-barcode bar.

**Barcode consumers are only 30 kHz samplers** (NI, Intan). Cameras never decode barcodes — the Pi triggers them, so frame times are known by construction. This is what permits short bit slots.

### 4.2 Event code protocol

16-bit parallel + strobe, generated by the behavioral control software (MonkeyLogic or custom).

**Routing:**

| Destination | Lines | Rationale |
|---|---|---|
| Sync box (Pi) | 16 data + strobe | Sole recorder on training sessions |
| NI (PXIe-6363) | 16 data + strobe | 32 waveform DI available |
| Intan RHS | strobe only | 16 DI ceiling cannot fit 16 + strobe + barcode |

**The rule that makes strobe-only safe:** *every session type must have at least one device recording full code content.* Intan may be strobe-only because the Pi is always present. This does **not** generalize back to the Pi, which is the sole recorder on training days.

**Standalone Intan sessions** (a satellite rig with no PXIe chassis) remain recoverable: Pi 16-bit codes at 5 µs + Intan strobe timing at 30 kHz as an independent check + task file for parameters.

**Protocol requirements:**

1. Every trial start is followed by an explicit **trial-number payload**. Trial matching is by ID, never by ordinal position — one dropped code must not shift every subsequent trial.
2. Every **block start** emits a block marker, and blocks are the unit wl.works asserts quality against (§5.2.1). Block boundaries decoded here are cross-validated against wl.works' `animal_session_block` rows.
3. Multi-word payloads carry a **checksum word**.
4. Strobe timing: data stable ≥0.5 ms before and after a ≥1 ms strobe (≈30 samples of margin each side at 30 kHz).
5. MonkeyLogic's digital output is software-timed; the *recorded* strobe edge establishes true event time, so ML scheduling jitter is measured rather than inherited.

**Two acquisition-provenance stamps, per block.** wl.works row 29 requires `animal_session_block` to carry `acquisitionBuildId` and `stimulusCalibrationId`, both **stamped by the task itself** rather than by a person or the rig machine. Nothing in the protocol above carries them, so they must reach the pipeline via the session manifest or the task file, and from there into `Block` and the NWB.

- `acquisitionBuildId` **is** a content hash of a free-text `{component: version}` set, deliberately assuming no git — so `{matlab, psychtoolbox, wl-bhvtask}` and `{bonsai, workflow}` are the same shape. The behavioural stack is unchosen and the design must not presume a resolvable commit.
- `stimulusCalibrationId` disambiguates **which rig's config** was loaded, since the task picks one at startup and the same build on two rigs reads different numbers.
- Both repeat across blocks within a rig day, and that redundancy is deliberate: two blocks cannot differ in calibration but *can* differ in task code, and hoisting calibration to the session would make "all blocks agree" a cross-row condition.

**Reserved ranges** — to be finalized against the first task implementations:

| Range | Purpose |
|---|---|
| 1–255 | Session/block markers, trial outcomes |
| 256–4095 | Task events |
| 4096–32767 | Task-specific / condition encoding |
| 32768+ | Payload words and escape codes |

### 4.3 Sync box (Pi 4)

**Hardware:** Raspberry Pi 4, 2 GB. **Not Pi 5** — `pigpio` drives BCM2711-and-earlier DMA/GPIO peripherals directly and does not support the Pi 5's RP1 southbridge. Pi 4 also has Gigabit Ethernet.

**Timing approach:** DMA-driven GPIO, never interrupt handlers. `pigpio` samples GPIO via DMA at a fixed 1–5 µs rate and generates waveforms the same way, bypassing the Linux scheduler entirely. Deterministic ~5 µs timestamps, well under a 30 kHz sample period.

> **HARDWARE WARNING: Pi GPIO is 3.3 V and is NOT 5 V tolerant.** Rig TTL is typically 5 V. Direct connection destroys the Pi. Every input requires level shifting; every output driving 5 V equipment requires buffering. A `74HCT541` handles both directions in one part (3.3 V reads as valid logic high on a 5 V rail) and provides the fan-out for driving NI + Intan + camera GPIO from one barcode line.

**GPIO allocation** (~25 of 28 usable):

| Direction | Lines | Signal |
|---|---|---|
| Out | 1 | Barcode (buffered fan-out) |
| Out | 1 | ohDPI camera trigger, 500 Hz |
| Out | 1 | Behavior camera trigger |
| In | 16 | Event code data |
| In | 1 | Event code strobe |
| In | 2 | Camera exposure-active returns |
| In | 1 | Photodiode (comparator output) |
| In | 2 | Reward, lick |

**Line allocation rule:** a signal needs a Pi input only if it can occur on a session type where the Pi is the sole recorder. Stim triggers therefore go to NI and RHS only — stim occurs exclusively on recording sessions.

**Photodiode** is analog into Intan/NI (full waveform, rise time, intensity) *and* comparator-digital to the Pi (so training sessions retain stimulus-onset ground truth).

**Known constraint:** 16-bit codes consume the Pi's headroom. Escape hatches if more inputs are later needed: a second Pi, or dropping camera exposure returns (partly redundant with FLIR frame-ID metadata).

**Residual risk:** `pigpio` is unmaintained upstream would pin the lab to Pi 4 hardware. Years-out concern with an obvious escape hatch; noted, not mitigated.

### 4.4 Breakout PCB

The task PC emits 17 lines fanning to Pi (17), NI (17), and Intan (1). Building this as one small board rather than per-rig hand wiring makes rigs reproducible.

Contents: `74HCT541` buffers/level shifters, optoisolators on ephys-bound lines, IDC in, BNC/IDC out.

**Optoisolation:** ground loops between rig equipment are a genuine ephys noise source. Isolate ephys-bound lines; leave the rest direct. Propagation delay is constant and calibrates out.

**Barcode routing caution:** 20% duty cycle means more TTL edges near headstages. Route the barcode line away from headstage cables. QC includes a **barcode-locked artifact check** so coupling is caught by the pipeline rather than by a reviewer.

### 4.5 Timebase construction

Session time is **t = 0 at the sync box's first barcode**, on every session type. One code path.

**Critical: separate the clock model from the segment model.**

- **Rate** is fitted once per `(system, session)`, pooling barcodes across *all* of that system's segments. A full session fits rate to well under 1 ppm.
- **Offset** is fitted per `(system, segment)` from that segment's own barcodes.

A 3 s segment therefore never estimates its own rate — it inherits a session-wide rate and establishes only its position. Residual over 3 s is a few microseconds. Fitting rate locally from two barcodes spanning ~2 s would yield ~16 ppm, *worse* than inheriting.

This also yields a free integrity check: a segment whose local barcodes disagree with the device-level rate indicates a mis-assigned file or a device clock reset, surfacing as a QC failure rather than a silent millisecond error.

**SpikeGLX handles imec↔NI sync internally** using its own mechanism. The barcode aligns SpikeGLX-as-a-whole to session time via one NI digital line. Fewer moving parts; the imec SMA stays free.

All data is written to NWB in session time, with fit parameters, residuals, and native stream timestamps retained so every transform is reversible and auditable.

### 4.6 Coverage model

"Not all systems have all trials" is a data model, not a sync problem.

- The Pi is continuous and always present, so it defines the session extent and the **canonical trial list**.
- Each system's segments define an interval set; intersected with trial intervals this yields, per trial per system: **`full` / `partial` / `absent`**.
- **`partial` is the important state.** A recording that stopped mid-trial must never be silently treated as complete.
- Coverage lands in NWB so anyone pooling units across a session sees immediately what each probe actually covered.

**Behavior-camera sidecar contract** (the interface the separate FLIR project must satisfy):

```yaml
# <session>/bcam<N>/frames.yaml
system: bcam0
trigger_source: syncbox          # frames are Pi-triggered
frame_count: <int>
columns: [frame_index, camera_timestamp_ns, flir_frame_id, exposure_us]
dropped_frame_ids: [<int>, ...]  # from FLIR frame-ID gaps
video_files:
  - path: bcam0_seg000.mp4
    first_frame_index: 0
    last_frame_index: <int>
    codec: <str>
    checksum: <blake3>
```

### 4.7 Timing provenance and tiers

Recorded per session:

- Barcodes emitted / decoded / matched per system; match rate
- Global linear fit residual (µs) and drift rate (ppm) per system
- Event-code agreement between Pi and NI where both present
- Camera trigger count vs. frames received
- Trial count from codes vs. from task file
- Segment count, rejected-segment count, coverage summary

| Tier | Condition | Typical topology |
|---|---|---|
| **A** | ≥2 independent full-code records (Pi + NI), barcodes matched within tolerance, trial counts agree | NP+NI, or NI+Intan |
| **B** | 1 full-code record + ≥1 independent strobe witness | Standalone Intan |
| **C** | 1 full-code record, cross-checked only against task file | Behavior-only training |
| **D** | Any check failed | **Quarantined**, not auto-published, surfaced in daily report |

The tier is **derived, not asserted** — underlying counts, rates, and residuals are retained so it can be re-derived under different thresholds later.

---

## 5. Schema and primary keys

DataJoint's `.alter()` handles non-key attributes; **changing a primary key means drop-and-repopulate**, cascading downstream. Keys below are the expensive-to-change surface.

### 5.1 Adopted vs. custom

| Layer | Source |
|---|---|
| Lab, User, Protocol | `element-lab` |
| Subject | `element-animal` |
| Session | `element-session` (standard datetime-keyed) |
| Probe, insertion, clustering, curation, units, waveforms, QC | `element-array-ephys` |
| Events, trials, blocks | `element-event` |
| Sync, segments, timebase, coverage, provenance | **custom** |
| Eye (ohDPI, calibration, detection) | **custom** |
| Stim (params, events, artifact handling) | **custom** |
| Intan/RHS ingest | **custom** — Elements covers SpikeGLX and Open Ephys only |

### 5.2 Key hierarchy

```
Subject                (subject)
  Session              (subject, session_datetime)
    Block              (…, block_id)              ← mirrors wl.works animal_session_block
    AcquisitionSystem  (…, system)
      Segment          (…, system, segment_barcode)
      RejectedSegment  (…, system, file_path)
    Trial              (…, trial_id)              → block_id as attribute
    TrialCoverage      (…, trial_id, system)
    BlockCoverage      (…, block_id, system)
    Activation         (…, activation_id)         ← one NWB; canonical or derivative
      ActivationBlock  (…, activation_id, block_id)
    ProbeInsertion     (…, insertion_number)
      Clustering       (…, activation_id, paramset_idx)
        Unit           (…, curation_id, unit)
```

**`system`, not `device`** — the segment unit is an *acquisition run*. One SpikeGLX run stops `imec0`, `imec1`, and `nidq` together; RHS stops independently. Systems: `syncbox`, `spikeglx`, `rhs`, `ohdpi`, `bcam`.

### 5.2.1 Blocks are orthogonal to segments, and both are required

A **block** is one run of one task (wl.works' `animal_session_block`). A **segment** is one recording file's extent, forced by an RHS stim-parameter change, a crash, or a restart. **They do not align**: a block can span segments and a segment can span blocks. Neither can be derived from the other, so both exist.

- Block boundaries come from the event-code block markers (§4.2), and are **cross-validated against wl.works' `animal_session_block` rows**.
- `BlockCoverage` is the block-grain companion to `TrialCoverage`: per block per system, `full` / `partial` / `absent`.
- **A block partially covered by a probe is the state that matters.** It is what `block_neural_assertion` is asserted against in wl.works, and what excludes a block from a sort.

**Clustering is keyed on the activation, not the session**, because a sort's unit identity is a product of its block set (§8.3). Two activations over different block sets produce genuinely different units, and nothing may imply otherwise.

**`session_datetime`** follows the `element-session` standard. Deviating from Elements on the most-referenced key in the schema is the worst place to deviate, and the cost of a post-hoc correction is bounded: everything downstream of raw is recomputable, so a drop-and-repopulate costs CPU time, not data.

**Source of the datetime:** the behavioral control system's session start where present; the sync box's NTP-stamped session start otherwise (anaesthetised mapping, spontaneous-activity, and other non-behavioural sessions have no task PC). The source is retained as a secondary attribute `session_datetime_source`.

> **Distinction:** this is the session *label* only. The session **timebase** remains the sync box (§4.5). MonkeyLogic's output is software-scheduled; the Pi is DMA-timed. The behavioural clock must never become the timebase.

**Two safeguards make the datetime key safe:**

1. **NTP is mandatory** on the task PC and every sync box. This should be a rig requirement regardless.
2. **The timestamp is validated at ingest, before it becomes a key.** The watcher cross-checks the behavioural control system's session start against the sync box's NTP wall clock and **refuses to insert** if they disagree beyond tolerance. A clock problem therefore surfaces as a quarantined session on the day it happens, rather than as a primary key that later turns out to be wrong.

**`session_id`** — the human-readable string the Pi generates at session start (`2027-03-14_01`) and stamps into every device's directory — is retained as a **secondary attribute** and remains the on-disk directory name. Queries can restrict on it freely; it simply is not the key.

**`segment_barcode`** is the first barcode value in the segment (uint32): globally unique, immutable, monotonic, so it encodes ordering for free. `segment_idx` remains as a secondary convenience attribute. Discovering a previously-missed file does not shift any existing key.

**`trial_id`** comes from the explicit trial-number payload, never from ordinal position.

### 5.3 Parameter sets

Follows the `element-array-ephys` pattern: `paramset_idx` (int) with a content-hash uniqueness constraint. Paramset tables exist for preprocessing, artifact removal, clustering, LFP, MUA, and eye detection.

Computed tables are keyed on `(…, paramset_idx)`, so **re-running with new parameters adds rows rather than overwriting**. Three sortings of one session with different drift settings coexist permanently with full provenance.

**Paramsets are immutable once used.** The content hash enforces this structurally — an edit yields a different hash, which is a *new* paramset. The CLI refuses in-place modification. Lab defaults are themselves versioned paramsets, so "what were our defaults in March 2027" stays answerable.

### 5.4 Manual triggering

Manual reprocessing is an **insert**, not a second execution path:

```
wlpp run --session 2027-03-14_01 --clustering-paramset ks4_drift_aggressive
```

writes one Manual-tier request row; the populate daemon picks it up. There is no "manual mode" that behaves differently from automatic mode. The CLI accepts the human-readable `session_id` and resolves it to the `(subject, session_datetime)` key internally, so nobody types timestamps.

**Parameters may travel with raw data** as `session_params.yaml` in the session directory. The watcher validates against a schema, **rejects unknown keys** (so `nblocks` vs `n_blocks` fails loudly rather than silently defaulting), content-hashes it, registers it if new, and inserts the request row. Provenance records that the paramset arrived from the session file rather than a lab default.

---

## 6. Ephys processing

### 6.1 Preprocessing chain (paramset-keyed)

1. Ingest via SpikeInterface (`read_spikeglx`, `read_intan`)
2. Bad channel detection and removal/interpolation
3. **Phase shift correction** — required for Neuropixels ADC multiplexing
4. Common reference (median; global or per-shank)
5. High-pass filter for AP band
6. **Stim artifact removal** (RHS sessions only — §6.3)

### 6.2 Sorting

- **Kilosort4** via `spikeinterface.sorters`
- **Multi-segment:** bursts are concatenated via `append_recordings` and sorted **once**, so unit identities remain consistent across the session; spike times are then split back per segment.
- **Boundary handling:** spikes within a few ms of a segment boundary are dropped (discontinuity artifacts).
- **Cross-segment stability:** KS4's drift model assumes continuity, which gaps violate. Per-segment amplitude, waveform, and rate metrics feed a per-unit stability score. Units that shift across a gap are flagged rather than silently trusted.

### 6.3 Stim artifact handling (RHS)

Untreated stim artifacts will wreck sorting.

**Key lever:** the `.rhs` format carries per-sample stim state including amplifier-settle and charge-recovery flags — *the hardware reports exactly when to blank*, which is far more reliable than threshold-based artifact detection.

Artifact removal is a **paramset-keyed stage**, so blanking, interpolation, template subtraction, and multichannel methods (e.g. ERAASR) are swappable and directly comparable. The right choice depends on stim amplitude and geometry and will not be known until real sessions exist.

**Stim sessions are ineligible for auto-publish without review** — artifact contamination is subtle enough to warrant human eyes.

> **OPEN:** confirm exact `.rhs` stim-flag field layout against the RHX file format specification at implementation.

### 6.4 Derived signals

**LFP** — from the NP LFP band where available (NP 1.0), otherwise decimated from AP band (NP 2.0, Intan). Anti-alias filter then decimate. Channel geometry preserved for downstream CSD.

**MUA envelope** — the standard MUAe construction: bandpass ~500–5000 Hz, full-wave rectify, low-pass ~200 Hz, downsample to 1 kHz.

> **OPEN:** verify and cite the MUAe reference (Supèr & Roelfsema) at implementation.

### 6.5 Quality control

`spikeinterface.qualitymetrics`: ISI violations, presence ratio, amplitude cutoff, SNR, isolation distance, L-ratio, d-prime, nearest-neighbour metrics, drift metrics.

Automated curation via rules-based thresholds (in the spirit of Allen's ecephys-spike-sorting) plus optional model-based curation. Curation output is a `paramset`-keyed table, so thresholds are revisable without recomputing sorting.

### 6.6 GPU constraints

The **Quadro P6000** is Pascal (compute capability 6.1), 24 GB VRAM, no tensor cores.

- **CUDA 13 dropped Pascal support.** The P6000 requires CUDA 12.x with a PyTorch build shipping `sm_61` kernels. Pin explicitly; do not allow `pip install -U` to break sorting.
- Versions are **parameterized, not hardcoded**, so the planned GPU upgrade is a config change.
- The upgrade will retain ≥24 GB VRAM, so KS4 batching config stays constant across the swap.
- **Environment isolation:** U'n'Eye is also a PyTorch consumer. Two independently-pinned PyTorch dependents in one environment is how dependency hell starts — separate environments or containers per stage.

**Pre-January benchmark:** run KS4 on synthetic NP sessions to establish a concrete P6000 baseline. This converts "is the P6000 fast enough" into a number, which is exactly what justifies a specific card in a budget request.

---

## 7. Eye and behavior

### 7.1 ohDPI

Two FLIR BFS-U3-16S2M-CS at 500 Hz, running OpenIris with the OpenIrisDPI plugin, plus optional ACCESIO USB-AO16-8A analog output.

**Dual independent sync paths:**

1. **Pi-triggered frames.** `pigpio` DMA waveform generation emits a jitter-free 500 Hz trigger; the existing primary/secondary cable chains camera 2. Eye frame times exist on the sync-box clock *by construction*, on every session type, independent of OpenIris internals or file format.
2. **ACCESIO analog output** into Intan/NI ADC channels on recording days — eye signal lands natively on the ephys clock at full rate, needing no alignment.

> **OPEN:** verify whether OpenIrisDPI surfaces Spinnaker **chunk data** (per-frame GPIO pin state). If so, feeding the barcode into a camera GPIO input stamps every eye frame with the barcode for free. Treat as a check, not a dependency.

### 7.2 Calibration and detection

Calibrated gaze is computed once and is canonical. Detection lives in its own Computed table keyed by `paramset_idx`, so detection parameters are revisable per project without recomputing gaze or touching anything upstream.

**Both detectors run on every session, always:**

1. **Engbert–Kliegl velocity threshold** — the always-on baseline. Small, zero dependency risk, universally accepted, and the algorithm every other method is benchmarked against.
2. **U'n'Eye** — Bellet, Bellet, Nienborg, Hafed & Berens, *Human-level saccade detection performance using deep neural networks*, J Neurophysiol 2019, [10.1152/jn.00601.2018](https://doi.org/10.1152/jn.00601.2018). CNN, human-level accuracy, validated on *Macaca mulatta*. Vendored at a pinned commit — the repo (berenslab/uneye) is effectively dormant with no version pins.

**Their agreement rate is a data-quality metric.** Sessions where the two detectors diverge indicate degraded tracking, surfaced automatically in the daily report rather than discovered during analysis months later.

Also considered: Otero-Millan, Alba Castro, Macknik & Martinez-Conde, *Unsupervised clustering method to detect microsaccades*, J Vis 2014, [10.1167/14.2.18](https://doi.org/10.1167/14.2.18) — threshold-free with a per-detection reliability index, and by the author of OpenIris itself. A strong candidate for a third paramset later.

**Caveat:** U'n'Eye's pretrained weights come from datasets dominated by video-based trackers. A dual-Purkinje tracker at 500 Hz has far lower noise, so the input distribution differs meaningfully. Expect to fine-tune on hand-labeled lab data (a post-January task — "minimal training examples" is U'n'Eye's selling point). Conversely, threshold methods perform *better* on DPI data than their video-tracker benchmark reputation suggests, so the baseline is stronger than it looks.

---

## 8. NWB export and archival

### 8.1 NWB contents

Derived products only — **raw wideband is archived separately**, never embedded. Confirmed by wl.works Plan 25 §0.3: only compressed raw is ever archived, so **an NWB never goes cold**.

Each NWB is **self-contained over its own activation's block set** — trials, LFP, MUA, eye, units, all trimmed to those blocks. Nothing carries data belonging to blocks the activation did not cover.

- Subject and session metadata
- Electrode table with probe geometry (ProbeInterface)
- Units: spike times in session time, waveforms, QC metrics, cross-segment **and cross-block** stability
- LFP and MUA envelope
- Trials table **including per-system coverage columns**, and block membership
- Block table, with wl.works' `block_behaviour_assertion` and `block_neural_assertion` verdicts carried as metadata
- Full event table (all codes with times)
- Eye: gaze, pupil, and detection events from *both* detectors
- Behavior video as external file references plus frame times
- Stim events and parameters
- Acquisition provenance: `acquisitionBuildId`, `stimulusCalibrationId` per block (§4.2)
- **Timing provenance record and tier**

Validated with `nwbinspector` before publication to the analysis array.

> **OPEN:** NWB representation for extracellular electrical stimulation is less well-covered than optogenetics. May require an extension. Resolve before §12 Phase 3.

### 8.1.1 All continuous data at 500 Hz; timing-critical data as event times

**Every continuous channel is stored at 500 Hz.** LFP, MUA envelope, eye position, pupil — one uniform rate, no per-signal exceptions to remember.

**This is already the assumption in wl.works.** Plan 23 §3.1 computes the viewer's entire bandwidth argument from *"LFP at 500 Hz × 384 channels × int16 = 384 KB per second"*, and concludes range reads are *"honest, not merely cheaper than a download."* Storing at 1 kHz would silently double the figures that argument rests on.

**Why the loss is acceptable, and it is the decisive point:** raw wideband is archived losslessly and permanently (§8.4). The NWB is a *convenience artifact*, not the archival record — anything 500 Hz discards is regenerable from the archive by re-running with a different paramset. Nothing is lost, only deferred.

**What 500 Hz actually costs.** Nyquist is 250 Hz, and a real anti-alias filter must roll off below that, so the usable band is ~0–200 Hz. That covers delta through high gamma, and comfortably covers CSD, laminar and traveling-wave analyses, which are dominated by far lower frequencies. What it clips is the top of broadband high-gamma (200–250 Hz) when used as a spiking proxy — and **the MUA envelope is the better instrument for that anyway**, since it is computed from the 500–5000 Hz band *before* any decimation.

**Storage:** 384 ch × 500 Hz × int16 = 384 KB/s per probe. A 2 h dual-probe session is ~5.5 GB of LFP and ~5.5 GB of MUA, so ~11 GB of continuous data — half what 1 kHz would cost.

**Two consequences that are easy to get wrong:**

1. **The MUA envelope's low-pass must sit at ≤200 Hz before decimation**, or the envelope itself aliases. Standard MUAe low-passes near 200 Hz and downsamples to 1 kHz; at 500 Hz output the filter is the same and the margin is thinner, so it is specified rather than inherited.
2. **Eye at 500 Hz is lossless**, not a compromise — ohDPI runs at 500 Hz natively (§7.1), so the uniform rate happens to be its acquisition rate and no decimation occurs at all.

**The photodiode is the exception, and it proves the rule.** At 500 Hz an onset edge is localised to 2 ms — which discards exactly the precision that justified installing a photodiode. So:

> **Anything whose value is its *waveform* is stored as a 500 Hz trace. Anything whose value is its *timing* is stored as event times at native precision.**

| Stored as 500 Hz traces | Stored as event times |
|---|---|
| LFP, MUA envelope | Spikes |
| Eye position, pupil | Event codes, block and trial boundaries |
| Photodiode trace *(QC only)* | **Photodiode onsets/offsets**, detected at native rate |
| | Saccades, microsaccades, fixations |
| | Reward, lick, stim pulses, barcode edges |

The output rate is an `LFPParamSet` / `MUAParamSet` attribute with 500 as the lab default, so a project needing 1 kHz regenerates a derivative from the archive without disturbing the canonical.

### 8.1.2 The NWB must be lazily readable over HTTP range requests

Plan 23 opens these files in the browser through Neurosift using range reads — *"a 160 GB file opened by reading two bytes."* That is a **constraint on how wl-preproc writes HDF5**, not merely on how wl.works serves it:

- Chunk continuous datasets so a time window across channels touches few chunks — time-major chunking sized in the low hundreds of KB, not whole-array chunks.
- Per-dataset compression only. Nothing whole-file, which would defeat ranges entirely.
- Keep `/units` columns as separate datasets, which §8.2's checksum granularity already requires for a different reason.

Untested, this is the kind of thing that works perfectly on the LAN and is unusable in a browser. The synthetic harness (§9) should assert it by serving a generated NWB over range requests.

### 8.2 Checksums wl.works depends on

wl.works Plan 24 §3.3 assigns checksum computation to **this machine**, riding along with a read it was already performing. Two constraints the headline answer misses, both verified there against `hdmf-common-schema`:

1. **Hash decoded dataset contents, never the group.** `colnames` is an attribute *on* the `/units` group and changes whenever a column is appended — so a checksum over the group breaks on exactly the legitimate accretion it must tolerate.
2. **A ragged column is a pair.** `spike_times` and `spike_times_index` are two datasets; hashing one without the other lets a real change hide in the half left unnamed.

Only the **written-once** columns are checksummed — spike times, LFP, MUA, task, behavioural, eye tracking. Everything that accretes (depth, receptive fields, curation) is deliberately excluded.

`(path, size, mtime)` per object is the documented fallback if hashing at terabyte scale proves too slow. That is a **cost** fallback, not a correctness one, and measuring it is one of wl.works' two genuinely open questions.

### 8.3 The canonical NWB, derivatives, and regeneration

Refines wl.works Plan 24 §1.2, which makes one NWB one activation but treats all activations as peers.

| Kind | Origin | Block set | Multiplicity |
|---|---|---|---|
| **Canonical** | Automatic, X hours after session data lands | All session blocks with no `bad` `block_neural_assertion` for that probe | Exactly one current per session |
| **Derivative** | Requested via wl.works | Any hand-picked subset; may span sessions for a chronic array | Unbounded, additive |

**Behavioural badness never gates a sort.** A block with a mis-specified or irrelevant task still contains good neural data; excluding it would discard real spikes and degrade drift estimation for no benefit. Only `block_neural_assertion` — which is per `(block, probe)` — excludes a block from sorting. Behavioural verdicts travel into the NWB as metadata and exclude from *analysis*.

**Regeneration supersedes; it never overwrites.** When a researcher finds a bad block after reviewing the canonical, the corrected canonical is a **new activation** with `supersedesId` pointing at the old one. The superseded NWB stays on the NAS, readable.

> **This is not tidiness — it is the only way enrichment survives.** wl.works Plan 20 §0.1 has derived information accreting into `/units` (depth, receptive fields, curation), and Plan 24 §13 explicitly refuses to hold per-unit records anywhere else. **A re-sort over a different block set produces different units**, because unit identity is a product of the sort — so annotations cannot be carried forward, and an overwrite would silently destroy them with no copy anywhere. Superseding keeps the old file and its annotations readable.
>
> Cost: roughly 25 GB per derived NWB against ~180 GB of compressed raw per session, so two or three canonical generations is a modest overhead — and since NWBs never go cold, they stay online.
>
> This is also the argument for a generous X-hour window: **the window is what makes regeneration rare.**

### 8.4 Compression and archival

**One compressed artifact per session** — not per segment and not per block. This is wl.works Plan 25 §1.2's grain, on the requester's own words: *"archiving data will always be done with the compressed raw data at the session-level, not in any other way."* Plan 25 §1.3 turns on it: you restore a session's archive or nothing, so blocks never move independently, which is why a per-block archive record was designed and withdrawn.

- **Lossless compression**, likely WavPack via Zarr (the current recommended approach for Neuropixels-scale data).
- **Mandatory roundtrip verification:** decompress and compare against the original hash before the original becomes eligible for deletion. "Lossless" is a property of a correct implementation, not a promise — a silent compression bug found in 2029 would be unrecoverable across every session.
- Checksums recorded in the DB alongside archive location, as **host + share + relative path** (§11.2), never an opaque string.
- **A completion sentinel is written when the artifact is whole.** wl.works' `nas_artifact_observation.complete` records whether that sentinel was seen; without it, a partially-written artifact is indistinguishable from a finished one.
- **Scratch is reclaimed only when** archived + roundtrip verified + cold copy confirmed, **the human "checked good" gate has passed** (below), *and* either no pending paramset requests exist or a warm copy is present.
- **Backpressure at ingest:** the watcher refuses new sessions below a scratch high-water mark and alerts, rather than filling scratch and stalling mid-sort.

### 8.5 The "checked good" gate — an item wl.works explicitly leaves to this pipeline

Plan 23 §12 records two facts that arrived mid-session and that no spec there owns. Item 2 became roadmap row 25. **Item 1 is still owned by nobody, deliberately:**

> *"A human **"checked good"** gate exists between preprocessing and archival. Nothing models it… the "checked good" gate is a workflow step in **somebody else's pipeline** and was declined rather than folded in."*

**That pipeline is this one.** wl-preproc claims the gate.

**Where the gate actually belongs is not where the sentence puts it.** Read literally, it blocks compression and archival — which would leave hundreds of GB sitting on scratch waiting for a human, and scratch is five sessions deep (§3.3). A researcher on holiday stalls acquisition.

**Resolve it by splitting the two things the gate is conflated with.** Compression and archival are *safety* operations: they are reversible, they add copies, they destroy nothing, and under a keep-everything-forever policy they are correct regardless of whether the science is good. **Scratch reclamation is the irreversible one.** So:

| Step | Gated by |
|---|---|
| Compress, roundtrip-verify, checksum | Nothing — runs immediately |
| Copy to NAS (T2) and cold (T3) | Nothing — runs immediately |
| **Reclaim scratch** | **The human "checked good" verdict** |
| Publish canonical NWB to the analysis array | Automatic; tier D quarantines instead (§4.7) |

This gives the gate what it is actually for — *don't throw away the fast copy until someone has confirmed the derived products look right* — without letting a person's absence stall the pipeline. If the verdict is "bad", the raw is already safely archived and the fast copy is still there to re-run from.

**The verdict is recorded, not inferred:** an actor, a timestamp, a verdict and a reason, in the shape wl.works uses for every other judgment. It is surfaced in the daily report alongside stuck jobs, since an ungated session is what will eventually fill scratch.

> **OPEN:** verify and cite the compression-strategy reference (Buccino et al., compression for large-scale electrophysiology) at implementation.

---

## 9. Testing strategy

**This is what makes a data-free build possible.** The pipeline is ~85% buildable before January; what cannot be done is *tuning*.

### 9.1 Synthetic session generator

Emits byte-format-correct files with known ground truth:

- SpikeGLX (`.bin` + `.meta`), Intan RHS (`.rhs`), sync box log, OpenIris output, behavior camera sidecar, task file
- Planted spike trains (via SpikeInterface ground-truth generators / MEArec), known event codes, known trial structure, known barcode times

**Injected pathology — the part that matters:**

| Fault | Tests |
|---|---|
| Clock drift (configurable ppm) | Pooled rate fitting |
| Dropped barcodes | Match-rate metrics, degraded fits |
| Segment < 1.2 s | `RejectedSegment` path |
| Mid-session restart with gap | Segment model, coverage matrix |
| Recording stops mid-trial | `partial` coverage state |
| Dropped camera frames | Exposure-return counting |
| Missing device | Topology discovery, tier derivation |
| Trial count mismatch (codes vs task file) | Hard-fail cross-validation |
| Stim artifacts | Artifact removal, blanking from flags |
| Truncated/corrupt file | Ingest validation, quarantine |

### 9.2 CI

Full pipeline runs on synthetic sessions; assertions compare recovered spike times, trial tables, and coverage matrices against ground truth within tolerance. A **scratch DataJoint schema** is populated by the generator so people can experiment without touching production.

### 9.3 Real-data validation

Two to three real public sessions (DANDI has NHP Neuropixels data) to catch format assumptions the generator bakes in wrong. The generator can only test what its author imagined; real files cannot.

---

## 10. Operations and guardrails

Written for a DataJoint first-timer. The four real DataJoint hazards, each with explicit handling:

| Hazard | Handling |
|---|---|
| **Long computations in `make()` transactions** — a 4 h Kilosort run holds a MySQL connection and hits `wait_timeout` | Compute outside the insert; raise `wait_timeout`/`max_allowed_packet`; ping-and-reconnect. Newer DataJoint supports splitting `make` into fetch/compute/insert phases — **OPEN:** confirm exact API |
| **Stale job reservations** — a crashed populate leaves `~jobs` marked reserved and the session is skipped forever | Scheduled reaper; "stuck jobs" front and centre in the daily report |
| **Primary key changes** require drop-and-repopulate | Keys documented in-schema with an explicit warning; §5 chosen for stability |
| **Cascading deletes** reach further than expected | **No bare `.delete()` anywhere in the codebase** |

**CLI guardrails:**

- `wlpp delete --session X --from-stage Y` — prints the full cascade, defaults to `--dry-run`, requires explicit confirmation
- `wlpp doctor` — DB connectivity, external store paths, scratch and warm-pool headroom, GPU visibility, stale jobs, orphaned files
- `wlpp pin` / `wlpp unpin` — warm-tier cache management

**Daily status report:** ingested / populated / failed / **stuck** / quarantined, disk headroom, tier-D sessions, eye-detector disagreement outliers.

**MySQL backup is a first-class pipeline component.** Provenance lives in the DB. Nightly dump → NAS → cold tier, with restore tested on a schedule.

**Documentation:** a query cookbook covering the ~15 restrictions and joins a trainee actually needs, so nobody has to learn relational algebra to get spike times.

---

## 11. Integration with wl.works

wl.works is the control plane. It owns blocks, quality verdicts, activations, dataset selection and every research judgment. wl-preproc executes and reports, and **renders no verdicts of its own**.

### 11.1 Pull, not push — and it is not negotiable

wl.works publishes its app port bound only to a WireGuard interface address. **wl-preproc is on the lab LAN and cannot reach it** without leaving the building, crossing an untrusted VPS, and coming back. So:

- **wl.works opens every connection.** It posts requests, polls for status, and pulls results and figures on completion.
- **wl-preproc never initiates.** Any design here that "reports back to wl.works" is wrong about the direction, not the outcome — results still land in wl.works.
- wl-preproc **publishes its own action list**, which is what lets one host serve five different dispatching stages without wl.works hardcoding what it can do.

### 11.2 The protocol document is a shared artifact

wl.works' `waiting-on.md` already records that the wl.works↔wl-preproc protocol *"is better written before"* the hardware exists. **It is a pre-January deliverable and belongs in §3.5's frozen-interface list.** wl-preproc owns the responder half; wl.works owns the caller half; both build against it, and wl.works' 18b tests are contract tests against a *fake wl-preproc*, which only works if the contract is written down.

The protocol carries, at minimum:

| Direction | Content |
|---|---|
| wl.works → wl-preproc | Action list request; job request with `(domain, selection, parameters, idempotencyKey)` |
| wl-preproc → wl.works (in response to polls) | Action list; job status including *"already running since 09:02"*; resolved component versions and commit SHAs; result metrics; figure files; artifact locations as **host + share + relative path** |

**Artifact locations are a triple, never a string.** wl.works Plan 23 §10.1 replaced the opaque `artifactLocation` with `artifactHostId` + `artifactShare` + `artifactPath` precisely so an agent can open the file rather than a human reading a path out of a field.

**The health/action endpoint schema is already fixed** by wl.works Plan 10 §4, and wl-preproc serves it:

```json
{
  "verdict": "ok",
  "readings": [
    { "key": "transfer",   "label": "Latest transfer", "value": "complete",       "featured": false },
    { "key": "spike-sort", "label": "Spike sorting",   "value": "4 of 7 sessions","featured": true  }
  ],
  "actions": [
    { "name": "start-preproc", "label": "Start preprocessing run" }
  ]
}
```

Two properties of that contract shape what we build. **The app makes no domain claims** — it renders `label` and `value` as opaque strings and never learns what spike sorting is, which is what let the contract be written before any machine existed. And **the host publishes its own action list**, so adding a sixth job type needs no change on the wl.works side.

**We are treated as untrusted, and should behave accordingly.** That spec notes a compromised host controls strings rendered in the app's UI, so labels render as escaped plain text and never markup — enforced there by a test. Nothing wl-preproc emits into `label` or `value` should ever contain markup, and the synthetic harness should assert it.

### 11.3 Concurrency: queue, never refuse

Two different rules apply at two different boundaries, and both are inherited rather than invented here.

- **Across the network:** the idempotency key distinguishes a retry of an accepted request from a genuinely new one. **It is not what prevents a second run.** wl.works never asks "is a run going, then start one" — that is a check-then-write race stretched across a network boundary, where no lock either side takes reaches the other.
- **On this host:** wl-preproc **queues rather than refuses**, because concurrent runs are legitimate and expected — analyses run staggered across blocks and tasks. It **dedupes on the run key**: a request whose `(selection, task type)` is already in flight returns the running one instead of starting a second.
- **One job runner holds the machine**, with priority expressed inside it. Two runners each concluding the machine is free is the same failure refused for VRAM allocation, and here it is tractable because both contending parties are on one host.

The DataJoint populate daemon **is** that job runner. The responder does not compute; it inserts a Manual-tier request row and the daemon picks it up, exactly as the ingest watcher does.

### 11.4 The five dispatch domains

| Stage | Domain | What this host runs |
|---|---|---|
| Plan 18 · 18b | `behaviour` | Per-task behavioural analysis. Pulls `wl-bhveval` from GitHub, reports the **resolved commit SHA it actually pulled** |
| Plan 20 · 20b | `neural` | Spike sorting — one run per probe, fanned out from one activation |
| Plan 24 · 24b | `export` | Trim-and-export an NWB to a block subset for DANDI deposit |
| Row 27 · 27b | — | `@animal_warper` atlas registration; leaves a ~690 MB output directory |
| Row 29 · 29b | — | Stimulus calibration from a `.spectrashop` measurement; leaves a ~112 KB output directory |

Two consequences worth stating plainly. **wl-preproc needs outbound egress to GitHub** and a git client — an operational precondition that will otherwise be discovered at the worst possible moment. And **a trimmed export carries the original unit metrics with a disclosure**, never recomputed: units are pooled across the whole sort, so recomputing over a subset would make *"dataset 47's median SNR"* resolve two ways with no rule saying which is authoritative.

### 11.5 Appending to an NWB while somebody reads it

wl.works Plan 23 §8 establishes that a concurrent read of an NWB being appended to is genuinely unsafe, and that the risk is **accepted rather than mitigated** — its in-flight banner is disclosure and explicitly never a gate, because no lock that app can take reaches this machine.

**wl-preproc can do better than accept it, cheaply, and should.** Append to a copy on scratch, then atomically rename into place. A reader either sees the old complete file or the new complete file, never a half-written one. The cost is one file copy on fast local storage; the alternative is a corrupted read with no error message.

### 11.6 Per-channel detail is ours, by their deliberate choice

wl.works has drawn a boundary here that decides what this machine owes it, and it is easy to cross by accident in either direction.

**The channel map is derived by wl.works, not received from us.** Plan 19 §4.1 considered parsing what SpikeGLX emitted and **overrode it**: *"before the day there is no file. The session planner has to be able to say 'these probes, this chain, therefore these channels'… receiving cannot plan."* So wl-preproc must not supply the map, and any design here that "reports the channel map to wl.works" is reversing a settled decision.

**But that same section states the risk it takes on**, and it is one this machine is uniquely able to close:

> *"The app is now the authority on the map, so a wrong pinout silently corrupts every recording that used that device, and it is found in analysis months later by someone who trusted it."*

**wl-preproc reads what the hardware actually recorded, so it can verify rather than supply.** SpikeGLX's `.meta` carries the readout table and geometry map; Intan's header carries its channel list. Comparing the derived map against the recorded one turns *"found in analysis months later"* into a QC failure on the day, in the daily report, next to the barcode match rate. **This does not reverse §4.1** — wl.works still derives and still plans; we only disagree out loud when the hardware says otherwise.

**Per-channel detail belongs here and nowhere else, and that is also settled.** Plan 19 §6.2's Plan 20 note records that per-unit and per-channel detail stays on wl-nas on the requester's explicit choice, so *"wl.works holds no per-channel area at all"* — and warns that a later plan starting to store per-channel area *"as evidence"* would re-create a hazard recorded as closed. Two consequences:

- **The NWB electrode table is the only home for per-electrode area, depth and geometry.** It is not a convenience copy of something wl.works has; it is the record.
- **The per-recording site selection** — which ~384 of >4000 electrodes were enabled, which the glossary flags as recorded nowhere and which date-versioned pinouts structurally cannot express — is read from the `.meta` readout table and written into the electrode table. That closes a gap their corpus lists as unowned, on our side of the boundary rather than theirs.

**And two of their four area sources are our outputs.** `insertion_area_assignment.source` admits `functional_mapping` and `waveform_depth`; both are things a sort produces. A person reads a sort summary and writes the row — wl.works is explicit that no machine writes it — so what wl-preproc owes is a summary a human can act on, not an assignment.

---

## 12. Roadmap to January

| Phase | Window | Deliverable |
|---|---|---|
| **0** | Aug–Sep 2026 | **Freeze contracts** (§3.5). Pi firmware. Breakout PCB design. Event code protocol finalized against first task implementations. |
| **1** | Sep–Oct 2026 | Synthetic generator. DataJoint schemas. Ingest watcher. Sync/timebase/coverage. CI green on synthetic. |
| **2** | Oct–Nov 2026 | Ephys branch: SpikeGLX + Intan readers, artifact removal, LFP, MUA, KS4, QC. **P6000 benchmark.** |
| **3** | Nov–Dec 2026 | Eye and behavior branch. NWB export + validation. Archival, compression, roundtrip verification. Ops tooling. |
| **4** | Dec 2026 | Validation against public DANDI sessions. Dry runs against rig hardware as it arrives. Prepare hand-labeling workflow for U'n'Eye fine-tuning. |
| **Live** | Jan 2027 | First real sessions **validate** rather than discover. |

### 12.1 Role B is not in the phases above, and that is a gap rather than a deferral

Phases 0–4 build **role A** only — the pipeline that runs itself after a session. The responder (§11) and the five dispatch domains (§11.4) appear nowhere.

**Why that matters more than it sounds:** without the responder, the only sorts that ever happen are the automatic canonical ones. **A derivative activation cannot be run at all** — no re-sort with different parameters, no trim-and-export, no DANDI deposit. "Role B is later" therefore means "no re-sorts, ever" until it lands, which is not what anyone intends.

The five domains are not equally urgent, and the split is clean:

| Domain | When | Why |
|---|---|---|
| **Responder + action list + job queue** | Phase 1 | It is the entry point all five share, and role A's queue is the same queue. Cheap if built with the ingest watcher; expensive to retrofit. |
| **20b spike sorting on request** | Phase 2 | Ships with the ephys branch — it is the same code with a different trigger. |
| **24b trim-and-export** | Phase 3 | Ships with NWB export, same reason. |
| **18b behavioural analysis** | Post-January | Needs `wl-bhveval`, which does not exist yet, plus outbound GitHub egress. |
| **27b atlas registration, 29b calibration** | Post-January | Independent tools with no dependency on anything else here. |

So three of five fold into existing phases at near-zero marginal cost, and two are genuinely later. **The phases above are amended in the table sense rather than restructured** — no phase moves, one line is added to each of 1, 2 and 3.

**Purchasing recommendations arising from this design:**

- **NI PXIe-6363**, not the 6341 (8 waveform DI cannot fit 16-bit codes + strobe + barcode). Avoid USB NI devices for digital input — SpikeGLX warns of digital buffer overruns.
- **Raspberry Pi 4**, not Pi 5 (`pigpio` DMA support).
- **Intan I/O expander** at the main rig; base RHS suffices at a satellite rig (barcode + strobe = 2 lines).
- **≥10 GbE** server↔NAS and rig↔server.
- Cameras must accept external frame trigger; this is unfixable after purchase.

---

## 13. Open items

| # | Item | Blocking |
|---|---|---|
| 1 | NWB representation for extracellular electrical stim; may need an extension | Phase 3 |
| 2 | Whether OpenIrisDPI surfaces Spinnaker chunk data (per-frame GPIO state) | Phase 3 (enhancement only) |
| 3 | Exact `.rhs` stim-flag field layout | Phase 2 |
| 4 | MonkeyLogic 16-line behavioral code configuration on the chosen task-PC DAQ | Phase 0 |
| 5 | Tolerance for the ingest-time task-PC vs. sync-box clock cross-check | Phase 1 |
| 6 | DataJoint `make` fetch/compute/insert splitting API | Phase 1 |
| 7 | MUAe and compression-strategy citations | Phase 2 |
| 8 | Event code range allocation finalized against real tasks | Phase 0 |
| 9 | **Who creates `animal_session_block` rows, and when.** They are human-created in wl.works, but the canonical activation fires X hours after landing and needs a block set to select over. Either the session planner's rows pre-exist and wl-preproc matches its detected boundaries against them, or wl-preproc proposes blocks from event codes and wl.works adopts them — the second is more robust but writes into wl.works from a machine, which several rules there resist | Phase 0 — it gates the canonical trigger |
| 10 | **The X-hour canonical delay value.** Long enough that regeneration is rare (§8.3), short enough that a sort exists by morning | Phase 0 |
| 11 | Whether `seed` and `device` are pinned to the activation or may differ across its probe runs. wl.works flags this as unsettled; **wl-preproc is the machine that would pin them**, so this is answerable from here | Phase 2 |
| 12 | Identity of the actor for automatic canonical activations — a system user, or a nullable `requestedBy` under an `origin` discriminator (§11) | Phase 1 |
| 13 | Who renders the "checked good" verdict (§8.5), and whether it is entered here or in wl.works. **Nothing in wl.works models it and it was declined rather than folded in**, so if it lives there it needs a row somebody designs | Phase 3 |
| 14 | Chunk shape and per-dataset compression settings that keep the NWB efficiently range-readable (§8.1.2). Measurable on synthetic files before January | Phase 3 |
| 15 | Whether the derived-vs-recorded channel map comparison (§11.6) should ever *block* a session or only warn. Blocking makes wl.works' pinout a hard dependency of preprocessing | Phase 2 |

---

## 14. Amendments made to wl-works

wl-works requires that a design amending another document carry a ledger of what it changed. This spec drives amendments in that repository, so the ledger lives here. **All were executed 2026-08-12, before this section was written; verify rather than trust that, since in-session execution is the weaker form and that repository's own record is that execution is reliable while completeness is not.**

| # | Document | Change |
|---|---|---|
| 1 | Plan 24 §1.2 | Canonical vs. derivative activations; the enrichment-loss hazard that makes supersession load-bearing; the unrepresentability of an automatic NWB |
| 2 | Plan 24 §10.4 | Five columns on `analysis_activation`, two `CHECK`s, and the partial unique index |
| 3 | Plan 20 §1.2 | The activation is this plan's table, so the amendment is recorded at its owner; the `origin` gap and the two-judgment split gaining a consumer |
| 4 | Plan 11 §3.2 | Flagged, not fixed: who creates `animal_session_block` rows and when |
| 5 | Design spec §4 (`2026-07-29-lab-wiki-design.md`) | The column list for `analysis_activation`, which enumerates columns and would otherwise have gone stale |
| 6 | `docs/ops/waiting-on.md` | Two items under **"Waiting on a prior decision"** — block creation, and the canonical delay value |
| 7 | Plan 23 §12 | **Item 1 claimed.** The "checked good" gate, and the correction that it belongs on scratch reclamation rather than on archival |
| 8 | Plan 19 §4.1 | The derived map gains an external check. **The derive-not-receive ruling is explicitly not reopened** |
| 9 | Glossary §6.2 | The per-recording site-selection gap gains an owner — and the reason it could never have been wl.works' |

**Item 5 was nearly missed, and how it was caught is worth recording.** The first pass amended the two plan specs and stopped. The design spec turned out to enumerate `analysis_activation`'s columns rather than only naming the table, so a column-level change reaches it — found by grepping the identifier across the repository rather than by re-reading the amendment list. That is that repository's own convention diff, and it produced exactly the class of omission its ledger discipline exists to catch.

**Not amended, stated rather than left silent:**

- **`AGENTS.md` and `CHECKPOINT.md` counts.** No table is added, no spec is added, no roadmap row changes status. Every count in both files is about rows, specs and implementation plans, and none of those move. **Recounting was not needed because nothing countable changed** — stated so the next reader does not assume it was skipped.
- **The roadmap table.** No cell's status changes; verified by diff.
- **`brainstorm-queue.md`.** This design ran in another repository, so nothing there opened or closed.
- **Plan 25.** Its §1.2 session-grain archive rule needed no amendment — *this* spec was wrong about the grain and was corrected (§8.4). The error ran the other way.
- **Plan 23.** Its §8 accepts the concurrent-read-during-append risk explicitly. §11.5 here mitigates it on this side without changing anything there, so its acceptance is untouched and simply stops being load-bearing.

**One form is new and is flagged rather than assumed acceptable:** these amendments cite a spec in a *different repository* by path. Every prior cross-reference there is a sibling link that a reader can follow. A cross-repo path cannot be followed and cannot be checked by any tooling in that repository, so it will decay silently — the exact failure mode `AGENTS.md` warns about for citations into `next-session.md`. Whoever plans 11a, 20b or 24b should decide whether the wl.works↔wl-preproc protocol document (§11.2) becomes the shared artifact both repositories cite instead.
