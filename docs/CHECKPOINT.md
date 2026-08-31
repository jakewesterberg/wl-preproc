# Where this build actually is

**Last updated 2026-08-31**, on `spec/second-order-calibration` at `2fca563` (the
second-order calibration branch's tip). `main` is at `a95d73b`; **this branch is not
merged** -- that decision was left to your human partner, the same way the eye branch's
was. Check `git log --oneline -1` against `2fca563`; if it has moved, this file is stale
and the spec wins.

This file went **nine days and three merged subsystems stale** before this update — it still
claimed 688 tests and an unmerged 1c-4. If you are reading it after a gap, distrust the
numbers before you distrust the reasoning.

**The lab starts January 2027.** Everything here is being built before any real data exists,
so that January validates rather than discovers.

---

## The three repositories, and what each owns

| Repo | Visibility | State |
|---|---|---|
| **wl-sync** | **public**, CI green on 3.11/3.13 | Session identity, barcode codec, log format, backend protocol, PIO FIFO decoding. **Task 5b — the PIO program and `piolib` binding — awaits a Pi 5.** |
| **wl-preproc** | private, CI green, **1016 tests, 1 skipped, 1 deselected** (980 on `main`) | Phase 0 contracts, 1a synthetic generator, 1b Intan RHS, 1b2 the RHS header, 1c-1 schemas, 1c-2 ingest watcher, 1c-3 responder — all merged. **1c-4, 1c-5, 2a, 2b-2's front half, archival-and-compression and the eye reader/calibration/gaze are all merged.** Phase 1c is done. |
| **wl-works** | — | The ELN and lab site. **Another worker owns it, including its remote.** Do not push, do not create branches; check `git branch --show-current` before any read. |

**The dependency runs one way only.** `wl-sync` owns everything the sync box produces —
session identity, the barcode codec, the log format — and `wl-preproc` depends on it via a
direct git reference. `wl-sync` must never import from `wl-preproc`. CI enforces this: a
step asserts `wl_sync.session` and `wl_sync.log` do not resolve inside `wl_preproc`.

---

## What is built

**wl-sync** — `wl_sync/{session,barcode,log,backend,service,rp1/pio_capture}.py`. All pure
Python; `pigpio` is gone and `piolib` sits behind an optional extra, so the whole suite runs
with no Pi libraries installed.

**wl-preproc Phase 0** — every frozen interface in spec §3.5 is executable, except the NWB
target layout (Phase 3). Six JSON Schemas are committed under `docs/schemas/`, and CI fails
if a model changes without re-exporting them. Three of those schemas are **published
contracts other projects build against**: `behavior_camera_sidecar.json` for the separate
FLIR project, and `job_request.json` / `health_response.json` for wl.works, whose 18b tests
run against a *fake* wl-preproc.

**wl-preproc Phase 1a** — `wl_preproc/synth/`. `wlpp synth generate --profile ci` writes a
complete session directory that SpikeInterface opens; `--profile benchmark` writes a
realistic 384-channel hour for the P6000 benchmark. **A SpikeGLX run emits two streams** — the
imec AP pair and an NI (`nidq`) pair — and **the barcode is on the NI digital line**, per §4.5,
with the imec SY channel emitted but undriven. That was corrected in 1c-4; see the trap below.

**wl-preproc Phase 1b** — `wl_preproc/synth/{stim,rhs}.py`. `wlpp synth generate --profile
stim` writes a **standalone-Intan** session: no NI, no SpikeGLX, stim planted as ground truth
and rendered into both the stim words and the amplifier artifacts so the two cannot disagree.
Three tick origins now exist (sync box 1.0 s, SpikeGLX 0.7 s, RHS 0.45 s), so a pipeline that
never computes an offset fails.

**wl-preproc Phase 1b2** — `wl_preproc/synth/rhs_header.py`. `info.rhs` was a 20-byte
identification stub that `spikeinterface.read_intan` could not open; it is now a **byte-correct
Standard Intan RHS header**, transcribed from the vendor document and cross-checked field by
field against neo's own tables. `read_intan` opens the emitted sessions, returns the four
channels at 30 kHz, reshapes `amplifier.dat` correctly and reports the declared stim step size
as the Stim stream's gain. **The reader-as-oracle test that verified SpikeGLX now covers RHS
too**, so the claim the Phase 1b plan made — and did not test — is now the thing CI enforces.
The strict xfail that pinned the gap is gone, having done its job.

> A header that parses is not the same as a header that is true, so
> `write_rhs_header` refuses to write one that is not: a reader reshapes `amplifier.dat` by the
> header's **enabled** channel count, and a declared count that differs from `n_ap_channels` —
> or an amplifier channel marked `enabled=False` — silently mis-shapes every sample instead of
> failing. It is checked in the writer rather than the recipe because `model_copy(update=...)`
> does not re-run pydantic validators.

**wl-preproc Phase 1c-1** — `wl_preproc/schema/`. DataJoint 2.3.2 plus four Elements
(lab/animal/session/event), all five git dependencies commit-pinned. Custom tables for sync,
segments, requests and paramsets. A guardrail sweep auto-discovers every schema module and
fails on a bare `longblob`; `wlpp delete` prints a cascade and defaults to a dry run.

**wl-preproc Phase 1c-2** — `wl_preproc/ingest/`. `wlpp ingest --root` scans a storage root
once: a session is complete when a `DONE` marker names every file with a blake3 digest, and
**quiescence is an alarm, never a trigger** — a stalled transfer and a finished one are both
quiet. Device absence never blocks ingest; transfer integrity does. `wlpp report` writes the
daily status file. The watcher deliberately never calls `submit()`: landing the parent rows
*is* the trigger, which is what let it avoid needing a montage it cannot measure.

**wl-preproc Phase 1c-3** — `wl_preproc/responder/`. `wlpp responder --port --root` serves
`GET /health` and `POST /jobs` behind a bearer token from `WLPP_RESPONDER_TOKEN` and one
process-wide lock that is load-bearing for correctness, not only thread safety. Stdlib only —
no web framework. An AST guardrail over the whole package enforces §11.1, that this host never
initiates a connection. **It also ships `docs/ops/lab-host-protocol.md`**, the cross-repo
interface contract wl.works cites from eleven places and neither repository had written:
endpoints, status codes, the token, the verdict values, and the three timing constants their
Plan 10 leaves unfilled. **It is proposed, not agreed** — the token and `408` are real work on
their side.

**wl-preproc Phase 1c-4** — `wl_preproc/timebase/` and `wl_preproc/schema/timebase.py`. Every
system's recordings become session time: one extraction function per system (`EXTRACTORS`, whose
set equality against `SYSTEMS` is the phase's completeness claim), then shared decode, rate fit
per `(system, session)`, offset fit per `(system, segment)`, rejection, coverage and provenance.
**Decode reliability is measured rather than asserted** — the suite prints recovered-versus-emitted
per system on every run, currently 100% on clean fixtures for all five including ohdpi at its
2.5-samples-per-bit margin. This project's **first Computed tables** are declared here, and
`_computed_tables()` stops being empty.

> The generator gained as much as the pipeline did. `--profile eye` and `--profile drift` are
> new; the camera runs at 500 Hz because 200 Hz could not decode a barcode at all; SpikeGLX
> emits an NI stream; `ohdpi` has an emitter for the first time; and **clock drift is per
> system**, since applying it to the sync box too had made it cancel exactly.

---

**wl-preproc archival and compression** — merged 2026-08-27 (`25cd7bf`). `wl_preproc/archive/`.
A session compresses to a Zarr store, verifies the store reconstructs the original bytes,
publishes to the NAS, confirms the copy and stamps a sentinel. A five-condition predicate
decides when the scratch copy may be freed. **`wlpp reclaim` previews and deletes nothing**,
deliberately: rehydration is what makes reclamation safe and it is not built. Ingest now
refuses new sessions below the scratch floor `doctor.py` already owned.

**wl-preproc eye: reader, calibration, gaze** — merged 2026-08-31 (`e7c8ea4`).
`wl_preproc/eye/`. Reads the real OpenIrisDPI format, fits a per-eye map over the
dual-Purkinje vector, exposes canonical gaze as a **computation, never a stored array**.
Records per session how the calibration was obtained — fitted, borrowed from the map in
use online during acquisition, carried forward, or refused with a stated reason.

**wl-preproc second-order calibration** — built 2026-08-31 on
`spec/second-order-calibration`, eight tasks, **not merged**. Replaces that branch's
affine choice with a **model ladder**: second-order first, affine where the geometry
cannot constrain twelve parameters, both `calibration_source = fitted`. Twelve schema
columns named for the basis term each multiplies, plus a `calibration_model` column that
is the authority rather than a derivation. `monkeylogic` becomes `online` everywhere it is
a *role*; `eye/bhv2.py` keeps the vendor name. The protocol gains
`TaskTypeCode.CALIBRATION` and a `CALIBRATION_START`/`END` pair — both mechanisms, ruled
2026-08-31.

Three corrections it forced, each measured rather than argued:
`_conditioning` moved from bare target positions to their **mean-centred basis expansion**
(a ring of 8 constrains an affine perfectly at 1.0000 and a quadratic not at all at
0.0000; dropping the centring would have falsely refused an off-axis grid at 0.0404
against 0.1966); the **synthetic generator could not produce a session reaching the
second-order rung at all** — best quadratic conditioning 0.0739 over 40,000 window
placements — so `SessionRecipe.eye_fixations` now holds the gaze at stated raw positions,
which is what a calibration block is; and `read_ohdpi` **reports dropped-frame gaps
instead of losing the session over one**, with the refusal moved to `extract_ohdpi`, the
one caller whose sample-index-to-time map a gap actually breaks.

**This corrected five wrong assumptions Phase 1c-4 shipped**, which survived because the
fixture generator and the reader agreed with each other by construction: three wrong column
names, a timestamp unit off by 10⁶, a contiguity check requiring a zero start, and a `*.csv`
glob for `.txt` files. `find_recordings` returned nothing on every real session.
`tests/fixtures/ohdpi/OpenIris-sample.txt` is now a committed slice of a genuine recording,
and a test pins the synthetic generator's header to it. 1c-4's spec carries a new §14.


## What is next

**2026-08-31 — what is actually next, and what each needs.**

1. **Second-order calibration — BUILT 2026-08-31**, all eight tasks, on
   `spec/second-order-calibration` (`2fca563`). **Not merged.**
   `specs/2026-08-31-second-order-calibration-design.md`,
   `plans/2026-08-31-second-order-calibration.md`, and
   `handoffs/2026-08-31-second-order-calibration-built-detection-is-next.md` for what it
   changed and what it left open. It superseded the eye spec's §3.3 affine choice:
   OpenIrisDPI's own tutorial notebook shows the P1−P4 nonlinearity is real and a
   second-order term accounts for much of it. **Sequenced before detection deliberately** —
   detection thresholds are tuned against the gaze signal, so tuning against affine gaze
   and then changing the model means validating twice. That ordering held.
2. **Saccade detection** — the second half of parent §7, split from the eye spec deliberately.
   Three detectors, each its own paramset: Engbert–Kliegl (baseline, no dependencies),
   Otero-Millan (threshold-free, per-detection reliability, no PyTorch), and U'n'Eye (a CNN,
   vendored at a pinned commit). **Ruled 2026-08-31: all three, with PyTorch declared
   properly** rather than worked around — `where: serv`, following `kilosort`'s precedent,
   since a CNN detector belongs on the preprocessing server and not a rig. Their agreement
   becomes a three-way data-quality metric.
3. **Rehydration** — decompress-to-scratch. Small, reuses `archive/verify.py`'s existing
   reconstruction, and it is what turns `wlpp reclaim` from a preview into real disk-freeing.
   Worth doing before the hardware lands.
4. **One eye gap needing a human decision**, both recorded in
   `docs/handoffs/2026-08-31-eye-reader-and-calibration-built-detection-is-next.md`: no convention says where a
   `.bhv2` sits in a session directory. (**The calibration-block marker was settled
   2026-08-31**: both a reserved `TaskTypeCode` and a `CALIBRATION_START`/`END` pair, Task 4
   of the second-order plan.) Pairs with implementing §4's `TARGET_POSITION` encoding, which
   the eye spec specifies exactly.

**Everything in Phase 2b proper still needs the compute machine.** The two subsystems merged
this week were chosen precisely because they needed no GPU and no container — and both are now
done, so that gap is open again.

**Phase 2a is merged** (`056ee57`, follow-ups `068c8b0`), so item 1 as this section stood on
2026-08-22 — *"resolve `element-array-ephys` #230 here"* — is **closed, and not the way the brief
expected.** The dependency was **declined** rather than patched: its `Clustering` key cannot carry
`activation_id`, which §5.2 requires, so fixing all fourteen blobs would still have left the branch
unusable. The ephys tables are custom. See
`specs/2026-08-22-phase-2a-ephys-schema-design.md` §2.

1. **Phase 1c-5 — event decoding. It RAN** (built 2026-08-23 on `phase-1c5-events`). This entry
   read *"It runs NEXT"*, decided the same day for a hardware reason as much as a dependency one:
   Phase 2b's first two pieces both need the compute machine, which had not arrived, and 1c-5
   needed none. That ordering held. See item 3 below for what it did — **item 3, not the item 2
   this entry used to point at**, which is Phase 2b — and
   `specs/2026-08-23-phase-2b-decomposition-design.md` §8 item 2 for the argument that put it
   first.
2. **Phase 2b — the ephys branch proper.** Decomposed 2026-08-23 into a spike and eight pieces:
   `specs/2026-08-23-phase-2b-decomposition-design.md`. Two of its rulings are worth knowing before
   reading it: **containers come first**, before any processing stage (§6.6.1's setup cost is
   explicitly one to pay in October); and the **P6000 benchmark is a spike that runs first**, not a
   Phase 2 deliverable that runs last, because its whole purpose is producing a number that
   justifies a card in a budget request.

   **2026-08-26: 2b-2 is designed, and its front half is built.**
   `specs/2026-08-26-phase-2b2-reader-and-chain-design.md` designs the reader seam and the
   reordered preprocessing chain; its own §1 records why the design grew a front half: the
   synthetic generator had no spatial structure at all, so none of Phase 2b's questions were
   measurable against it. Correcting that fixture is now built
   — the generator plants unit identity, multi-channel spike footprints with a real amplitude-decay
   bias, spatially correlated noise, and an LF band. **`wl_preproc/ephys/` is no longer only
   `geometry.py`** — `sorter_geometry.py` now sits beside it, deriving Kilosort's
   `dminx`/`max_channel_distance` from the probe rather than accepting its 32 µm default. The
   decomposition's §0.1 claim that *"no signal reading exists yet"* **still holds**: neither module
   reads a sample of signal, and a reader should not infer otherwise from a second file appearing in
   that directory. **The back half — the reader seam itself, the chain, the Kilosort seam and the
   paramset work (design spec §4, §5, §6, §8) — is unbuilt and waits on 2b-1**, same as the rest of
   Phase 2b.
3. **What 1c-5 is — and it is built.** It decodes the 16-bit event stream, builds the canonical
   trial list, fills per-trial coverage, and **resolves the data-quality tier**.

   **This entry used to describe a wait. Every clause of that wait is now false**, and the
   reversal is recorded rather than quietly edited. It read: *"`TimingProvenance.tier` holds
   `'pending'` for every session, and `pending_inputs` names the three things it waits for —
   `event_code_agreement`, `trial_count_agreement`, `camera_trigger_count`. Tiers A/B/C are
   unreachable until they exist."* The tier now resolves to **A, B, C or D on every session**;
   `'pending'` is **retired from the enum** rather than merely left unwritten, because a stored
   value no code path can produce is worse than no value at all. `pending_inputs` is `''` on every
   row. All three inputs are measured in `TimingProvenance.make()` — the Pi's word stream against
   the NI's, trial counts decoded from the codes against the task file, and the behaviour-camera
   sidecar — so A/B/C are reachable, and each input is stored in its own column so §4.7's
   *"derived, not asserted"* holds on the row itself.

   **The same entry also put the measured block boundary in the wrong table, and that clause was
   wrong before this phase rather than made wrong by it.** It read *"in its own Computed table"*.
   There is no such table: `schema/events.py` declares none at all — it is a module of functions —
   and `populate_session` inserts the measured boundary into element-event's
   `pipeline.trial.Block`, cross-validated against wl.works' asserted `core.Block`, where a
   disagreement is its own tier-D condition. Spec §5's adoption table assigns *"Events, trials,
   blocks"* to `element-event`; the entry contradicted it.

   > **This entry used to say nothing downstream was blocked by it. That is now false**, and the
   > reversal is recorded rather than quietly edited. Phase 2b's §6.8 characterization registry
   > keys on block type (`rf_map`, `resting_dark`, `passive_flash`, *"any task with in-RF stimulus
   > events"*), and §6.9's chain runs `RF estimation → in-RF trial selection → task-evoked CSD →
   > laminar depth`. Both need the canonical trial list and decoded event codes. **1c-5 is a
   > prerequisite for 2b-7 and 2b-8** — see the decomposition's §4. It still does **not** block
   > 2b-0 through 2b-6, so it may be built in parallel with them.

   *Transcription fix, 2026-08-23, not a change of position: the last sentence of the blockquote
   above had lost its "not" and so read "It still blocks 2b-0 through 2b-6, so it may be built in
   parallel with them", which contradicts itself — a phase that blocks those pieces cannot be built
   alongside them. Restored against the decomposition's §4, which reads "1c-5 does not block 2b-0
   through 2b-6." Corrected in place rather than recorded-and-left, because nothing was ever
   believed here that the correction reverses.*

   > **Satisfied 2026-08-23**, by the phase above being built. The blockquote is kept rather than
   > deleted because the dependency it records is *why* 1c-5 ran first; nothing downstream waits on
   > it now — 2b-7 and 2b-8 have their canonical trial list and decoded event codes.
4. **Phase 3 onward** — see spec §12. Phase 2's window is Oct–Nov 2026; Phase 1's was Sep–Oct and
   finished 2026-08-22, about six weeks early.

**Phase 1c-5 is merged to `main`** (2026-08-23, `5d81ef8..03db020`, 38 commits, 765 tests). With it
done, **every remaining piece of Phase 2b is blocked on the compute machine** — 2b-0 needs the
P6000 and 2b-1 needs x86_64 with SELinux and GPU passthrough, and §5 forces 2b-1 before everything
else. 1c-5 was the piece chosen to be productive while the box was in transit, so that gap is open
again. What is still unblocked, and why, is in
`handoffs/2026-08-23-next-session-phase-2b-is-hardware-blocked.md`.

**The NI cards are ordered** (2026-08-23), closing the item that stood here as the longest-lead
purchase on §12's list. **The compute machine is bought or on order** as of the same date, which is
what makes the P6000 spike runnable — it gates nothing else in Phase 2b.

---

## Decisions that are settled and should not be relitigated

Each of these was argued and is recorded with its reasoning in the spec. Reopening one is a
defensible call, but it is a reversal rather than a gap.

- **Sync box: Raspberry Pi 5 with RP1 PIO, running Raspberry Pi OS.** The distribution is
  part of the constraint — mainline RP1 support lags and PIO is the piece this design
  depends on entirely. §4.3.
- **Server: Fedora.** Independent of the above; they are different machines. §4.3.
- **One container per stage**, built at deploy and never per run; the image digest is the
  provenance identity. §6.6.1.
- **Canonical NWB is per `(session, recording montage)`**, not per session. §8.3.
- **All continuous data at 500 Hz**; anything whose value is its *timing* is stored as event
  times instead. §8.1.1.
- **16-bit event codes** to the sync box and NI, **strobe only** to Intan RHS. §4.2.
- **Transport is pull-only.** wl.works opens every connection; everything this machine needs
  from the ELN arrives in the request payload. §11. **As of 1c-3 this is enforced by a test over
  the whole package, not just intended** — and it is a tripwire against a convenient callback
  added months from now, not a sandbox. Its documented limits are in the guardrail's own
  docstring.
- **The responder is stdlib-only.** No web framework, no new runtime dependency — the whole
  HTTP surface is `http.server` plus `hmac`. Argued in the 1c-3 design spec.
- **The barcode reaches SpikeGLX on an NI digital line, not the imec SY channel**, leaving the
  imec SMA free. §4.5, and it is why §12 orders a card with 32 hardware-timed Port 0 lines. The
  1a fixture contradicted this until 1c-4; the fixture was corrected, not the spec.
- **Every device's clock drifts against session time; the sync box's does not**, because session
  time *is* its timeline. §4.5. `SessionRecipe.system_drift_ppm` is per system for that reason.
- **Rate is read from each recording's own metadata, never from a constant** — `.nidq.meta`,
  `info.rhs`, the ohdpi file's own timestamps, the sidecar's `frame_rate_hz`. Every fixture in
  the repo samples at 30 kHz or 500 Hz, so a hardcoded rate is indistinguishable from a read one
  unless a test supplies a rate that is neither. Each of the four has such a test.

---

## Traps that cost real time, recorded so they are paid for once

- **A double quote on a definition's FIRST comment line breaks table declaration.**
  DataJoint emits only that first `#` line as the table's SQL `COMMENT`, wrapped in
  **unescaped double quotes**, and drops every later comment line
  (`declare.py`: `table_comment = definition.pop(0)[1:].strip()`). So a `"` there closes
  the string early — `COMMENT "… -- not "the configuration of a"` → 42000, *syntax error
  near 'the configuration of a"'*. Later comment lines are harmless, which is exactly why
  `core.RejectedSegment` and `request.Activation` carry double quotes and declare fine.
  **Found the hard way in Phase 2a**, where the reviewer's first instinct — "those two
  tables prove the diagnosis is wrong" — was itself wrong, and only reverting the
  character reproduced it. Two consequences beyond the trap: the `Key: (...)` line usually
  **never reaches the database at all**, and `test_every_table_documents_its_key_in_schema`
  only asserts `startswith("#")` — so that convention is enforced by neither the test nor
  the DDL, only by review.
- **The hand-listed-module shape bit a THIRD time, in a file no plan mentioned.**
  `ingest` (1c-2) and `timebase` (1c-4) are recorded below. Phase 2a added `ephys` as a
  **seventh** schema module and missed `wl_preproc/daemon.py`'s `_PROJECT_SCHEMA_MODULES`
  tuple — a list the phase spec never named, while that same spec claimed vendoring
  "retires that risk". **What saved it was the guard written after the second bite**:
  `test_every_schema_module_is_swept_for_job_tables` compares the hand-list against
  `pkgutil` discovery, and `daemon.py`'s own comment predicted the rescue verbatim — *"a
  seventh module fails the suite rather than being silently skipped."* The lesson is not
  "remember the list" — that has now failed three times. It is that **every hand-written
  list of modules needs a discovering test beside it**, and that a claim of "this shape is
  retired" must name the files it was checked against.

- **An upstream Element can be right about types and wrong about keys, and the key is the one
  that cannot be shimmed.** `element-array-ephys` was kept out for its 14 bare `longblob`s
  (below), and that was the whole recorded reason for eight months. The decisive defect was
  elsewhere: its `Clustering` is keyed `(subject, session_datetime, insertion_number,
  paramset_idx)`, with `EphysRecording` one row per (session, insertion) and **nowhere to put
  `activation_id`** — which §5.2 requires and states in bold. Two derivative activations over
  different block sets would have collided on one primary key. **A blob defect is a definition
  change; a key defect is a rewrite**, and the project spent eight months tracking the cheaper
  one. **When evaluating an upstream schema, diff the primary keys before the column types.**
  §5.1.1, and `specs/2026-08-22-phase-2a-ephys-schema-design.md` §2.3.
- **A dependency's own `install_requires` can re-import a moving git ref you already closed.**
  `pyproject.toml`'s five pins were converted from branches to commit SHAs on 2026-08-14 for
  exactly this reason. Adding `element-array-ephys` would have brought **four unpinned git refs
  back transitively** — `spikeinterface`, `element-interface`, `neo`, `probeinterface` — and,
  worse, **silently satisfied this repo's own pinned `spikeinterface>=0.101` with SpikeInterface
  `main`**, replacing the format oracle that `tests/synth/` validates both emitters against, with
  no error. Measured with `uv pip compile`: +104 packages onto 66. **A pin you control says
  nothing about what your dependencies pin**; resolve the lock, do not read the pin list.

- **A bare `longblob` silently destroys array data under DataJoint 2.x.** 2.x declares it as a
  raw binary column rather than a DataJoint blob, so a numpy array is stored as its *string
  repr* — with the middle elided by numpy above ~1000 elements — and **nothing raises on insert
  or on fetch**. Measured: a 384 × 82 float32 waveform set, 31,488 values, became 488 bytes and
  is unrecoverable. Declare **`<blob>`** instead. The fix is definition-only and needs no table
  migration **only while no row has yet been written under 2.x** — so it must cover this repo's
  own custom tables, not just the Elements', and it must land before January. §5.1.1.
- **PyTorch must come from the `cu126` index.** cu128 dropped Pascal at 2.7, and PyPI's
  default moved to CUDA 13 at 2.11 — so a plain `pip install torch` silently yields a build
  that cannot run on the P6000. §6.6.
- **Intan numbers bits from 1.** "Bit 16 (the MSB)" is bit 15 zero-based; reading the
  document literally keys the artifact blanking mask to charge recovery instead of amplifier
  settle, silently. §6.3.
- **MonkeyLogic's strobe `T1` is the pulse, not a setup interval before it**, and the latch
  is `T1`'s far edge. §4.2 originally specified "data stable ≥0.5 ms before and after a ≥1 ms
  strobe," which is a shape ML does not implement, so the requirement could not be
  transcribed into the software that emits it. Same class as the Intan trap: right about the
  intent, wrong about the mechanism. §4.2.1.
- **`eventmarker()` blocks the task loop for `T1 + T2`.** Not jitter — a hard stall, on a
  1 kHz software-timed system, and §4.2's "jitter is measured rather than inherited" does not
  cover it. A multi-word trial start costs ≈2.25 ms and must stay out of timing-critical
  epochs. §4.2.1.
- **`requires-python <3.12` was invented** and propagated by being cited rather than
  re-derived. There is no Python constraint from Pascal. §6.6.
- **Barcode alignment is 2.0 s / 3.0 s, not 2.2 s.** The decoder requires a preceding idle,
  which adds one inter-frame interval to every bound. §4.1. **This one bit a second time**, in
  Phase 1b: the plan set the RHS pre-roll to 0.35 s, under `IDLE_MIN_US` of 0.4 s, so the
  first barcode silently failed to decode (11 of 12 recovered). **Any emitter's pre-roll must
  exceed 400 ms.** When a rule has cost you twice, suspect the next new emitter too.
- **A strobe as wide as the word spacing is not a strobe.** Phase 1b wrote a 1 ms pulse at
  1 ms `CODE_WORD_SPACING_S`, so consecutive strobes were *contiguous* — they merged into one
  long high with no falling edge between, and 31 code words rendered as 5 countable edges.
  The tier-B "independent strobe witness" (§4.7) silently stopped witnessing. **The pulse must
  be strictly narrower than the spacing**; it is now 500 µs, matching §4.2.1's `T1`.
- **§4.2's old strobe requirement was arithmetically impossible**, and nothing caught it for
  months because no code rendered a strobe. It demanded ≥1 ms strobe plus 0.5 ms each side —
  2 ms per word — while Phase 1a spaced words 1 ms apart. Two independent investigations, one
  from the emitter (MonkeyLogic, §4.2.1) and one from the receiver (the RHS fixture), landed
  on the same 500 µs. **A spec number no code has executed yet is a hypothesis.**
- **A dependency that imports is not a dependency that works, and the first error hides the
  rest.** Probing the Elements on DataJoint 2.x, the first failure (`dj.schema` removed) masked
  everything behind it, and a one-line shim made all seven modules import — which looked like
  the answer and was not. Behind it sat a dropped attribute type, then generated-SQL failures,
  then the silent blob corruption above, which **activation tests cannot see at all** because
  the tables declare perfectly and only lose data once written to. **Peel every layer before
  concluding, and for a data store, test a round-trip rather than a declaration.**
- **A plan can assert a capability that none of its tasks tests.** Phase 1b's plan justified
  its whole file layout on "SpikeInterface reads them" and specified no test for it; the
  emitted fixtures cannot in fact be opened by `read_intan`. Every task passed its own review,
  because the claim lived in the Architecture paragraph and in no task's diff. **When a plan
  argues from a capability, check that some task actually exercises it.**
- **"Well under 1 ppm" is a claim about a session, and a 15 s fixture cannot hold it.** §4.5 says
  a full session fits rate to well under 1 ppm, and Phase 1c-4's plan turned that into a flat
  `abs=1.0` assertion on a short fixture. It is not achievable there and never was: a sampled edge
  is known only to within one sample period, so a slope across a span `T` cannot beat
  **one sample period / T** — 2.4 ppm at 30 kHz over 14 s, and **143 ppm at 500 Hz**. Measured: at
  a realistic 47 ppm the ohdpi fixture fitted **0.000 ppm with a zero residual**, because every
  barcode landed in the frame it would have occupied with no drift at all; and RHS missed its
  planted drift by exactly one 30 kHz sample per second, which is a staircase rather than noise
  and so does not average down with barcode count. Tolerances are now derived from
  `sample_period / span` per system, and the two camera fixtures carry deliberately unrealistic
  drift magnitudes — the honest alternative to a camera test that cannot fail. **A tolerance
  copied from a spec sentence is the same hypothesis as a timing number no code has executed.**
- **Drift applied to the reference cancels exactly, and every fixture had it.** `session.py` passed
  `recipe.drift_ppm` to *every* emitter including the sync box — but session time **is** the sync
  box's timeline, so drifting it alongside the devices left zero relative drift for the rate fit to
  find. It was invisible for three phases because all four shipped recipes left the value at `0.0`,
  where the bug and the correct behaviour are identical. **A knob every fixture leaves at its
  default is not a tested knob**; the `drift` profile now exercises it, per system.
- **A fixture no reader has consumed is a hypothesis, exactly like a spec number no code has
  executed.** §4.5 says the barcode reaches SpikeGLX on **one NI digital line**, leaving the imec
  SMA free — and §12 orders the PXIe-6353 for the 32 Port 0 lines that requires. Phase 1a's
  generator put the barcode on the **imec SY channel** instead, with a passing test whose
  docstring asserted that layout was how the pipeline aligns. It survived from 1a to 1c-4
  because **nothing extracted a SpikeGLX barcode in between**: the fixture was written, read by
  `read_spikeglx` for *format* validity, and never once consumed for the thing it existed to
  carry. 1c-4's own plan then wrote a test globbing `*.nidq.bin` — correct against the spec,
  matching no file on disk. **A fixture is only pinned by a consumer that needs its content,
  and "the reader opens it" is not that consumer.**
- **neo cannot parse a header out of `io.BytesIO`.** `read_variable_header` reads every
  non-QString field with `np.fromfile`, which needs a real OS file descriptor, so a BytesIO
  raises `UnsupportedOperation: fileno` on the very first field — before a byte of header
  content is examined. Phase 1b2's plan specified four tests that way and every one of them
  failed identically, which reads exactly like a broken header when the header is fine. **Any
  test that parses an Intan header must hand neo an open file**, so round-trip through
  `tmp_path` rather than staying in memory.
- **Check `git log main..<branch>` before merging, and read it.** Not doing so once
  fast-forwarded twenty unrelated commits of an unmerged feature onto `wl-works` `main`.
- **`in_transaction` is not a read-only check.** DataJoint's `insert()` calls
  `connection.query()` directly and never touches the transaction machinery, so
  `in_transaction is False` is equally true of a writing function and a reading one. **This has
  now misled five separate investigations.** To prove a function does not write, snapshot rows —
  `tests/conftest.py` has session-scoped `table_snapshot` and `deep_equal`.
- **A rule derived correctly, then applied one case too narrowly.** Phase 1c-3's
  outbound-connection guardrail took three fix rounds because each closed the escape that had
  been demonstrated and left the next one live. The reason round 2 missed round 3 was written in
  round 2's own docstring: it stated the correct rule, then narrowed the consequence to one AST
  node type. It converged only when *what is forbidden* changed shape rather than *which nodes
  are visited*. **When a fix is the third of its kind, the defect is the rule's shape, not its
  coverage.**
- **Fixing code without sweeping the document that describes it.** Three times in one phase, a
  status code, exit code or message changed and `docs/ops/lab-host-protocol.md` kept describing
  the old one — once because a table was rewritten *for accuracy* and a one-line fix invalidated
  it a commit later. Anything that changes what a client or an operator sees needs that document
  opened in the same commit. And **re-derive a whole table rather than patching three of its
  rows**: that caught a further defect three separate times.
- **A test that reds by timeout is not a test.** A hang burns a CI job's whole budget instead of
  failing one case, and it was twice reported as a pass because a harness timed out. Every
  subprocess helper needs an explicit `timeout=`.
- **A broad outer guard can subsume the only failure a narrow-guard test detects**, leaving the
  suite green with the narrow guard deleted. Anywhere one is added, re-mutation-test the guards
  it now covers.
- **A DataJoint `make()` that inserts nothing counts as a SUCCESS and leaves its key
  outstanding.** So a stage that legitimately has nothing to write re-runs on **every daemon
  pass, forever**, doing its full I/O each time and reporting keys "populated". Measured in 1c-4:
  `success_count` stayed at 2 across four consecutive passes with no row ever appearing, and the
  work being redone was extracting and decoding every recording of the affected systems. **Every
  `make()` must insert at least one row on every key its `key_source` yields** — which usually
  means a status column recording that the thing was attempted and could not be done, not an
  absent row. An absent row also cannot distinguish "checked, failed" from "not reached yet".
- **NULL, not zero, for a measurement that was never made.** A stored drift of `0.0` ppm with a
  `0.0` µs residual is exactly what a *flawless* fit looks like, so a system that was never
  fitted reads as the best-aligned one in the session. Same shape as the tier: an input treated
  as passing because it is absent is a false claim, not a default.
- **A knob every fixture leaves at its default is not a tested knob.** `drift_ppm` was applied to
  every emitter *including the sync box* — which **defines** session time, so it cancelled
  exactly and every fixture had zero relative drift. Three phases missed it because all four
  recipes left the value at `0.0`, where the bug and the correct behaviour are identical.

> The full defect lists — twelve tests that passed while proving nothing, seventeen instances of
> prose asserting what the code does not support — are in
> `docs/handoffs/2026-08-15-phase-1c2-rulings.md` and
> `docs/handoffs/2026-08-16-phase-1c3-rulings.md`, with the rulings that produced them.
> **The habit that finds all of them: mutate, don't read.**

---

## Open items

Spec §13 carries the full list. None now gate other work — these are named because each
leaves something behind that does:

- **Item 4 is closed (2026-08-13).** MonkeyLogic declares behavioral-code lines multiline
  with no cap, so **the 16-bit protocol was never at risk** and no frozen contract moved.
  What it left behind is a *procurement clock*: the task-PC board is an **NI PCIe-6343** (§12,
  corrected 2026-08-16 from the 6363), whose lead time is the least slack of any purchase on that
  list against January and wants re-confirming for the corrected model. The 6321/6323 went end-of-life 31 Dec 2024 — and the 6323 is the board
  NIMH's own documentation uses in its examples, so it is easy to inherit by accident.
- **Items 9 and 10 are closed (2026-08-13), and together they hand Phase 1c a requirement.**
  Block rows are created by the session planner in wl.works; wl-preproc cross-validates its
  decoded boundaries and **never writes**. An absent row quarantines and reports, carrying the
  decoded boundaries so the person entering the row has the machine's work in front of them.
  The canonical delay is **12 hours** — the tight end, buying morning availability and paying
  in regeneration. §8.3.1, argued in `docs/handoffs/2026-08-13-open-item-9-block-rows.md`.
  **The requirement: at 12 h the ELN will often not be current, so quarantine is ordinary and
  the activation must re-fire automatically once the missing rows land.** At 48 h that could
  have been a manual nudge; at 12 h it cannot. Whatever implements the canonical trigger must
  treat "waiting on ELN" as retryable with no human step.
- **NEW, found 2026-08-23 while wiring Phase 1c-5's daemon stage: an errored key is never
  retried, and that collides with the requirement two items above.** Measured against
  DataJoint 2.3.2 on a live probe — three consecutive `run_once` passes gave **2 errors, then
  0, then 0**, with `make()` called only on the first. `_populate_distributed` draws solely
  from `jobs.pending`, and `Job.refresh()` re-pends *completed* jobs but not errored ones (its
  step 3 only deletes rows whose key has left `key_source`). Because `run_once` passes
  `suppress_errors=True`, **one transient failure parks that session permanently** and the
  daily report names it once, never again.
  This is not merely an operational nuisance. Items 9 and 10 above require that a session
  quarantined *"waiting on ELN"* **re-fire automatically with no human step** — and at the
  canonical 12-hour delay that quarantine is ordinary, not exceptional. If waiting on ELN ever
  surfaces as a populate error rather than as an empty `key_source`, the retry that requirement
  depends on will not happen. Deciding the retry policy — what is transient, how many attempts,
  what backoff — is a design question, so it is recorded here rather than patched into 1c-5.
  Outside `reap_stale_jobs`' contract: that function was audited in the same pass and **is**
  correct, since a `make()` that raises ends at `status='error'` and is never left reserved.
- **Item 12 narrowed.** No machine creates a block, so the actor question is now only about
  the canonical activation row itself.
- **One question is out with the wl.works owner**, and nothing blocks on the answer: can the
  block-entry UI display a pipeline-supplied boundary proposal? If no, the rule above still
  holds and only the decision aid is lost.
- **The montage precondition dissolved in 1c-3, and the requirement above is what survives it.**
  `MetadataBundle` carries `blocks` and `montage_boundaries` **inbound with every request**, so
  neither the watcher nor the responder needs a montage source it cannot have. **The residual is
  exactly the automatic canonical trigger** — nothing yet re-fires an activation when the missing
  ELN rows land, and at a 12-hour canonical delay that cannot be a manual nudge.
- **1c-4 leaves four things recorded rather than closed.**
  - **The FLIR project has not been told.** `behavior_camera_sidecar.json` gained `digital_line`
    and `frame_rate_hz`, both **optional**, so nothing they emit today breaks — but a proposal
    nobody has received is not an agreement, and until they emit both fields `bcam` alignment is
    *specified and unavailable*. `extract_bcam` refuses such a sidecar by name rather than
    falling back on the generator's `CAMERA_FPS`, which is the fixture's camera and not theirs.
  - **`time_source` is `'barcode'` on every row**, because nothing is trigger-timed yet. Whether
    the sync box can also trigger `ohdpi`'s frames is the open hardware question (1c-4 design
    §12.2); answering it *yes* removes the 2 ms edge quantisation that is the dominant error term
    for both camera systems.
  - **A system whose files are all rejected is re-scanned on every daemon pass.** Deliberate —
    it is what lets a corrected or re-transferred file be picked up with no manual step — but it
    is unbounded rework on a permanently broken device, and worth revisiting if the daemon loop
    is ever run at a fast cadence.
  - **`RejectedSegment` still has no correction path**, inheriting the gap 1c-2 recorded for a
    landed `Ingestion` row. Nothing consumes a re-align command.
- **1c-3 left three things recorded rather than closed**, all in
  `docs/handoffs/2026-08-16-phase-1c3-rulings.md`: `GET /health` holds the process-wide lock
  while walking each *incomplete* session's directory tree, and `POST /jobs` contends for it —
  so the proposed 10 s health timeout is exposed to that walk and should be **re-derived on real
  hardware before anyone raises the poll rate**; there is **no job-status endpoint**, so nothing
  can ask what became of an activation; and the **privileged-port errno in the protocol document
  is unmeasured** — this development machine bound port 81 without complaint, so Linux's errno 98
  is stated as a claim about Linux and wants confirming once on the lab host.

---

## Working notes

- Both `wl-sync` and `wl-preproc` use `uv` with a `.venv`; develop against **3.11**, the
  floor, since CI also tests 3.13.
- **The `.pth` trap is one root cause, and it was diagnosed on 2026-08-16 after two workers had
  worked around its symptoms separately.** Every `.pth` in
  `.venv/lib/python3.11/site-packages/` carries macOS's BSD **`UF_HIDDEN`** flag, and CPython
  3.11+'s `site.addpackage()` has an explicit `if st.st_flags & stat.UF_HIDDEN: return` — so
  **no `.pth` executes at all**, not the editable install's finder and not `_virtualenv.pth`.
  `sys.meta_path` holds only the three built-ins.
  - **`ls -la` does not show it. Only `ls -lO` prints `hidden`.**
  - Fix: `chflags nohidden .venv/lib/python3.11/site-packages/*.pth`. **A reinstall does not
    help** — it writes new files the same sync pass re-hides, which is what made this look like
    a broken editable install. It recurred within minutes, twice, on the day it was found. The
    durable fix is excluding `.venv` from whatever syncs `~/Documents/GitHub`; the tell is the
    duplicate `__editable__… 2.pth` collision artifacts.
  - **It hides itself well**: `python -m pytest` from the repo root is immune, because
    `sys.path[0]` is the repo. So the suite stays green while the shipped `wlpp` console script
    is broken, and any CLI test that shells out via `PYTHONPATH=<repo> python -m
    wl_preproc.cli.main` cannot see it either. **Anything measuring the CLI as an operator will
    use it must invoke the real entry point.**
  - This supersedes two earlier notes that recorded the symptoms as separate facts: that
    `.venv/bin/pytest` "does not put the package on `sys.path`" (it does — the `.pth` was
    hidden), and that `wl-sync` had to be installed non-editable because "an editable install's
    `.pth` did not execute" (same cause).
- `wl-preproc` depends on `wl-sync` by **pinned git SHA** in `pyproject.toml`, not a local path
  — one of the five commit pins CI enforces. Installed non-editably as a real package directory.
- **Run the suite as `.venv/bin/python -m pytest`, from the repo root**, which is immune to the
  above regardless of the flag's state.
- **Twelve amendments to the wl.works corpus.** Eleven are in spec §14's table, all applied. The
  twelfth is §14.1, raised while designing 1c-2 and **applied to their `main` locally on
  2026-08-15** — it narrowed their open item rather than closing it, because whether they accept
  the split is theirs to answer. The reasoning for each lives in that repo's own specs as dated
  amendment blocks.
  - **§14.1 is now stale in our favour and wants a decision.** It argues that
    `animal_session_block` and the montage boundaries are *"a blocking precondition for every
    automatic activation"*. Phase 1c-3 dissolved that: `MetadataBundle` carries both **inbound
    with every request**, so nothing here needs to fetch them. What survives is only the
    automatic canonical trigger. Whether to amend §14.1 — and whether to tell wl.works their
    half of the precondition is gone — has not been decided.
