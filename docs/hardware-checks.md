# What waits on hardware

Every check, measurement, threshold or open question in this repository that cannot be settled until real hardware
or real recorded data exists. One list, so that none is missed when the rig, the breakout board, the cameras or the
compute machine arrive. The requester asked for it on 2026-11-21.

**How to use it.**
- **When a piece leaves something for the hardware,** add a row here in the same commit, with its source.
- **When an item is settled,** strike it through, and say how and in which commit. Don't delete it.
- **Sources** are `path:line` at the time the row was written.

**How it was made.** A read-only sweep of the specs, plans, handoffs, the checkpoint, `wl.yaml` and the handover
and amendment files, at `291bbea` (2026-11-21), plus the items of the analog-copy design (amendments 77-83). Each
item was judged open from its text. The code was not run. Items a later document settled were dropped. The last
section lists what the sweep could not judge.

## The synced rig: sync box live, NI/SpikeGLX, Intan

| # | Waits on | The check | What it settles | Source |
|---|---|---|---|---|
| 1 | A few minutes recorded with the sync box live, with NI and Intan | Run barcode decode, rate fit, offset alignment and the whole timing chain on real data. They have only ever run on synthetic fixtures; the lab's one recording has no wl-sync barcodes. | Whether the alignment path works at all: the cheapest large risk reduction before January. | `wl.yaml` `status.next`; `docs/CHECKPOINT.md:471` |
| 2 | The same recording | The rig's frame-gap rate. | Whether gap-aware barcode extraction was worth building. The reference file has no gaps. | `docs/handoffs/2026-09-19-gap-aware-barcode-extraction-built.md:241` |
| 3 | A rig recording with NI, Intan and strobes | One limit on stored fit residuals for every device's fit, barcodes included. It is unset by choice; a real crystal's 12 h rate wander is unmeasured. | A residual gate on publishing. | `docs/handoffs/2026-11-20-part-3b-2.md:58`, `:108` |
| 4 | Rig strobe and code-word streams | The rig's real code-word burst sizes: fixed bursts of 11 or more words make the strobe matcher refuse. Also the cost of refusing a 12 h recording that does not match (422 s on synthetic data). | The strobe matcher's margin rule and its `Unanchored` refusals. | `docs/handoffs/2026-11-20-part-3b-2.md:176`, `:189` |
| 5 | wl-sync's bench day on the Pi 5 box | The latency from a `B` tick to the barcode's lead edge on the wire, which wl-sync then writes as `barcode_delay_us`. | The barcode delay correction. Until then it is recorded as unmeasured and not corrected. | `HANDOVER-wl-sync.md:61`, `:137` |
| 6 | wl-sync's bench acceptance | The `W` tick's offset at capture start, and the PIO timestamp gate: within 100 µs over 10 min, with no dropped words. | Whether the code-word clock model holds. If not, a Pi 4 with `pigpio` is the fallback. The box runs `--fake` until then. | `HANDOVER-wl-sync.md:179`; `docs/superpowers/specs/2026-11-04-syncbox-header-design.md:50` |
| 7 | A real box run lasting hours | wl-sync's fix for unwrapping its 71.6-minute tick wrap (`d309bf1`) holds. | Whether codes after a long pause are timed early by multiples of 71.6 min. | `HANDOVER-wl-sync.md:151` |
| 8 | A real box | `rp1-pio`'s unscaled ticks against SI seconds: wl-sync's figure is up to 50 ppm, its rate unmeasured. | How far the one-count timebase's seconds are from true seconds; the daily report's split-clock section. | `docs/superpowers/specs/2026-11-04-syncbox-header-design.md:191`; `docs/handoffs/2026-11-20-wl-sync-notes-7-8.md` |
| 9 | The rig's SpikeGLX output | The rig's real SpikeGLX layout, which has not been seen (both layouts are read). Also the NHP `~imroTbl` geometry against `ElectrodeConfig`. | The probe census, electrode-table geometry and `SegmentConfig`. | `docs/handoffs/2026-09-30-nwb-probes.md` §3 |
| 10 | A real Intan RHS session | `dcamplifier.dat`'s dtype: the vendor's text and its code disagree. Also how well stim words and `dcamplifier.dat` compress. | The RHS reader's `dcamplifier` scaling; archival. | `docs/superpowers/specs/2026-08-12-wl-preproc-design.md:672` |
| 11 | A real NP1032 (NHP) session | The compression ratio on NHP data (3.59 is from NP1), and a codec comparison on real noise. The synthetic fixture inverts the ranking. | The codec choice (`blosc-zstd` by default; WavPack about 6% better). | `docs/superpowers/specs/2026-08-27-archival-and-compression-design.md:127`, `:168` |
| 12 | Real transfers from the rig | `STALL_AFTER_S` (2 h, a first guess) against real transfer times. Also whether the rig or the transfer tool writes `DONE` per system. | A reporting threshold. | `docs/superpowers/specs/2026-08-15-phase-1c2-ingest-design.md:519` |
| 13 | Real rig data (January) | The reference mode and the size of the double high-pass. Also whether interpolating dead channels helps or harms sorting. | The reader and chain defaults (2b-2). | `docs/superpowers/specs/2026-08-26-phase-2b2-reader-and-chain-design.md:722` |
| 14 | Real stimulation sessions | An artifact-removal method, which depends on stimulation amplitude and geometry. | The artifact-removal paramset's default. | `docs/superpowers/specs/2026-08-12-wl-preproc-design.md:653` |

## The eye tracker's analog copy on the NI (amendments 77-83)

| # | Waits on | The check | What it settles | Source |
|---|---|---|---|---|
| 15 | A real session with both the sync box and the eye tracker's analog copy reaching the NI (needs the breakout board) | The copy's lag behind each frame. | The lag correction. Until then, eye data placed by its copy is published labelled unmeasured. | spec 2026-10-18, amendment 79 |
| 16 | The board, wired | Which NI analog inputs carry the eye signals. Nothing declares it; wl-sync pins only AI15, the audio tap. | Confirms the matcher's search over every input. | amendments 78, 82 |
| 16a | OpenIrisDPI's analog-output settings on the lab's eye-tracker computer | What the ACCESIO DAC actually sends for eye X, Y and pupil: the P1-P4 difference, P1 alone, or OpenIris's own calibrated gaze. | Narrows the matcher's search over candidate sources. | amendment 78a |
| 17 | Real eye traces with their copy | The matcher's constants (window length and step, distinctness ratio, minimum pairs, residual bound), set from synthetic data. | The match's refusals and precision. | amendments 78, 82 |
| 18 | The board | Buffering and any smoothing on the eye channels. | The matcher's sub-frame refinement. | amendment 82 |

## The eye tracker: OpenIrisDPI, calibrated sessions, labelled data

| # | Waits on | The check | What it settles | Source |
|---|---|---|---|---|
| 19 | A calibrated session from the synced rig | Whether Otero-Millan and BMD discriminate on a calibrated recording, the BMD model's fit to monkey data, and hit and false-alarm rates. | Lifts PROVISIONAL from the Otero-Millan and BMD rows: the eye subsystem's highest-value unblock. | `docs/handoffs/2026-09-01-consensus-and-otero-millan-built.md:76`; `docs/handoffs/2026-09-27-bmd-built.md:316` |
| 20 | A calibrated session | The main-sequence defaults in degrees. The reference recording's degrees are a guessed scale. | The main-sequence and vigor defaults. | `docs/handoffs/2026-10-07-main-sequence.md:85` |
| 21 | Real calibrated sessions | The size of the second-order calibration at this lab's eccentricities, and the error-map diagnostic against real residuals per block. | Whether second-order calibration helps. | `docs/superpowers/specs/2026-08-31-second-order-calibration-design.md:288` |
| 22 | Hand-labelled macaque DPI data | Fine-tune U'n'Eye, whose networks were trained on video trackers. | U'n'Eye rows stay PROVISIONAL until then, after January. | `docs/handoffs/2026-10-06-uneye.md:74` |
| 23 | Hand-labelled macaque data | A supervised emission refit for NSLR, which detects nothing below 1°. | A second NSLR paramset. | `docs/handoffs/2026-09-27-nslr-built.md:240` |
| 24 | Hand-labelled blinks or raw eye video, and the rig's first recordings | Validate an adaptive blink pad. Re-measure the 50 ms and 80 ms flank pads, which were measured on the tutorial recording. | The blink pads in the validity mask. The adaptive pad is not built. | `docs/handoffs/2026-10-11-blink-padding.md:41`, `:51` |
| 25 | The lab's OpenIrisDPI computer recording raw eye video | That it records, and where the files land in the session folder. | Blink detection from the image. | `docs/pending-wl-xcon-amendments.md:139` |
| 26 | Rig geometry and real data | The plausible region (the recorded field plus 2°) and `max_speed_deg_s` (1000), both placeholders. | The eye-lost-data limits. | `docs/superpowers/specs/2026-11-06-eye-lost-data-and-region-design.md`; `docs/superpowers/specs/2026-08-31-saccade-detection-design.md:1343` |
| 27 | Real macaque DPI data | Whether fixational drift stays under 2 °/s after a 4 Hz low-pass, and `max_initial_saccade_freq`, which comes from humans watching a movie. | REMoDNaV's defaults. | `docs/superpowers/specs/2026-09-26-remodnav-design.md:795`, `:879` |
| 28 | Real rig data with a drifting eye | Why Otero-Millan found about 40 microsaccades on synthetic drift, against its band on the real recording. | Otero-Millan's behaviour on drift. | `docs/handoffs/2026-09-27-bmd-built.md:305` |
| 29 | wl-xcon's V3 bench | What OpenIris sends on a tracking failure. Its datagram has no blink or validity field. | wl-xcon's online detector, on its side. | `docs/superpowers/specs/2026-11-06-eye-lost-data-and-region-design.md:262` |
| 30 | A session that calibrates in MonkeyLogic | Which transport carries gaze into MonkeyLogic: OpenIrisDPI's UDP pixels, the ACCESIO voltage, or both. | Whether MonkeyLogic's saved calibration is in a space our pixel feature can use. The validation step is correct either way. | `docs/superpowers/specs/2026-08-30-eye-ohdpi-calibration-and-gaze-design.md:564` |

## The behaviour cameras

| # | Waits on | The check | What it settles | Source |
|---|---|---|---|---|
| 31 | The camera package (wl-camacq, registered, no repository yet) | Each video file's start and end on the camera computer's wall clock, with its synchronisation state, in the sidecar. | Telling a camera file that ran after a partial box stopped from a dead barcode cable (amendment 65's camera half). Until then a camera file with no barcode under the NI fails the session. | amendment 83 |
| 32 | The same package | Its real sidecar. Our contract still requires `trigger_source` `syncbox`, but wl-sync voided camera triggers on 2026-08-16: the cameras free-run and record the barcode line. Also `bcam`'s per-frame digital field names, and the optional `digital_line` and `frame_rate_hz` fields, which no one has told the camera project about. | `contracts/sidecar.py`, `extract_bcam` and bcam alignment, which is "specified and unavailable" until both fields are emitted. | amendment 83; `docs/superpowers/specs/2026-08-16-phase-1c4-timebase-design.md:425` |

## wl-xcon live sessions

| # | Waits on | The check | What it settles | Source |
|---|---|---|---|---|
| 33 | A live wl-xcon recording on build C7, with its words and clock files | Lab time (tier L) and the rig record's clock fit on a real session. wl-xcon has not measured the rig's clock. | Part 4. | spec 2026-10-18, `:685`, `:489` |
| 34 | A real wl-xcon session, or its simulated session folder | Measured runs, blocks and trials from the codes. | Runs and trials in the NWB on real data. | `docs/handoffs/2026-10-01-runs-and-trials.md:56` |

## The compute machine and the servers

| # | Waits on | The check | What it settles | Source |
|---|---|---|---|---|
| 35 | The compute machine (P6000, x86_64 Fedora, SELinux, GPU) | Phase 2b: the 2b-0 benchmark, then 2b-1's container seam, then 2b-2 to 2b-8. | Units, LFP and MUA in the canonical NWB. | `wl.yaml` `status.next`; `docs/superpowers/specs/2026-08-23-phase-2b-decomposition-design.md:194` |
| 36 | The compute machine | U'n'Eye's CPU inference time over a 1.18M-sample recording. | Whether U'n'Eye needs the `slow` test marker. | `docs/superpowers/specs/2026-08-31-saccade-detection-design.md:1347` |
| 37 | `serv` at 12 h | The SpikeGLX and Intan stages end to end at 12 h. The per-trial coverage writes, which took 26 min while another session held six cores, against a 10-minute line. | Batching the coverage writes; the stage memory target. | `docs/handoffs/2026-11-14-scaling-long-sessions.md:41`, `:55` |
| 38 | The real scratch device | The archival high-water mark, and whether the server gets the `wavpack-devel` library. | That value; the codec. | `docs/superpowers/specs/2026-08-27-archival-and-compression-design.md:512` |
| 39 | The lab host | Linux's errno 98 on a privileged port, and the 10 s `GET /health` timeout. The health walk holds the process-wide lock, so a slow NAS could make a healthy host look flaky. | The responder's timeout and the poll rate. | `docs/CHECKPOINT.md:2117`; `docs/handoffs/2026-08-16-phase-1c3-rulings.md:168` |

## Procurement and the board

| # | Waits on | The check | What it settles | Source |
|---|---|---|---|---|
| 40 | Ordering | The 12-13 week lead time for the NI PCIe-6343 and PXIe-6353. It was measured for the 6363. The cards were reported ordered on 2026-08-23. | Delivery against January 2027. | `docs/superpowers/specs/2026-08-12-wl-preproc-design.md:1369` |
| 41 | The breakout board, in layout at wl-sync on 2026-11-21 | The board's lead time, and the event-protocol bench test end to end. | Whether the board's schedule is real. Items 15-18 wait on it. | `docs/handoffs/2026-08-13-syncbox-pcb-brief.md:105`, `:117`; wl-sync `wl.yaml` |

## Not judged by the sweep

- **Whether the sync box should trigger the eye tracker's frames** (`docs/CHECKPOINT.md:2102`; the 1c-4 spec `:339`).
  This is probably settled: wl-sync emits no camera trigger since 2026-08-16. Check with wl-sync before striking
  it.
- **The tolerance of the cross-check between the task PC's clock and the sync box's clock at ingest** (parent spec
  §13 item 5, "Blocking Phase 1"). Lab time and tier N may have replaced it.
- **`clock_trusted` and `clock_reason`**: whether anything reads them (`HANDOVER-wl-sync.md`).
- **Whether OpenIrisDPI surfaces Spinnaker's per-frame chunk data** (parent spec §13 item 2, "enhancement only").
