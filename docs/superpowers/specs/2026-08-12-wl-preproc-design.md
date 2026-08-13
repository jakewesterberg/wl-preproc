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
| Runtime | Python-first (`>=3.11`, no upper cap): SpikeInterface, Kilosort4, NeuroConv/pynwb. **PyTorch from the `cu126` index only** (§6.6) |
| Orchestration | DataJoint + DataJoint Elements, with explicit newcomer guardrails |
| Compute | 16-core AM4, 128 GB RAM, Quadro P6000 (24 GB), ≥4 TB NVMe scratch, ≥10 GbE |
| Server OS | **Fedora** on wl-preproc |
| Sync box OS | **Raspberry Pi OS** — the vendor kernel, for the vendor's silicon (§4.3) |
| Environments | **One container per stage**, built at deploy not per run. Image digest is the provenance identity (§6.6.1) |
| Sync master | Sync box (**Raspberry Pi 5** + RP1 PIO), one per rig, present at every session. Separate repo, `wl-sync` |
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
| Canonical grain | Per **`(session, recording montage)`** — a montage is a maximal interval with no probe movement |
| Montage source | **wl.works `item_insertion` alone.** No insertion record → no canonical, session quarantined |
| Characterization | A plugin registry keyed on task capability, not a frozen task contract |
| Depth | Three independent methods computed and stored, plus their agreement. kCSD, not standard CSD |
| Provisional data | Characterizations enter the canonical NWB immediately, flagged `provisional` until a human verdict |
| "Checked good" gate | Claimed by this pipeline; gates **scratch reclamation only**, never compression or archival |
| Channel map | wl.works **derives** it; wl-preproc **verifies** it against the recorded `.meta` and disagrees out loud |

---

## 3. System architecture

### 3.1 Components

| # | Component | Runs on | Role |
|---|---|---|---|
| 1 | Sync box | Pi 5, one per rig | Barcode generation, camera triggers, event-code capture, local buffer, push to server |
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

**The sync box is a separate repository, `wl-sync`.** It runs on different hardware (Pi 5), carries a different dependency (`piolib`, behind an optional extra), and is useful to any rig independently of this pipeline. It owns **everything it produces** — session identity, the barcode codec, and the log format — and `wl-preproc` depends on it. The dependency runs one way only; `wl-sync` must never import from here.

```
wl-sync/                       ← separate repo
  wl_sync/
    session.py   SessionId, minted by the Pi at session start
    barcode.py   codec, pure — no hardware dependency
    log.py       on-disk log: edges, strobed code words, tick unwrapping
    backend.py   SyncBackend protocol + FakeBackend
    rp1/         PIO capture decoding and the Pi 5 backend
    gpio.py      GpioBackend protocol + FakePigpio
    service.py   BarcodeGenerator, EdgeRecorder

wl-preproc/
  wl_preproc/
    contracts/ manifest, event codes, sidecar, wl.works protocol, session layout
    schemas/   DataJoint schemas: lab, subject, session, sync, event, ephys, eye, video, stim
    ingest/    watcher, manifest validation, device discovery, session-complete detection
    sync/      timebase construction, coverage model, provenance metrics
    events/    code decoding, trial tables, task-file adapters
    ephys/     spikeinterface wrappers, artifact removal, lfp, mua, kilosort, qc
    characterize/  the plugin registry: depth, RF, preference maps (§6.8)
    eye/       ohDPI reader, calibration, detection (Engbert–Kliegl, U'n'Eye)
    export/    nwb assembly + validation
    archive/   compression, roundtrip verification, checksums, tiered transfer
    responder/ the HTTP surface wl.works polls (§11)
    cli/       wlpp commands
  tests/synth/   synthetic session generator
  docs/schemas/  exported JSON Schema for wl.works and the camera project

hardware/breakout/  distribution PCB: buffers, level shifters, optoisolators
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
| ≥ 3.0 s | ≥2 complete barcodes | Local offset fit + local rate verification |
| ≥ 2.0 s | ≥1 complete barcode | Offset from barcode, rate inherited from device-level fit |
| < 2.0 s | May contain zero | **Unalignable** → `RejectedSegment`, excluded, flagged |

Trials can be as short as 3 s, so a single-trial segment always clears the one-barcode bar and normally clears the two-barcode bar as well.

> **Corrected 2026-08-13 while writing the Phase 0 plan, and the correction is a consequence of a decoder decision rather than of the format.** This table first read 2.2 s / 1.2 s / <1.2 s, derived from frame geometry alone: frames are 200 ms long at 1 Hz, so a 2.2 s window must contain two whole frames.
>
> **That assumed a decoder that recognises a frame from its internal structure alone, and the implemented decoder does not.** It requires a **preceding idle** of ≥400 ms to identify the lead pulse, because a lead pulse and a run of two set bits are both 10 ms of HIGH and are otherwise indistinguishable. Requiring the idle makes false positives structurally impossible — a garbage barcode is far worse than a missing one — but it means a frame decodes only if the window *also* contains the end of the previous frame, which adds one inter-frame interval to every bound.
>
> **This does not threaten the 3 s trial case, because one barcode is sufficient.** §4.5 fits clock rate per `(system, session)` across all segments and needs only an offset locally, so a 2.0 s guarantee covers a single-trial segment with a second to spare. The two-barcode case buys local rate *verification*, which is a QC nicety rather than a requirement.
>
> **Kept visible rather than silently fixed**, because the failure is one this design is otherwise careful about: a number derived from geometry, correct about the format, and wrong about the thing that would actually read it.

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

### 4.3 Sync box (Pi 5)

**Hardware:** Raspberry Pi 5.

> **Changed 2026-08-13, reversing this section's original Pi 4 ruling.** It read *"Raspberry Pi 4, 2 GB. **Not Pi 5**"*, on the grounds that `pigpio` cannot reach the Pi 5's RP1 southbridge. That fact is correct and unchanged — `pigpio` and `RPi.GPIO` both mmap Broadcom SoC registers the Pi 5 does not expose, and the kernel-chardev replacements (`lgpio`, `gpiozero`) are interrupt-driven with millisecond tails. **What was wrong was treating the sync box as one hard-real-time job.**
>
> It is three jobs, and only one is demanding. Working that out is what made Pi 5 viable:
>
> | Function | Requirement | Why |
> |---|---|---|
> | **Barcode output** | Very loose | 5 ms bit slots sampled at 30 kHz give 150 samples per bit, and **each receiver times its own edges** — the barcode carries identity, not timing. Several hundred µs of generator jitter is harmless. |
> | **Camera trigger** | Loose | The box records its own output edges, so jitter is *measured* rather than error — and on Pi 5 it is a hardware PWM channel anyway. |
> | **Event strobe capture** | **~100 µs or better** | The only hard one. On training days this is the sole record of event times, and reaction times and saccade latencies come off it. |
>
> So Pi 5 needs PIO for **one** function, not all three — a bounded piece of work rather than the rewrite this section originally assumed. The decision was taken on **supply chain and maintenance**, not on precision: `pigpio` is Pi-4-only and unmaintained, and starting a decade-long rig on a toolchain the vendor has moved past is starting in debt. Precision was never the differentiator — `pigpio`'s 1–5 µs already beat the requirement by 20×.

**Timing approach:** three mechanisms, one per function.

| Function | Mechanism |
|---|---|
| Event strobe capture | **RP1 PIO** via `piolib` — a hardware state machine, sub-µs and deterministic |
| Camera trigger | **Hardware PWM**, GPIO12/13/18/19 |
| Barcode output | Ordinary GPIO, software-timed |

**A new hardware constraint follows, and it reaches the breakout PCB.** PIO parallel capture reads a **contiguous pin range**, so the 16 code lines plus strobe must be adjacent on the header. `pigpio` sampled all GPIO simultaneously and had no such requirement, so this pins down the GPIO map before the board in §4.4 is designed.

**Raspberry Pi OS on the sync box, whatever the rest of the lab runs.** RP1 support is landing in mainline incrementally — GPIO and pinctrl patches in flight, a PWM driver arriving around April 2026 — but **PIO is the piece this design depends on entirely**, and vendor kernels carry vendor silicon support first. The sync box is an appliance running one service, so there is no upside to matching the lab's general distribution and a real downside to running ahead of the driver.

**This constrains nothing else.** The sync box and the preprocessing server are different machines; the server runs **Fedora** (§6.6). Two practical consequences there, both the kind that bite at 8am rather than at install time:

- **Fedora ships Python 3.13 as system Python**, and this pipeline is pinned to 3.11 by the Kilosort4/PyTorch requirement for Pascal. Use the packaged `python3.11` or a `uv`-managed environment; do not fight the system interpreter.
- **The NVIDIA driver is an out-of-tree module on a fast-moving kernel.** `akmod-nvidia` rebuilds it automatically, but a kernel update can still leave CUDA broken until that rebuild completes — on a machine expected to sort overnight. Hold or version-lock the kernel and update it deliberately rather than letting it ride.

**The risk is bounded by construction.** `wl-sync` reaches hardware only through a mechanism-neutral `SyncBackend` protocol with an in-memory fake, so the codec, log format and session identity are pure Python tested in CI, and a Pi 4 + `pigpio` backend remains one class away if the PIO acceptance gate fails.

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

**Residual risk:** the PIO strobe-capture program is the one piece with no precedent to copy in this application. It is gated on a bench acceptance test — timestamps within ±100 µs over a 10-minute run with zero dropped words — and a Pi 4 + `pigpio` backend remains one class away behind the same protocol if that gate fails.

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
    Montage            (…, montage_id)            ← recording montage; from item_insertion
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

- **The constraint is the `cu126` wheel index, and nothing looser.** Verified 2026-08-13 against PyTorch's published architecture lists rather than assumed:

  | Wheel index | Pascal (`sm_61`) |
  |---|---|
  | **cu126** | **Yes** — sm_61, 70, 75, 80, 86, 90 |
  | cu128 / cu129 | **No** — dropped from PyTorch 2.7 on binary-size grounds |
  | PyPI default | **No** — CUDA 13.0 became the stable PyPI variant at PyTorch 2.11, and CUDA 13 dropped Pascal entirely |

  **So `pip install torch` now silently yields a build that cannot run on this GPU.** It must come from the cu126 index explicitly, and the container image (§6.6.1) is what makes that permanent rather than a thing someone remembers.

> **Corrected 2026-08-13. This bullet read "CUDA 12.x with a PyTorch build shipping `sm_61` kernels", which is too loose in exactly the direction that fails silently** — cu128 *is* CUDA 12.x and has no Pascal kernels, so the rule as written would have permitted a wheel that cannot run. The same check retired a second claim: **`requires-python = ">=3.11,<3.12"` was invented.** It was justified here as "the Kilosort4/PyTorch pin for Pascal", but cu126 ships wheels for cp39 through cp315 — Python was never constrained by Pascal at all, and the two were conflated. The cap propagated into `wl-sync`'s plan before being removed there for an unrelated reason, which is how an invented constraint survives: it gets cited rather than re-derived.
>
> **Python is now `>=3.11` with no upper bound**, matching `wl-sync`. Reproducibility comes from the container digest (§6.6.1), not from narrowing a range in `pyproject.toml`.

- **This narrows the P6000's runway, and that is the real argument for the upgrade.** cu126 is the last index carrying `sm_61`, and it will stop being built. The card works today and its supported-software window is closing — which is a better reason to budget for a replacement than raw throughput.
- Versions are **parameterized, not hardcoded**, so the planned GPU upgrade is a config change.
- The upgrade will retain ≥24 GB VRAM, so KS4 batching config stays constant across the swap.
- **Environment isolation:** U'n'Eye is also a PyTorch consumer. Two independently-pinned PyTorch dependents in one environment is how dependency hell starts. Resolved by §6.6.1 — one container per stage.

**Pre-January benchmark:** run KS4 on synthetic NP sessions to establish a concrete P6000 baseline. This converts "is the P6000 fast enough" into a number, which is exactly what justifies a specific card in a budget request.

### 6.6.1 Every stage is a container, and the digest is the provenance

**One mechanism, not two.** Every pipeline stage runs as a container with a pinned image digest — including the pure-Python ones, where a lockfile would have sufficed. Uniformity is worth more than the marginal saving: one form of environment identity, one rebuild story for the server, and no per-stage judgment about which mechanism applies.

The lab already runs Docker Compose across `wl-works` and `wl-elab`, so this is an existing competence rather than a second new system landing beside DataJoint.

**Environments are built at deploy, never per run.** Per-run creation would be slow (resolving PyTorch and CUDA is minutes and gigabytes), network-dependent (a registry outage would stop acquisition), and — worst — **non-deterministic**, so two runs weeks apart would silently differ. That would make provenance weaker while appearing to strengthen it.

**The image digest satisfies an obligation that already exists.** wl.works expects resolved component versions back from this host (§11.2), and `analysis_component_version` is where they land. A digest is a stronger identity than a version string or a lock hash, so this is not extra bookkeeping — it discharges a contract already owed.

**Old images are retained, never replaced.** Re-running a 2027 session under its original environment means pulling its recorded digest. Same supersede-don't-overwrite rule as canonical NWBs (§8.3) and paramsets (§5.3).

#### What containers do *not* buy, stated because the opposite was claimed first

> An earlier draft of this design argued that containers insulate the pipeline from Fedora's kernel and `akmod` churn. **That is half wrong and the wrong half matters.** A container pins the **CUDA userspace toolkit**; the **NVIDIA kernel module lives on the host**. If a Fedora kernel update lands before `akmod-nvidia` rebuilds, a containerised sort fails exactly as a host-installed one would. Containers make the toolkit reproducible and do nothing about the driver — so §4.3's hold-the-kernel discipline remains the actual mitigation, not a belt-and-braces extra.

#### Three constraints that are cheap now and expensive later

1. **Logs land on a bind-mounted host path**, not container stdout alone. The entire ops design (§10) optimises for a trainee diagnosing an overnight failure; that survives containerisation only if they read a file rather than first learning `docker logs`.
2. **`wlpp doctor` runs on the host and inspects the containers**, never requiring the operator to be inside one. Same reasoning.
3. **Two compose profiles, and the distinction is written down.** Development bind-mounts the source so Phase 1–3 is not a rebuild per change; production bakes it into the image. A bind-mounted dev compose file reaching a rig would silently defeat the reproducibility this section exists for.

#### Setup cost to pay before January, not during it

**Fedora with SELinux enforcing plus GPU passthrough is the fiddly part**: NVIDIA Container Toolkit, correct device exposure, and `:z`/`:Z` labels on every bind mount. It is a one-time cost, but it is the step most likely to consume an afternoon — and far better consumed in October than with an animal waiting.

**Explicitly not a concern: scratch I/O.** Bind mounts on Linux are near-native, so Kilosort hammering the NVMe loses nothing to containerisation. (The intuition that says otherwise comes from Docker Desktop on macOS, where it would be real; it does not apply here.)

### 6.7 Unit and channel enrichment

Computed in every canonical run. Cost is a rounding error against the sort — benchmark on synthetic sessions rather than trusting the estimates below.

**Unit-level**

| Enrichment | Notes |
|---|---|
| Waveform metrics | Peak–trough duration, half-width, PT ratio, repolarization and recovery slope, spatial spread, propagation velocity. Templates already extracted for QC, so cost is seconds |
| Burst metrics | Burst index, CV2, local variation (LV), ISI distribution shape. Milliseconds — spike trains only |
| Duplicate / oversplit flags | CCG zero-lag structure over spatially-restricted pairs. **Flag, never auto-merge** — oversplitting is the commonest sorting failure and the easiest to miss |
| Cross-segment and cross-block stability | Per-block presence, amplitude drift, rate drift. Also computed per recording montage (§6.8) |

**No stored cell-type label.** Narrow- versus broad-spiking falls out of the waveform metrics, and a stored class is a second answer free to disagree with the metrics it came from. Store the metrics; derive the class. Same reasoning wl.works applies to derived verdicts throughout.

**Channel and probe-level**

Per-channel RMS noise in AP and LFP bands; bad-channel labels (`good` / `dead` / `noise` / `out`, where `out` is a free brain-surface estimate); **50 Hz** line-noise magnitude (KU Leuven is EU mains); per-channel spectral profile versus depth; saturation and artifact fraction; impedance carried from wl.works' `electrode_reading`.

**Session-level**

Yield (units, good units, units/channel, by area); total drift magnitude; behavioural summary (trials, hit rate, RT distribution, per block); eye quality (tracking-loss %, blink rate, calibration residual); video dropped-frame count. Plus **ELN metadata carried in from wl.works** — probe serials, insertion coordinates, experimenter, subject, recording counts (§11.2).

**Monosynaptic connectivity is explicitly not here.** GLM-per-pair over ~500 units is six figures of fits — hours to days. It becomes an on-demand activation under role B, never part of the canonical run.

### 6.8 The characterization registry

Recording characterization is **not a frozen task contract**. It is a registry of plugins, each declaring what it needs, and the pipeline runs whatever a session's blocks support.

| Block type | Feeds |
|---|---|
| `rf_map` | RF estimation, retinotopic progression (gaze-corrected) |
| `resting_dark` | Spectrolaminar motif, LFP coherence and correlation across depth |
| `passive_flash` | Evoked CSD — reliable in early areas |
| *any task with in-RF stimulus events* | **Task-evoked CSD** — the higher-order-area path |
| `shape_map`, `color_map`, `mgs`, … | Preference maps, response fields, presaccadic activity |

Adding a characterization is a plugin plus a task-type declaration, never a pipeline change.

**A capability report is a required output.** "No depth estimate: no flash block and no in-RF trials" is information, not silence. Every canonical run records which plugins ran, which were skipped, and why.

**One ordering constraint that is a fact rather than a choice:** task-evoked CSD depends on RF maps existing first, because selecting in-RF trials requires knowing the RF.

```
RF estimation → in-RF trial selection → task-evoked CSD → laminar depth
```

DataJoint expresses this natively as computed-depends-on-computed, but it means depth is **not a leaf node**: a session whose RF map fails silently loses its higher-order depth estimate too. That chain appears in the capability report rather than being discovered.

**Everything here is computed per recording montage**, not per session — a montage change invalidates depth, RF and unit identity alike.

### 6.9 Depth and receptive fields — method choices

**Depth: three independent families, all computed, all stored.**

| Family | Method | Needs a stimulus? |
|---|---|---|
| Evoked CSD | Flash or in-RF task stimulus → LFP → CSD → earliest sink | Yes |
| Spectrolaminar | Gamma superficial, alpha-beta deep, crossover marks L4 | No — resting-state |
| Unit-based | MUA depth profile, response latency by depth, waveform duration by depth | Either |

**The spectrolaminar method is contested**, and that drives the design rather than footnoting it. According to PubMed, Mackey et al. 2024 ([DOI](https://doi.org/10.1101/2024.09.18.613490)) tests the "ubiquitous spectrolaminar motif" against A1, belt and V1 laminar data and finds its L4 identification **unreliable** and non-generalising. Committing to one method would bake a contested position into every session the lab ever records. Computing three, storing three, and storing their pairwise agreement means that when the dispute resolves, the archive already holds whichever answer won — the same pattern as the two saccade detectors.

**Use kCSD, not the standard second spatial difference.** Standard CSD assumes a 1D evenly-spaced laminar array; Neuropixels is a staggered multi-column layout at 20 µm row pitch. Averaging columns to force a 1D formulation discards the geometry the probe was bought for. **kCSD** (kernel CSD) handles arbitrary electrode geometry with regularisation and is the natural fit; **iCSD** (`elephant.current_source_density`) is the fallback if kCSD proves awkward. This is the single most consequential methodological choice in the depth pipeline.

**Receptive fields: gaze-corrected, which is the lab's hardware advantage.** Nearly every RF pipeline assumes perfect fixation and maps in screen coordinates. With a dual-Purkinje tracker at 500 Hz (§7.1), RFs are mapped in **retinotopic coordinates corrected sample-by-sample for drift and microsaccades**. For V4-scale RFs that sharpens the estimate; for V1 it is the difference between a receptive field and a blur.

Also: bootstrap significance so units without a real RF are flagged rather than fitted to noise; model fits (2D Gaussian / DoG / Gabor) with parameter CIs rather than a centroid; regularised reverse correlation rather than raw STA; and **retinotopic progression along the probe**, where a smooth progression indicates one area and a discontinuity indicates a crossed boundary — an independent check that feeds wl.works' `functional_mapping` area source.

**Store the profiles; derive the boundaries.** The full CSD map, the power-by-depth matrix and the raw RF response map go into the NWB. The L4 boundary, the layer label and the fitted RF centre are **derived columns**, recomputable from them. This is the same reasoning that forbids a stored cell-type label, and it matters more here: a contested boundary method means the derived answer *will* change, and that must be a recomputation rather than a re-recording.

### 6.10 Provisional until verified

Every characterization is **useful but unverified**, and is marked as such.

- Products land in the canonical NWB as soon as they are computed, each carrying an explicit `provisional` flag and the identity of the method that produced it.
- A human verdict later **upgrades the flag in place** via the accretion path (§8.3).

**The verification loop needs no new machinery** — every piece already exists in wl.works:

1. wl-preproc computes → full detail into the NWB
2. wl-preproc generates a **review figure** — Plan 18's 18b already specifies figure ingest
3. wl.works pulls figure and summary metrics — Plan 20 §5.1's summary-only rule, per-unit detail staying on wl-nas
4. A person asserts a `verdict` / `reason` / `actor`, exactly as Plan 19 §6.2 already has someone read a sort summary and write an area assignment
5. The verdict accretes back into the NWB

**wl-preproc stays headless.** It computes and draws; wl.works renders and records judgment.

**The boundary that keeps this package from becoming an analysis grab-bag**, stated so somebody other than the PI can apply it:

> **Preprocessing characterises the recording. Analysis answers questions about the world.**

A plugin qualifies if its output is useful to *every* project touching that session and is provisional rather than a claim. *Where is this unit looking* characterises the recording; *how attention modulates its RF* does not. Shape and colour preference maps qualify; a cross-area selectivity comparison does not.

---

## 7. Eye and behavior

### 7.1 ohDPI

Two FLIR BFS-U3-16S2M-CS at 500 Hz, running OpenIris with the OpenIrisDPI plugin, plus optional ACCESIO USB-AO16-8A analog output.

**Dual independent sync paths:**

1. **Pi-triggered frames.** A hardware PWM channel emits a jitter-free 500 Hz trigger; the existing primary/secondary cable chains camera 2. Eye frame times exist on the sync-box clock *by construction*, on every session type, independent of OpenIris internals or file format.
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

- Subject and session metadata, plus **ELN metadata carried in from wl.works** — probe serials, insertion coordinates, experimenter, recording counts (§11.2)
- Electrode table with probe geometry (ProbeInterface), per-electrode area, per-recording site selection (§11.6), and channel-level metrics (§6.7)
- Units: spike times in session time, waveforms, QC metrics, waveform/burst metrics, duplicate flags, cross-segment **and cross-block** stability
- LFP and MUA envelope
- Trials table **including per-system coverage columns**, and block membership
- Block table (`TimeIntervals`), with `task_type`, acquisition provenance, and wl.works' `block_behaviour_assertion` / `block_neural_assertion` verdicts as metadata
- Full event table (all codes with times)
- Eye: gaze, pupil, and detection events from *both* detectors
- Behavior video as external file references plus frame times
- Stim events and parameters
- **Characterization products** (§6.8–6.10): CSD maps, power-by-depth matrices, RF response maps, preference maps — the **profiles**, with derived boundaries and fits as separate columns, every one flagged `provisional` until verified
- **The capability report** — which characterizations ran, which were skipped, and why
- **Timing provenance record and tier**

**Blocks use NWB's `TimeIntervals`**, either `nwbfile.epochs` or a dedicated `intervals/blocks` table, so *"which blocks did this unit survive, and was this block any good"* is answerable inside the file without wl.works.

**Written-once versus accreted, stated explicitly** because Plan 24 §3.3's checksum design depends on the boundary rather than discovering it:

| Written at creation | Accreted later |
|---|---|
| Spike times, LFP, MUA, trials, blocks, events, eye, electrode table, QC and waveform metrics | Verified verdicts upgrading `provisional` flags, curation, histology-derived depth, connectivity |

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
| **Canonical** | Automatic, X hours after session data lands | All blocks **within one recording montage** with no `bad` `block_neural_assertion` for that probe | Exactly one current per `(session, montage)` |
| **Derivative** | Requested via wl.works | Any hand-picked subset; may span sessions for a chronic array | Unbounded, additive |

#### The recording montage

A **recording montage** is a maximal interval during which no probe moved. It is the grain at which unit identity is meaningful, and therefore the grain of the canonical NWB.

**Sorting across a montage change produces garbage.** Kilosort's drift model treats a 500 µm advance as drift; it is not. An automatic process must never do it.

wl.works already models the other half: three penetrations in one rig day are **three insertions and one `item_use`** — so a probe move is *not* a new session and *not* a new probe. Within a montage, all probes belong in one NWB, which is what preserves cross-area simultaneity for population and laminar work.

| Case | Canonical NWBs |
|---|---|
| No movement — the common case | 1, identical to a session-grain rule |
| 3 penetrations, both probes moving together | 3, each holding both probes |
| Probes moved independently | 1 per distinct montage boundary |

**Montage boundaries come from wl.works' `item_insertion` rows and nothing else.** Signal-based detection and a strobed movement code were both considered and declined as impractical.

**That leaves one failure mode, and it is closed by refusing to guess.** Those rows are human-entered and may be late; with none present the pipeline would see one undifferentiated session and sort straight across a move, silently. So: **no insertion record → no canonical.** The session is quarantined and reported as "waiting on ELN entry" through the existing tier-D machinery. A silent bad sort becomes a visible blocked one.

The consequence is that the X-hour window must be long enough for **both** block rows and insertion rows to exist — open items 9 and 10 compound into a single dependency on the ELN being current.

> **This corrects a wl.works amendment made earlier in the same session.** The partial unique index committed to Plan 24 §10.4 keys on `(animal_session_id) WHERE role = 'canonical'`, which permits exactly one canonical per session and therefore makes the three-penetration case unrepresentable. It must key on the montage as well (§14 item 10).

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
| wl.works → wl-preproc | Action list request; job request with `(domain, selection, parameters, idempotencyKey)` **plus the metadata bundle below** |
| wl-preproc → wl.works (in response to polls) | Action list; job status including *"already running since 09:02"*; resolved component versions and commit SHAs; result metrics; **review figures**; the capability report; artifact locations as **host + share + relative path** |

**The request payload is the metadata channel, and this is load-bearing.** wl-preproc cannot fetch anything from wl.works — the app binds only to the WireGuard interface and we are on the lab LAN with no route in. So everything this machine needs from the ELN must arrive *with the request*:

```
activation request → { blocks, recording-montage boundaries, probe serials + insertions,
                       experimenter, subject, task types, quality verdicts }
```

One mechanism supplies three things that would otherwise each need their own: the block set, the montage boundaries (so no signal-based detection is required anywhere), and the ELN metadata that makes the NWB self-contained. **The payload schema is therefore a required part of the protocol document**, not an implementation detail.

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
- **Raspberry Pi 5** per rig, plus one for bench PIO development. Not Pi 4 — `pigpio` is Pi-4-only and unmaintained (§4.3).
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
| 16 | kCSD versus iCSD for Neuropixels geometry (§6.9) — decidable on synthetic laminar data before January | Phase 2 |
| 17 | Whether `resting_dark`, `rf_map`, `passive_flash` and the area-specific mapping tasks get **reserved event-code ranges** (§4.2), or are identified only by `task_type`. Reserved ranges make a block self-describing in the recording even if the ELN is wrong | Phase 0 |
| 18 | Whether a montage with **no** usable RF map should still attempt evoked-CSD depth from `passive_flash`, or record no depth at all (§6.8's dependency chain) | Phase 2 |
| 19 | Pose estimation from behavior video — acknowledged as a later integration. The sidecar contract (§4.6) must leave room for keypoint outputs without committing to a toolchain | Post-January |

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
| 10 | Plan 24 §10.4 **(correction to item 2)** | The partial unique index must key on `(animal_session_id, montage_id)`, not `animal_session_id` alone. As first committed it permits one canonical per session, which makes a three-penetration day unrepresentable. **Applied 2026-08-13, `3b49ced`** |
| 11 | Glossary §1 | **`recording montage`** added to the lab-word map — a maximal interval with no probe movement, the grain at which unit identity holds. **Applied 2026-08-13, `3b49ced`** |

**Item 5 was nearly missed, and how it was caught is worth recording.** The first pass amended the two plan specs and stopped. The design spec turned out to enumerate `analysis_activation`'s columns rather than only naming the table, so a column-level change reaches it — found by grepping the identifier across the repository rather than by re-reading the amendment list. That is that repository's own convention diff, and it produced exactly the class of omission its ledger discipline exists to catch.

**Not amended, stated rather than left silent:**

- **`AGENTS.md` and `CHECKPOINT.md` counts.** No table is added, no spec is added, no roadmap row changes status. Every count in both files is about rows, specs and implementation plans, and none of those move. **Recounting was not needed because nothing countable changed** — stated so the next reader does not assume it was skipped.
- **The roadmap table.** No cell's status changes; verified by diff.
- **`brainstorm-queue.md`.** This design ran in another repository, so nothing there opened or closed.
- **Plan 25.** Its §1.2 session-grain archive rule needed no amendment — *this* spec was wrong about the grain and was corrected (§8.4). The error ran the other way.
- **Plan 23.** Its §8 accepts the concurrent-read-during-append risk explicitly. §11.5 here mitigates it on this side without changing anything there, so its acceptance is untouched and simply stops being load-bearing.

**One form is new and is flagged rather than assumed acceptable:** these amendments cite a spec in a *different repository* by path. Every prior cross-reference there is a sibling link that a reader can follow. A cross-repo path cannot be followed and cannot be checked by any tooling in that repository, so it will decay silently — the exact failure mode `AGENTS.md` warns about for citations into `next-session.md`. Whoever plans 11a, 20b or 24b should decide whether the wl.works↔wl-preproc protocol document (§11.2) becomes the shared artifact both repositories cite instead.
